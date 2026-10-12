"""tsk-1330: задания курса повторения 2106 (подкурсы 2159-2161) — рекомендуемые.

Решение оператора 12.10: для ученика 4569 новые задания остаются по желанию.
До выката правки content_grace_service (индивидуальные курсы правилом tsk-692
больше не прощаются) это держалось прощением; после выката — этой отметкой.

Запуск: python scripts/tsk1330_review_tasks_recommended.py [--apply]
"""
import asyncio
import logging
import sys

import asyncpg

from tsk1330_replace_review_subcourses import _dsn

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tsk1330")

COURSES = [2159, 2160, 2161]
EXPECTED = 22


async def main(apply: bool) -> None:
    """Отметить задания рекомендуемыми в транзакции и проверить."""
    conn = await asyncpg.connect(_dsn())
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            res = await conn.execute(
                "UPDATE tasks SET requirement_level='recommended' "
                "WHERE course_id = ANY($1::int[]) AND requirement_level='required'", COURSES)
            left = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = ANY($1::int[]) "
                "AND requirement_level <> 'recommended'", COURSES)
            log.info("%s; осталось не recommended: %s", res, left)
            if res != f"UPDATE {EXPECTED}" or left != 0:
                raise RuntimeError("проверка не сошлась")
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
