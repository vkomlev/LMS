# -*- coding: utf-8 -*-
"""tsk-369, доводка: убрать повтор «Файл к заданию: Файл к заданию» в подписи ссылки.

ЧТО НЕ ТАК
Блок со ссылкой, добавленный в tsk-369, выглядит так:
    <p><strong>Файл к заданию:</strong> <a …>ИМЯ</a></p>
Если источник не дал имени файла (34 задания), подписью бралось «Файл к заданию» — и на
экране получалось «Файл к заданию: Файл к заданию». Ученику это читается как сбой вёрстки.

ЧТО ДЕЛАЕТ
Меняет ТОЛЬКО текст подписи внутри своего блока на «скачать». Адрес ссылки, порядок и
остальное условие не трогаются: правка чисто косметическая и идемпотентная (второй запуск
находит 0 заданий).

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Бэкап пишется до записи, после COMMIT —
построчная проверка.

Запуск: python scripts/tsk369_fix_link_label.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

OLD = '">Файл к заданию</a>'
NEW = '">скачать</a>'
MARKER = "<p><strong>Файл к заданию:</strong>"

SQL = """
SELECT id, external_uid, task_content->>'stem' AS stem
FROM tasks
WHERE is_active
  AND (task_content->>'stem') LIKE '<p><strong>Файл к заданию:</strong>%'
  AND (task_content->>'stem') LIKE '%">Файл к заданию</a>%'
ORDER BY id
"""


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(SQL)
        print(f"Заданий с повтором подписи: {len(rows)}")
        if not rows:
            print("Нечего править.")
            return
        print("  id: " + ", ".join(str(r["id"]) for r in rows[:40]))

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": r["id"], "external_uid": r["external_uid"], "stem": r["stem"]} for r in rows],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап: {backup_path}")

        async with conn.transaction():
            for r in rows:
                stem = r["stem"]
                head, sep, tail = stem.partition("</p>\n")
                if not sep or not head.startswith(MARKER):
                    raise AssertionError(f"id={r['id']}: блок ссылки не в начале условия")
                new_stem = head.replace(OLD, NEW) + sep + tail
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  jsonb_set(task_content, '{stem}', to_jsonb($2::text)) WHERE id = $1",
                    r["id"], new_stem)

            ids = [r["id"] for r in rows]
            check = await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])", ids)
            bad = [c["id"] for c in check
                   if OLD in (c["stem"] or "").split("</p>\n")[0] or NEW not in (c["stem"] or "")]
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad[:10]}")
            print(f"Внутри транзакции: обновлено и проверено {len(rows)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        after = await conn.fetch(
            "SELECT id, left(task_content->>'stem', 160) AS head FROM tasks WHERE id = ANY($1::int[])",
            [r["id"] for r in rows])
        left = [a["id"] for a in after if "Файл к заданию</a>" in a["head"]]
        print(f"\nЗАПИСАНО И ЗАКОММИЧЕНО. Проверено построчно: {len(after)}; "
              f"осталось с повтором: {len(left)}")
        print("  пример:", after[0]["head"][:140])
        if left:
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
