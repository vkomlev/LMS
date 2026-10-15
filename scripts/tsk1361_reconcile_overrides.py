"""tsk-1361: сверка таблицы переопределений с расчётом правил (только чтение).

Для каждого ученика с действующим зачислением сравнивает:
  * строки правил в `student_requirement_override` и текущий расчёт правил;
  * прощённое в таблице и прежний расчёт на лету (`graced_for_roots`).
Ничего не пишет: транзакция откатывается. Код выхода 1 — есть расхождения.

Запуск: python scripts/tsk1361_reconcile_overrides.py [--student ID]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory
from app.services.content_grace_service import graced_for_roots, invalidate_grace_cache
from app.services.program_scope_service import excluded_courses_for  # noqa: E402
from app.services.requirement_override_service import (  # noqa: E402
    REQUIRED_LEVELS,
    active_overrides,
    desired_rule_overrides,
    resolve_levels,
)

# Обязательные элементы поддерева выпавших номеров — как их сегодня вычитает движок.
_DROPPED_ITEMS_SQL = """
WITH RECURSIVE sub AS (
    SELECT unnest(CAST(:ids AS integer[])) AS cid, ARRAY[0] AS path
    UNION ALL
    SELECT cp.course_id, s.path || cp.course_id
      FROM sub s JOIN course_parents cp ON cp.parent_course_id = s.cid
     WHERE NOT cp.course_id = ANY(s.path)
)
SELECT 'task', id FROM tasks
 WHERE course_id IN (SELECT cid FROM sub) AND is_active AND requirement_level = ANY(:lv)
UNION
SELECT 'material', id FROM materials
 WHERE course_id IN (SELECT cid FROM sub) AND is_active AND requirement_level = ANY(:lv)
"""

logger = logging.getLogger("tsk1361_reconcile")


async def _run(only_student: int | None) -> int:
    """Пройти учеников и вернуть число учеников с расхождениями."""
    bad = 0
    async with async_session_factory() as db:
        ids = (
            await db.execute(
                text("SELECT DISTINCT user_id FROM user_courses WHERE is_active ORDER BY user_id")
            )
        ).scalars().all()
        if only_student is not None:
            ids = [i for i in ids if i == only_student]
        total_rows = 0
        for sid in ids:
            invalidate_grace_cache(db)
            desired = await desired_rule_overrides(db, int(sid))
            stored = {t: v.reason_code for t, v in (await active_overrides(db, int(sid))).items() if v.source == "rule"}
            total_rows += len(stored)
            graced = await graced_for_roots(db, int(sid))
            on_the_fly = {("task", i) for i in graced.tasks} | {("material", i) for i in graced.materials}
            stored_grace = {t for t, c in stored.items() if c.startswith("grace_")}

            # Б2: то, что увидят экраны при включённом рубильнике, против
            # того, что они видят сейчас (прощение + выпавшие номера). Цели
            # под решением человека не сравниваются — там расхождение и есть цель.
            dropped = sorted(await excluded_courses_for(db, student_id=int(sid)))
            today = set(on_the_fly)
            if dropped:
                rows = await db.execute(text(_DROPPED_ITEMS_SQL), {"ids": dropped, "lv": list(REQUIRED_LEVELS)})
                today |= {(str(k), int(i)) for k, i in rows.fetchall()}
            resolved = await resolve_levels(db, int(sid))
            human = {t for t, v in resolved.reasons.items() if v.source == "human"}
            resolver_gap = (set(resolved.demoted) ^ today) - human
            if resolver_gap:
                logger.warning("ученик %s: резолвер расходится с экранами сегодня %s", sid, sorted(resolver_gap)[:10])

            if desired != stored or on_the_fly != stored_grace or resolver_gap:
                bad += 1
                logger.warning(
                    "ученик %s: нет в таблице %s, лишнее в таблице %s, расхождение с расчётом на лету %s",
                    sid,
                    sorted(set(desired) - set(stored))[:10],
                    sorted(set(stored) - set(desired))[:10],
                    sorted(on_the_fly ^ stored_grace)[:10],
                )
        await db.rollback()
    logger.info("учеников %s, строк правил %s, с расхождением %s", len(ids), total_rows, bad)
    return bad


def main() -> None:
    """Точка входа."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student", type=int, default=None)
    args = parser.parse_args()
    sys.exit(1 if asyncio.run(_run(args.student)) else 0)


if __name__ == "__main__":
    main()
