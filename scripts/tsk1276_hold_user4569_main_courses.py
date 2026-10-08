"""Срочная мера: закрыть ученику 4569 основные курсы до выката серверного замка.

Замок «индивидуальный курс закрывает основные» (tsk-1245) действовал только в
подсказке следующего шага: задания из программы курса открывались и сдавались
напрямую, а автоматика ДЗ выдала задания закрытого курса. До выката правки
отключаем записи на 88 и 112 (прогресс сохраняется) и отменяем ДЗ 700.

Запуск: python scripts/tsk1276_hold_user4569_main_courses.py [--apply] [--undo]
--undo возвращает записи (ДЗ не возвращает).
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tsk1276")

USER_ID = 4569
ROOTS = [88, 112]
HOMEWORK_ID = 700
project_root = Path(__file__).resolve().parent.parent


def _dsn() -> str:
    """Прод-DSN learn из `.mcp.json` (секрет не печатаем)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    for arg in cfg.get("mcpServers", cfg)["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("прод-DSN не найден")


async def main(apply: bool, undo: bool) -> None:
    """Отключить (или вернуть) записи и отменить ДЗ в одной транзакции."""
    conn = await asyncpg.connect(_dsn())
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            n = await conn.execute(
                "UPDATE user_courses SET is_active=$3 WHERE user_id=$1 AND course_id = ANY($2::int[])",
                USER_ID, ROOTS, undo,
            )
            if not undo:
                await conn.execute(
                    "UPDATE homework_assignment SET cancelled_at=now() "
                    "WHERE id=$1 AND student_id=$2 AND cancelled_at IS NULL", HOMEWORK_ID, USER_ID,
                )
            rows = await conn.fetch(
                "SELECT course_id, is_active FROM user_courses WHERE user_id=$1 ORDER BY order_number", USER_ID)
            hw = await conn.fetchval("SELECT cancelled_at IS NOT NULL FROM homework_assignment WHERE id=$1", HOMEWORK_ID)
            log.info("%s; записи %s; ДЗ %s отменено: %s", n,
                     [(r["course_id"], r["is_active"]) for r in rows], HOMEWORK_ID, hw)
            states = {r["course_id"]: r["is_active"] for r in rows}
            if any(states.get(r) != undo for r in ROOTS) or states.get(2106) is not True:
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
    asyncio.run(main("--apply" in sys.argv, "--undo" in sys.argv))
