"""tsk-789: засеять справочники в базе экземпляра `pilot`.

Свежая база после `alembic upgrade head` не содержит НИ ОДНОЙ строки в
справочниках `difficulties` и `roles`. Следствие: экземпляр не принимает ни
одного задания (`difficulty_id` обязателен) и ни одного человека нельзя сделать
учеником или преподавателем. Миграции эти строки не создают — на боевом они
приехали вместе со снимком схемы, а не из истории Alembic.

Значения берутся с боевого, но **id НЕ навязываются**. На пустой базе миграция
`tsk478_parent_role_and_links` уже создала роль `parent`, и она получила id=1 —
а на боевом id=1 это `admin`. Вставка по id совпала бы ключом с чужим именем и
дала тихое расхождение экземпляров. Поэтому роли вставляются по ИМЕНИ
(`ON CONFLICT (name)`), сложности — по `uid`; id раздаёт последовательность.
Идентификаторы справочников внутренние, между экземплярами сравнивать их не надо.

Скрипт идемпотентный.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import asyncpg

DIFFICULTIES = [
    ("THEORY", "Теория", 1, "theory"),
    ("EASY", "Легко", 2, "easy"),
    ("NORMAL", "Средняя", 3, "normal"),
    ("HARD", "Сложно", 4, "hard"),
    ("PROJECT", "Проект", 5, "project"),
]

ROLES = ["admin", "methodist", "teacher", "student", "marketer", "customer", "parent"]


def dsn(env_path: str) -> str:
    text = Path(env_path).read_text(encoding="utf-8")
    raw = re.search(r"^DATABASE_URL=(.+)$", text, re.M).group(1).strip()
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def show(conn: asyncpg.Connection, title: str) -> None:
    d = await conn.fetchval("SELECT count(*) FROM difficulties")
    r = await conn.fetchval("SELECT count(*) FROM roles")
    print(f"  {title}: difficulties={d}, roles={r}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="без него только читает")
    ap.add_argument("--env", default="/opt/lms-pilot/.env",
                    help="окружение экземпляра, откуда взять DATABASE_URL")
    ap.add_argument("--db", default="pilot", help="имя базы, которое ожидаем")
    args = ap.parse_args()

    conn = await asyncpg.connect(dsn(args.env), timeout=20)
    try:
        db = await conn.fetchval("SELECT current_database()")
        if db != args.db:
            print(f"ОТКАЗ: подключились к базе {db!r}, ждали {args.db!r}.")
            return 1
        print(f"база: {db}")
        await show(conn, "ДО")

        if not args.apply:
            print("\nПлан (сухой прогон, ничего не записано):")
            print(f"  добавить недостающие сложности: " + ", ".join(x[0] for x in DIFFICULTIES))
            print(f"  добавить недостающие роли: " + ", ".join(ROLES))
            print("  (что уже есть — не трогаем; id раздаёт последовательность)")
            print("\nПовторить с --apply, чтобы записать.")
            return 0

        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO difficulties (code, name_ru, weight, uid) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT (uid) DO NOTHING",
                DIFFICULTIES,
            )
            # У roles.id нет последовательности (миграция tsk478 вставляла явным
            # числом), поэтому номера считаем сами — от текущего максимума, чтобы
            # не занять уже отданный роли `parent` id=1.
            existing = {r["name"] for r in await conn.fetch("SELECT name FROM roles")}
            next_id = await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM roles")
            for name in ROLES:
                if name in existing:
                    continue
                next_id += 1
                await conn.execute(
                    "INSERT INTO roles (id, name) VALUES ($1, $2)", next_id, name
                )

        await show(conn, "ПОСЛЕ")
        rows = await conn.fetch("SELECT id, code, uid FROM difficulties ORDER BY id")
        print("  сложности:", ", ".join(f"{r['id']}:{r['uid']}" for r in rows))
        rows = await conn.fetch("SELECT id, name FROM roles ORDER BY id")
        print("  роли:     ", ", ".join(f"{r['id']}:{r['name']}" for r in rows))
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
