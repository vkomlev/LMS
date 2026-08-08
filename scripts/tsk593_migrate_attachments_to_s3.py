"""tsk-593: перенос файлов вложений с диска приложения в объектное хранилище.

Переносит три пространства — вложения ответов (`uploads/attempts`), файлы
переписки (`uploads/messages`) и чеки об оплате (`uploads/receipts`).

**Сверка поштучная, а не «столько же файлов».** Совпадение количества ничего не
доказывает: перенести 46 файлов и испортить один — это тоже «46 из 46». Каждый
файл после записи скачивается обратно из хранилища и сверяется:

* по СОДЕРЖИМОМУ — sha256 скачанного против sha256 исходного, байт в байт;
* по ТИПУ — что хранилище отдаёт не `binary/octet-stream` для картинки. Это
  урок tsk-536: файлы уезжали с общим типом, код ответа был 200, а картинка у
  человека не рисовалась. «Ответ 200» не является доказательством переноса.

**Идемпотентность.** Файл, который уже лежит в хранилище с тем же содержимым,
пропускается. Повторный прогон безопасен.

**Исходники не удаляются.** Диск остаётся как есть: он же — запасной путь
чтения в коде. Убирать его — отдельное решение и отдельный день.

Запуск (на сервере, под пользователем app):

    sudo -u app bash -lc 'cd /opt/lms && set -a && . ./.env && set +a && \\
        venv/bin/python scripts/tsk593_migrate_attachments_to_s3.py --dry-run'
    sudo -u app bash -lc '... venv/bin/python scripts/tsk593_migrate_attachments_to_s3.py --apply'
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from app.services import attachment_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tsk593.migrate")

#: Типы, которые считаются «общими»: если файл с известным расширением уезжает
#: с таким типом, проверка это фиксирует как расхождение (урок tsk-536).
_GENERIC_TYPES = {"binary/octet-stream", "application/octet-stream", ""}


def _sha256_file(path: Path) -> str:
    """sha256 файла на диске, потоком."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


async def _download(space: str, name: str) -> Optional[tuple[bytes, str]]:
    """Скачать файл ИЗ БАКЕТА (без отката на диск): `(содержимое, тип)` или None.

    Именно из бакета, а не через обычное чтение. Обычное чтение откатывается на
    диск, а исходники при переносе никуда не деваются — и проверка «уже
    перенесено» отвечала бы «да» на файл, которого в бакете нет. Первый прогон
    так и отрапортовал 46 «OK» при нуле объектов в бакете.
    """
    return await attachment_storage.read_from_bucket(space, name)


async def _verify(space: str, name: str, source: Path, expected_sha: str) -> Dict[str, object]:
    """Сверить один перенесённый файл. Возвращает запись отчёта."""
    record: Dict[str, object] = {
        "space": space,
        "name": name,
        "size_bytes": source.stat().st_size,
        "sha256": expected_sha,
    }

    downloaded = await _download(space, name)
    if downloaded is None:
        record["status"] = "FAIL"
        record["reason"] = "файла нет в хранилище после записи"
        return record

    data, media_type = downloaded
    actual_sha = hashlib.sha256(data).hexdigest()
    record["content_type"] = media_type
    record["downloaded_bytes"] = len(data)

    if actual_sha != expected_sha:
        record["status"] = "FAIL"
        record["reason"] = f"содержимое разошлось: получено sha256={actual_sha}"
        return record

    expected_type = attachment_storage.guess_content_type(name)
    if expected_type not in _GENERIC_TYPES and media_type != expected_type:
        # Не роняем перенос: файл целый. Но помечаем — по такому типу браузер
        # откажется рисовать картинку, и это надо увидеть, а не проглядеть.
        record["status"] = "WARN"
        record["reason"] = f"тип содержимого {media_type!r}, ожидался {expected_type!r}"
        return record

    record["status"] = "OK"
    return record


async def migrate_space(space: str, *, apply: bool) -> List[Dict[str, object]]:
    """Перенести одно пространство. Возвращает поштучный отчёт."""
    directory = attachment_storage.local_dir(space)
    records: List[Dict[str, object]] = []

    if not directory.exists():
        logger.info("пространство %s: каталога %s нет — переносить нечего", space, directory)
        return records

    files = sorted(p for p in directory.iterdir() if p.is_file())
    logger.info("пространство %s: файлов на диске %s", space, len(files))

    for source in files:
        name = source.name
        expected_sha = _sha256_file(source)

        if not apply:
            records.append({
                "space": space, "name": name, "sha256": expected_sha,
                "size_bytes": source.stat().st_size, "status": "DRY-RUN",
            })
            continue

        # Уже лежит с тем же содержимым — повторная запись не нужна.
        already = await _download(space, name)
        if already is not None and hashlib.sha256(already[0]).hexdigest() == expected_sha:
            record = await _verify(space, name, source, expected_sha)
            record["skipped"] = True
            records.append(record)
            logger.info("%s/%s: уже в хранилище (%s)", space, name, record["status"])
            continue

        with open(source, "rb") as fh:
            payload = io.BytesIO(fh.read())
        await attachment_storage.store_bytes(
            space, name, payload,
            content_type=attachment_storage.guess_content_type(name),
        )

        record = await _verify(space, name, source, expected_sha)
        records.append(record)
        logger.info("%s/%s: %s", space, name, record["status"])

    return records


async def main() -> int:
    parser = argparse.ArgumentParser(description="Перенос вложений в объектное хранилище")
    parser.add_argument(
        "--apply", action="store_true",
        help="выполнить перенос (без флага — только показать, что будет перенесено)",
    )
    parser.add_argument(
        "--report", default=None,
        help="куда положить поштучный отчёт в формате JSON",
    )
    args = parser.parse_args()

    if not attachment_storage.s3_enabled():
        logger.error(
            "S3 не настроен (S3_ENDPOINT_URL / S3_BUCKET_NAME / ключи) — переносить некуда"
        )
        return 2

    logger.info(
        "хранилище: %s бакет=%s, режим=%s",
        attachment_storage.settings.s3_endpoint_url,
        attachment_storage.settings.s3_bucket_name,
        "перенос" if args.apply else "холостой прогон",
    )

    all_records: List[Dict[str, object]] = []
    for space in attachment_storage.SPACES:
        all_records.extend(await migrate_space(space, apply=args.apply))

    by_status: Dict[str, int] = {}
    for record in all_records:
        status = str(record.get("status"))
        by_status[status] = by_status.get(status, 0) + 1

    logger.info("итого: %s", by_status)
    for record in all_records:
        if record.get("status") in ("FAIL", "WARN"):
            logger.warning(
                "%s %s/%s: %s",
                record["status"], record["space"], record["name"], record.get("reason"),
            )

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "applied": bool(args.apply),
                    "summary": by_status,
                    "files": all_records,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("отчёт: %s", args.report)

    # Ненулевой код возврата, если хотя бы один файл не сошёлся: перенос,
    # закончившийся расхождением, нельзя считать успешным.
    return 1 if by_status.get("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
