"""Проверка изоляции экземпляра «pilot» от боевых данных (tsk-764).

Запускать на сервере из каталога экземпляра:

    cd /opt/lms-pilot && sudo -u app venv/bin/python deploy/pilot/isolation_check.py

Скрипт только читает. Он отвечает на один вопрос: та ли база подключена и
пуста ли она. Проверять изоляцию по конфигурации нельзя — прецедент tsk-614:
три яруса полигона выглядели раздельными, а обращения уходили в боевую систему.

Ожидаемый результат для нового экземпляра: имя базы `pilot`, во всех
перечисленных таблицах ноль строк. Ненулевой счётчик означает либо что
подключились не туда, либо что в чужую базу попали наши данные — и то и другое
повод остановиться.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Final

import asyncpg

# Таблицы, по которым видно «наши данные»: люди, их учёба и контент.
TABLES: Final[tuple[str, ...]] = (
    "users",
    "courses",
    "tasks",
    "materials",
    "task_results",
    "attempts",
)

EXPECTED_DB: Final[str] = "pilot"


def _dsn() -> str:
    """Достать DSN из окружения и привести к виду, понятному asyncpg."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        # Служба читает .env через systemd; при ручном запуске подхватим сами.
        for line in open(".env", encoding="utf-8"):
            if line.startswith("DATABASE_URL="):
                raw = line.split("=", 1)[1].strip()
                break
    if not raw:
        raise SystemExit("DATABASE_URL не найден ни в окружении, ни в .env")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        db_name: str = await conn.fetchval("SELECT current_database()")
        role: str = await conn.fetchval("SELECT current_user")
        print(f"база: {db_name}   роль: {role}")

        if db_name != EXPECTED_DB:
            print(f"ОТКАЗ: подключились к базе {db_name!r}, а ждали {EXPECTED_DB!r}.")
            return 1

        # Роль экземпляра не должна доставать до боевой базы даже по имени.
        visible = await conn.fetch(
            "SELECT datname FROM pg_database "
            "WHERE has_database_privilege(current_user, datname, 'CONNECT') "
            "AND datname NOT IN ('template0', 'template1', 'postgres') "
            "ORDER BY datname"
        )
        print("базы, куда пускает эта роль:", ", ".join(r["datname"] for r in visible))

        dirty = False
        for table in TABLES:
            exists: bool = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
            if not exists:
                print(f"  {table:<14} — таблицы нет (схема не применена?)")
                dirty = True
                continue
            count: int = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
            mark = "ok" if count == 0 else "НЕ ПУСТО"
            print(f"  {table:<14} {count:>8}  {mark}")
            if count:
                dirty = True

        if dirty:
            print("\nИтог: в базе экземпляра есть строки или нет схемы — разобраться до запуска людей.")
            return 1
        print("\nИтог: база своя и пустая. Наших учеников в экземпляре нет.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
