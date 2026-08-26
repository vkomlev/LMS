# -*- coding: utf-8 -*-
"""tsk-692: вернуть NULL заданиям, которым ADD COLUMN проставил дату накатки.

# Что случилось

Миграция `tsk692_tasks_created_at` добавила `tasks.created_at` с
`server_default now()`, рассчитывая, что существующие строки останутся NULL.
PostgreSQL так не делает: `ALTER TABLE ... ADD COLUMN ... DEFAULT <expr>`
**заполняет значением по умолчанию все существующие строки** (с PG 11 — быстро,
через хранимое значение, но заполняет). В итоге все 7629 заданий получили
`created_at` = момент накатки, то есть выглядят «только что заведёнными» для
КАЖДОГО ученика.

Для правила tsk-692 это худший из возможных исходов: незакрытое задание,
которое «новее» любого зачёта ученика, перестаёт быть долгом. Замер сразу после
выката: правило снимало обязательность с 622 элементов у 36 учеников вместо
13 элементов у 13 — то есть ровно там, где человек просто не дошёл.

# Что делает скрипт

Возвращает `created_at = NULL` заданиям с датой ровно из момента накатки. NULL
читается правилом как «существовало всегда»: такие задания не прощаются никогда.
Никаких других колонок и таблиц скрипт не трогает.

Метка времени берётся параметром и по умолчанию равна моменту прод-накатки
(`2026-08-26 21:02:41.452431+00`). Обнуляются ТОЛЬКО строки с этой меткой — если
после выката успели завести настоящее задание, его дата отличается и останется
на месте (проверяется до записи).

# Порядок (протокол /db-check)

1. Прочитать состояние: сколько строк с меткой, есть ли строки с другими датами.
2. Показать план и выборку (dry-run — поведение по умолчанию).
3. Выполнить в транзакции (`--apply`).
4. Проверить после: строк с меткой не осталось, общее число заданий не менялось.

Запуск:
    python scripts/tsk692_fix_tasks_created_at.py                 # только показать
    DBCHECK_OK=1 python scripts/tsk692_fix_tasks_created_at.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Момент накатки миграции на прод. Ровно эта метка стоит у всех заданий,
# существовавших до выката.
DEFAULT_STAMP = "2026-08-26 21:02:41.452431+00"


def parse_stamp(value: str) -> datetime:
    """Метка как `datetime`: asyncpg не принимает строку в параметр timestamptz."""
    return datetime.fromisoformat(value.replace("+00", "+00:00"))


def load_prod_dsn_asyncpg_style() -> str:
    """DSN прод-роли из `.mcp.json` в формате SQLAlchemy (секрет не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(stamp: str, apply: bool, dsn: str | None) -> int:
    import os

    os.environ["DATABASE_URL"] = dsn or load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    try:
        async with engine.begin() as conn:
            # 1. Состояние до
            before = (
                await conn.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE created_at = :stamp) AS at_stamp, "
                        "       count(*) FILTER (WHERE created_at IS NOT NULL "
                        "                          AND created_at <> :stamp) AS other_dates, "
                        "       count(*) FILTER (WHERE created_at IS NULL) AS already_null, "
                        "       count(*) AS total "
                        "FROM tasks"
                    ),
                    {"stamp": parse_stamp(stamp)},
                )
            ).mappings().one()

            print("== состояние до ==")
            print(f"  заданий всего:               {before['total']}")
            print(f"  с меткой накатки:            {before['at_stamp']}  <- вернём NULL")
            print(f"  с другими датами (настоящие):{before['other_dates']}  <- не трогаем")
            print(f"  уже NULL:                    {before['already_null']}")

            if before["at_stamp"] == 0:
                print("\nСтрок с меткой накатки нет — чинить нечего.")
                return 0

            sample = (
                await conn.execute(
                    text(
                        "SELECT id, course_id, left(COALESCE(task_content->>'title', ''), 40) AS title "
                        "FROM tasks WHERE created_at = :stamp ORDER BY id LIMIT 5"
                    ),
                    {"stamp": parse_stamp(stamp)},
                )
            ).fetchall()
            print("\n== выборка (первые 5) ==")
            for row in sample:
                print(f"  задание {row[0]} (курс {row[1]}): {row[2]}")

            if not apply:
                print("\nЭто предпросмотр. Записи не было. Для правки — --apply.")
                return 0

            updated = (
                await conn.execute(
                    text(
                        "UPDATE tasks SET created_at = NULL "
                        "WHERE created_at = :stamp"
                    ),
                    {"stamp": parse_stamp(stamp)},
                )
            ).rowcount
            print(f"\n== запись ==\n  обнулено строк: {updated}")

            # 4. Проверка после — в той же транзакции, до фиксации
            after = (
                await conn.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE created_at = :stamp) AS at_stamp, "
                        "       count(*) FILTER (WHERE created_at IS NOT NULL) AS with_date, "
                        "       count(*) AS total "
                        "FROM tasks"
                    ),
                    {"stamp": parse_stamp(stamp)},
                )
            ).mappings().one()
            print("== состояние после ==")
            print(f"  заданий всего:      {after['total']} (было {before['total']})")
            print(f"  с меткой накатки:   {after['at_stamp']}")
            print(f"  с любой датой:      {after['with_date']}")

            if after["at_stamp"] != 0:
                raise RuntimeError("Строки с меткой накатки остались — откатываю транзакцию")
            if after["total"] != before["total"]:
                raise RuntimeError("Число заданий изменилось — откатываю транзакцию")
            if after["with_date"] != before["other_dates"]:
                raise RuntimeError(
                    "С датой осталось не столько заданий, сколько было настоящих — откатываю"
                )
    finally:
        await engine.dispose()

    print("\nГотово, транзакция зафиксирована.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", default=DEFAULT_STAMP, help="метка времени накатки")
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    parser.add_argument("--dsn", default=None, help="DSN (по умолчанию — прод из .mcp.json)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.stamp, args.apply, args.dsn)))
