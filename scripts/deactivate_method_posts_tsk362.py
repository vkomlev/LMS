# -*- coding: utf-8 -*-
"""tsk-362: деактивировать посты-разборы приёма, импортированные как задания.

ПОЧЕМУ
Три записи из партии `tg:*` — не задания, а методические посты: у них нет условия задачи,
только заголовок темы разбора. Как задания они неработоспособны: ученику нечего решать,
а проверять нечего в принципе.

  * 3394 — «Задание 19-21. Пишем программу на две кучи»
  * 3447 — «Задание 13. Понятие сети, маски, адреса сети. Решение заданий на определение
            адреса сети (без программы)»
  * 3468 — «Задание 18. Динамическое программирование.»

Решение оператора 2026-07-22: деактивировать (`is_active=false`), а не «чинить». Содержимое
остаётся в базе — при желании его позже перенесут в материалы курса.

ГАРАНТИИ
UPDATE по трём id и только если запись действительно короткая (условия нет) и активна.
Порог длины проверяется в самом скрипте: если текст вдруг окажется полноценным условием,
скрипт остановится, а не деактивирует учебный контент.

Запуск: dry-run по умолчанию;
  python scripts/deactivate_method_posts_tsk362.py
  DBCHECK_OK=1 python scripts/deactivate_method_posts_tsk362.py --apply
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

project_root = Path(__file__).resolve().parents[1]

TARGETS = [3394, 3447, 3468]
MAX_STEM_LEN = 400  # длиннее — это уже похоже на условие задачи, останавливаемся


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, course_id, is_active, "
                "length(regexp_replace(task_content->>'stem', '<[^>]+>', ' ', 'g')) AS stem_len, "
                "left(regexp_replace(task_content->>'stem', '<[^>]+>', ' ', 'g'), 90) AS stem "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", TARGETS)
            if len(rows) != len(TARGETS):
                raise AssertionError(f"нашлось {len(rows)} из {len(TARGETS)}")
            for r in rows:
                print(f"ДО: id={r['id']} курс={r['course_id']} активно={r['is_active']} "
                      f"длина условия={r['stem_len']} «{r['stem'].strip()[:70]}»")
                if r["stem_len"] > MAX_STEM_LEN:
                    raise AssertionError(
                        f"id={r['id']}: условие длиной {r['stem_len']} — похоже на настоящее задание, не трогаю")

            res = await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = ANY($1::int[]) AND is_active", TARGETS)
            print(f"\nДеактивировано: {res}")

            after = await conn.fetch(
                "SELECT id, is_active FROM tasks WHERE id = ANY($1::int[])", TARGETS)
            if any(r["is_active"] for r in after):
                raise AssertionError("не все деактивированы")

            # Курс не должен остаться совсем без активных заданий незаметно для нас.
            empty = await conn.fetch(
                "SELECT course_id, count(*) FILTER (WHERE is_active) AS active FROM tasks "
                "WHERE course_id IN (SELECT course_id FROM tasks WHERE id = ANY($1::int[])) "
                "GROUP BY course_id ORDER BY course_id", TARGETS)
            for r in empty:
                print(f"  курс {r['course_id']}: активных заданий осталось {r['active']}")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
