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
from app.services.requirement_override_service import active_overrides, desired_rule_overrides

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
            if desired != stored or on_the_fly != stored_grace:
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
