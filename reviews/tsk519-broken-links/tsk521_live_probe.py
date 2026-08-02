"""tsk-521: живая проверка на проде — доходит ли находка до методиста.

Заводит временный материал с ссылкой на заведомо отсутствующий файл в пустом
курсе 19 (0 зачислений, вне учебного пути), даёт прогнать проверку и убирает за
собой. Протокол /db-check: сначала читаем состояние, потом пишем в транзакции,
потом верифицируем.

Запуск на проде:
    sudo -u app /opt/lms/venv/bin/python tsk521_live_probe.py --setup
    sudo -u app /opt/lms/venv/bin/python tsk521_live_probe.py --cleanup
"""
from __future__ import annotations

import asyncio
import json
import sys
from uuid import uuid4

sys.path.insert(0, "/opt/lms")

from dotenv import load_dotenv

load_dotenv("/opt/lms/.env", encoding="utf-8-sig")

import asyncpg  # noqa: E402

PROBE_TITLE = "tsk-521 проверка связности (временный)"
COURSE_ID = 19


def dsn() -> str:
    """DATABASE_URL из .env в формате asyncpg."""
    with open("/opt/lms/.env", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').replace(
                    "postgresql+asyncpg://", "postgresql://"
                )
    raise RuntimeError("DATABASE_URL не найден")


async def show_state(conn: asyncpg.Connection, label: str) -> None:
    """Печатает, что сейчас есть по пробе и по уведомлениям."""
    mats = await conn.fetch(
        "SELECT id, is_active FROM materials WHERE title = $1", PROBE_TITLE
    )
    notes = await conn.fetchval(
        "SELECT count(*) FROM notifications WHERE kind = 'broken_media_links'"
    )
    print(f"--- {label}: материалов-проб {len(mats)} {[dict(m) for m in mats]}, "
          f"уведомлений broken_media_links {notes}")


async def setup(conn: asyncpg.Connection) -> None:
    """Создаёт материал с ссылкой на файл, которого нет."""
    await show_state(conn, "ДО")
    file_id = f"{uuid4().hex}{uuid4().hex}.png"  # 64 hex — формат CAS-имени
    content = {
        "sources": [{"url": f"/api/v1/materials/files/{file_id}", "type": "file"}],
        "default_source": 0,
    }
    print(f"ПЛАН: INSERT materials (курс {COURSE_ID}, title={PROBE_TITLE!r}), "
          f"ссылка на несуществующий файл {file_id}")
    async with conn.transaction():
        mid = await conn.fetchval(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES ($1, 'image', $2::jsonb, $3, true) RETURNING id",
            PROBE_TITLE, json.dumps(content), COURSE_ID,
        )
    print(f"создан материал {mid}")
    await show_state(conn, "ПОСЛЕ")


async def cleanup(conn: asyncpg.Connection) -> None:
    """Убирает материал-пробу и уведомления, которые он породил."""
    await show_state(conn, "ДО")
    async with conn.transaction():
        deleted_mats = await conn.execute(
            "DELETE FROM materials WHERE title = $1", PROBE_TITLE
        )
        deleted_notes = await conn.execute(
            "DELETE FROM notifications WHERE kind = 'broken_media_links'"
        )
    print(f"удалено: материалы={deleted_mats}, уведомления={deleted_notes}")
    await show_state(conn, "ПОСЛЕ")


async def main() -> None:
    conn = await asyncpg.connect(dsn())
    try:
        if "--setup" in sys.argv:
            await setup(conn)
        elif "--cleanup" in sys.argv:
            await cleanup(conn)
        else:
            await show_state(conn, "текущее состояние")
            print("нужен флаг --setup или --cleanup")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
