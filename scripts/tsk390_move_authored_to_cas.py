# -*- coding: utf-8 -*-
"""tsk-390: перенести авторские файлы заданий с сайта автора в CAS платформы.

ЗАЧЕМ
У авторских заданий курса файл приложен прямой ссылкой на `victor-komlev.ru/wp-content/
uploads/...`. Ученику она работает, но файл живёт вне LMS: переедет или пропадёт на сайте —
задания молча останутся без данных, и заметить это будет нечем. Копия в CAS делает их
устойчивыми и попадает под общий регулярный чек.

ЧТО ДЕЛАЕТ
Скачивает файл, кладёт в CAS (тот же помощник и бакет, что во всей партии), проверяет
боевым эндпоинтом и **ЗАМЕНЯЕТ** внешний адрес в `href` на `/api/v1/media/<sha>.<ext>`.
Именно заменяет: добавление второй ссылки уже дало путаницу с двумя файлами (см. откат
tsk390_revert_authored). Видимый текст ссылки («скачать 3.zip») не трогается — ученик
видит то же самое.

Метаданные `has_attached_file` / `attached_file_paths` проставляются заодно, в том же
формате, что у остальной партии.

ГРУППЫ (файл автора, проверен по содержимому)
  * 4780-4789 (ЕГЭ №3)  → 3.zip → внутри 3.ods, листы «Движение_товаров»/«Товар»/«Магазин»,
    «M10» латиницей — дословно как в тексте заданий;
  * 5089-5095 (ЕГЭ №17) → 17_1970.zip → внутри 17_1970.txt, ровно 5000 целых чисел −1000…1000.

Запуск:
  python scripts/tsk390_move_authored_to_cas.py --backup <файл> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402
from tsk369_store_cas import check_public, load_cb_env  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0"
MEDIA_BASE = "/api/v1/media"

GROUPS = [
    {"url": "https://victor-komlev.ru/wp-content/uploads/2026/06/3.zip",
     "ext": "zip", "ids": list(range(4780, 4790))},
    {"url": "https://victor-komlev.ru/wp-content/uploads/2025/09/17_1970.zip",
     "ext": "zip", "ids": list(range(5089, 5096))},
]


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
        return r.read()


async def main(backup_path: Path, apply: bool) -> None:
    cas_root = load_cb_env()
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    # 1. Файлы в CAS (до правки БД: ссылка осмысленна только если по ней что-то отдаётся).
    for g in GROUPS:
        data = fetch(g["url"])
        sha_ext = f"{hashlib.sha256(data).hexdigest()}.{g['ext']}"
        g["sha_ext"] = sha_ext
        g["size"] = len(data)
        print(f"{g['url'].rsplit('/', 1)[-1]}: {len(data)} байт → {sha_ext[:16]}…")
        if apply:
            ok, note = check_public(sha_ext)
            if not ok:
                got = await store_bytes_to_cas(data, g["ext"], cas_root)
                if got != sha_ext:
                    raise RuntimeError(f"CAS вернул {got!r} вместо {sha_ext!r}")
                ok, note = check_public(sha_ext)
            if not ok:
                raise RuntimeError(f"файл не отдаётся боевым эндпоинтом: {note}")
            print(f"  в хранилище и доступен: {note}")

    by_id = {tid: g for g in GROUPS for tid in g["ids"]}
    ids = sorted(by_id)

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks "
            "WHERE id = ANY($1::int[]) AND is_active", ids)}
        missing = sorted(set(ids) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл активных заданий: {missing}")

        plan: dict[int, str] = {}
        skipped: list[int] = []
        for tid in ids:
            g = by_id[tid]
            stem = rows[tid]["stem"] or ""
            new_link = f"{MEDIA_BASE}/{g['sha_ext']}"
            if new_link in stem:
                skipped.append(tid)
                continue
            if g["url"] not in stem:
                raise RuntimeError(f"у задания {tid} нет ожидаемой ссылки {g['url']}")
            plan[tid] = stem.replace(g["url"], new_link)

        if skipped:
            print(f"  уже на CAS-ссылке (пропускаю): {skipped}")
        print(f"К замене ссылки: {len(plan)} заданий")
        if not plan:
            return

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": t, "stem": rows[t]["stem"]} for t in sorted(plan)],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних условий: {backup_path}")

        async with conn.transaction():
            for tid, new_stem in plan.items():
                paths = [f"{MEDIA_BASE}/{by_id[tid]['sha_ext']}"]
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  jsonb_set("
                    "    jsonb_set("
                    "      jsonb_set(task_content, '{stem}', to_jsonb($2::text)),"
                    "      '{has_attached_file}', 'true'::jsonb),"
                    "    '{attached_file_paths}', $3::jsonb) "
                    "WHERE id = $1",
                    tid, new_stem, json.dumps(paths))

            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem FROM tasks "
                "WHERE id = ANY($1::int[])", sorted(plan))}
            bad = [t for t in plan
                   if by_id[t]["sha_ext"] not in (check[t]["stem"] or "")
                   or by_id[t]["url"] in (check[t]["stem"] or "")]
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad}")
            print(f"Внутри транзакции: обновлено и проверено {len(plan)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks "
            "WHERE id = ANY($1::int[])", sorted(plan))}
        problems = [t for t in plan
                    if by_id[t]["sha_ext"] not in (after[t]["stem"] or "")
                    or by_id[t]["url"] in (after[t]["stem"] or "")]
        print(f"  проверено построчно: {len(plan)}; расхождений: {len(problems)}")
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
