"""tsk-519: деактивация материала 187 (битая ссылка на отсутствующий файл).

Протокол /db-check, режим записи: читаем состояние ДО, показываем план,
пишем в транзакции, верифицируем ПОСЛЕ. Без `--apply` только читает.

Запуск на проде: sudo -u app /opt/lms/venv/bin/python <файл> [--apply]
"""
from __future__ import annotations

import asyncio
import sys

import asyncpg

MATERIAL_ID = 187
DSN_PATH = "/opt/lms/.env"


def dsn_from_env(path: str = DSN_PATH) -> str:
    """Достаёт DATABASE_URL из .env и приводит его к формату asyncpg."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                return dsn.replace("postgresql+asyncpg://", "postgresql://")
    raise RuntimeError("DATABASE_URL не найден в .env")


async def snapshot(conn: asyncpg.Connection, label: str) -> asyncpg.Record | None:
    """Печатает текущее состояние материала и соседей по курсу."""
    row = await conn.fetchrow(
        """
        SELECT id, course_id, type::text AS type, title, is_active,
               order_position, updated_at, content::text AS content
        FROM materials WHERE id = $1
        """,
        MATERIAL_ID,
    )
    print(f"\n--- состояние {label} ---")
    if row is None:
        print("материал не найден")
        return None
    print(
        f"id={row['id']} course_id={row['course_id']} type={row['type']} "
        f"title={row['title']!r} is_active={row['is_active']} "
        f"order_position={row['order_position']} updated_at={row['updated_at']}"
    )
    siblings = await conn.fetch(
        "SELECT id, is_active, order_position FROM materials WHERE course_id = $1 ORDER BY order_position",
        row["course_id"],
    )
    print("материалы курса:", [(s["id"], s["is_active"], s["order_position"]) for s in siblings])
    return row


async def main() -> None:
    apply = "--apply" in sys.argv
    conn = await asyncpg.connect(dsn_from_env())
    try:
        before = await snapshot(conn, "ДО")
        if before is None:
            return
        if not before["is_active"]:
            print("\nматериал уже выключен — правка не нужна")
            return

        print(
            "\nПЛАН: UPDATE materials SET is_active = false WHERE id = 187 AND is_active = true"
            "\n      order_position не трогаем (триггер порядка при равном значении не двигает соседей);"
            "\n      updated_at проставит триггер trg_material_updated_at;"
            "\n      строку student_material_progress не трогаем."
        )
        if not apply:
            print("\nDRY-RUN: запись не выполнялась. Повторить с --apply.")
            return

        async with conn.transaction():
            status = await conn.execute(
                "UPDATE materials SET is_active = false WHERE id = $1 AND is_active = true",
                MATERIAL_ID,
            )
            print(f"\nвыполнено: {status}")
            if status != "UPDATE 1":
                raise RuntimeError(f"ожидалась ровно одна строка, получено: {status}")

        after = await snapshot(conn, "ПОСЛЕ")
        assert after is not None and after["is_active"] is False, "материал остался активным"
        assert after["order_position"] == before["order_position"], "сдвинулся order_position"
        assert after["content"] == before["content"], "изменился content"
        print("\nверификация пройдена: is_active=false, порядок и content не изменились")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
