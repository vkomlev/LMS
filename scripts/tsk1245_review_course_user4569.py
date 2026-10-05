"""tsk-1245: индивидуальный курс повторения для ученика 4569 (Афанасьев Кирилл).

Создаёт корневой курс, подключает готовые темы банка подкурсами
(863 числа -> 867 строки -> 864 условия -> 986 ветвление ОГЭ)
и записывает ученика 4569 на корень. Одна транзакция.

Запуск: python scripts/tsk1245_review_course_user4569.py [--apply]
Без --apply — прогон с откатом (dry-run).
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tsk1245")

project_root = Path(__file__).resolve().parent.parent
USER_ID = 4569
SUBCOURSES = [863, 867, 864, 986]
TITLE = "Повторение: числа, строки, условия (индивидуально)"
UID = "ind:tsk1245-user4569-povtorenie"
DESCRIPTION = (
    "Индивидуальный курс повторения. Порядок: числа -> строки -> условия -> "
    "ветвление в формате ОГЭ. В каждой теме сначала теория, затем задания: "
    "предскажи вывод программы, найди ошибку, напиши программу. "
    "Решай сам, без ИИ: на занятии разберём твой код устно."
)


def _dsn() -> str:
    """Прод-DSN learn из `.mcp.json` (секрет не печатаем)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("прод-DSN learn_prod_db не найден в .mcp.json")


async def main(apply: bool) -> None:
    """Выполнить запись в транзакции и проверить результат."""
    conn = await asyncpg.connect(_dsn())
    try:
        exists = await conn.fetchval("SELECT id FROM courses WHERE course_uid=$1", UID)
        if exists:
            raise RuntimeError(f"курс с uid {UID} уже есть: id={exists}")
        tr = conn.transaction()
        await tr.start()
        try:
            cid = await conn.fetchval(
                "INSERT INTO courses (title, access_level, description, course_uid) "
                "VALUES ($1, 'self_guided', $2, $3) RETURNING id",
                TITLE, DESCRIPTION, UID,
            )
            for n, sub in enumerate(SUBCOURSES, start=1):
                await conn.execute(
                    "INSERT INTO course_parents (course_id, parent_course_id, order_number, is_transparent) "
                    "VALUES ($1, $2, $3, false)", sub, cid, n,
                )
            await conn.execute(
                "INSERT INTO user_courses (user_id, course_id, is_active) VALUES ($1, $2, true)",
                USER_ID, cid,
            )
            kids = await conn.fetch(
                "SELECT course_id, order_number FROM course_parents WHERE parent_course_id=$1 ORDER BY order_number", cid)
            uc = await conn.fetchrow(
                "SELECT order_number, is_active FROM user_courses WHERE user_id=$1 AND course_id=$2", USER_ID, cid)
            log.info("курс id=%s; подкурсы=%s; запись=%s", cid,
                     [(k["course_id"], k["order_number"]) for k in kids], dict(uc))
            if [k["course_id"] for k in kids] != SUBCOURSES or uc is None:
                raise RuntimeError("проверка после записи не сошлась")
        except Exception:
            await tr.rollback()
            raise
        if apply:
            await tr.commit()
            log.info("COMMIT")
        else:
            await tr.rollback()
            log.info("dry-run: ROLLBACK")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
