# -*- coding: utf-8 -*-
"""tsk-369, шаг 4: положить скачанные файлы в CAS и прод-S3, проверить доступность.

ЗАЧЕМ ОТДЕЛЬНЫМ ШАГОМ
Ссылка в условии осмысленна, только если по ней реально отдаётся файл. Поэтому сначала
файлы кладутся в хранилище и проверяются HTTP-запросом к боевому эндпоинту, и лишь потом
правится БД. Обратный порядок оставил бы учеников со ссылками в никуда.

КАК
Используется штатный помощник ContentBackbone `store_bytes_to_cas`: тот же CAS-путь
(`<sha[:2]>/<sha>.<ext>`), тот же прод-бакет, та же идемпотентность по содержимому —
файл, который уже лежит в хранилище, не перезаписывается. S3-креды и `CAS_MEDIA_ROOT`
берутся из `.env` ContentBackbone; значения не печатаются.

Записывает только НОВЫЕ объекты в S3 (по содержимому), ничего не удаляет и не заменяет:
имя объекта — sha256 содержимого, коллизия означала бы тот же самый файл.

Проверка после записи: `GET https://api.learn.victor-komlev.ru/api/v1/media/<sha_ext>` —
307 на S3 и 200 по редиректу. Проверяются ВСЕ файлы, не выборка.

Запуск:
  python scripts/tsk369_store_cas.py --plan <plan.json> --out <stored.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

CB_ROOT = Path(r"D:\Work\ContentBackbone")
MEDIA_URL = "https://api.learn.victor-komlev.ru/api/v1/media/{}"
UA = "tsk369-store/1.0"


def load_cb_env() -> Path:
    """Подтянуть окружение ContentBackbone (S3-креды, CAS_MEDIA_ROOT). Значения не печатаем."""
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=CB_ROOT / ".env", encoding="utf-8-sig")
    sys.path.insert(0, str(CB_ROOT))
    cas_root = Path(os.environ.get("CAS_MEDIA_ROOT", str(CB_ROOT / "data" / "media_store")))
    return cas_root


def check_public(sha_ext: str) -> tuple[bool, str]:
    req = urllib.request.Request(MEDIA_URL.format(sha_ext), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            size = resp.headers.get("Content-Length") or "?"
            return resp.status == 200, f"HTTP {resp.status}, Content-Length={size}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"сеть: {exc}"


async def main(plan_path: Path, out_path: Path, apply: bool) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))["plan"]
    cas_root = load_cb_env()

    from monolith.external_tasks.media.cas_downloader import (  # noqa: E402
        CAS_MAX_FILE_BYTES, store_bytes_to_cas,
    )

    # Уникальные файлы: несколько заданий часто делят один и тот же файл источника.
    uniq: dict[str, dict] = {}
    for task in plan:
        for f in task["files"]:
            if f.get("reuse"):
                continue  # файл уже в CAS (близнец внутри LMS) — класть нечего
            uniq.setdefault(f["sha_ext"], f)

    oversize = [f for f in uniq.values() if (f["size"] or 0) > CAS_MAX_FILE_BYTES]
    print(f"Файлов к загрузке: {len(uniq)}; лимит на файл: {CAS_MAX_FILE_BYTES/1e6:.0f} МБ")
    if oversize:
        print(f"  ВНИМАНИЕ: {len(oversize)} файл(ов) больше лимита — будут пропущены "
              f"помощником CAS: {[f['sha_ext'][:12] for f in oversize]}")
        print("  Поднять лимит: переменная окружения CAS_MAX_FILE_BYTES перед запуском.")

    if not apply:
        print("\nDRY-RUN: ничего не записано (запусти с --apply).")
        return

    stored, failed = [], []
    for n, (sha_ext, f) in enumerate(sorted(uniq.items()), 1):
        # Идемпотентность прогона: файл, который уже отдаётся боевым эндпоинтом, не
        # перезаливаем — имя объекта равно sha256 содержимого, значит это тот же файл.
        ok, note = check_public(sha_ext)
        if ok:
            stored.append({"sha_ext": sha_ext, "size": f["size"], "http": note,
                           "skipped": "уже в хранилище"})
            print(f"  [ok ] {n}/{len(uniq)} {sha_ext[:12]}.{f['ext']} — уже в хранилище")
            continue
        data = Path(f["path"]).read_bytes()
        got = await store_bytes_to_cas(data, f["ext"], cas_root)
        if got != sha_ext:
            failed.append({"sha_ext": sha_ext, "error": f"CAS вернул {got!r}"})
            print(f"  [ОШИБКА] {n}/{len(uniq)} {sha_ext[:12]} → {got!r}")
            continue
        ok, note = check_public(sha_ext)
        (stored if ok else failed).append(
            {"sha_ext": sha_ext, "size": f["size"], "http": note})
        print(f"  [{'ok ' if ok else 'НЕТ'}] {n}/{len(uniq)} {sha_ext[:12]}.{f['ext']} "
              f"{(f['size'] or 0)/1e3:.0f} КБ — {note}")

    out_path.write_text(json.dumps({"stored": stored, "failed": failed},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nЗагружено и доступно: {len(stored)}; проблемных: {len(failed)}")
    print(f"Сохранено: {out_path}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(Path(a.plan), Path(a.out), a.apply))
