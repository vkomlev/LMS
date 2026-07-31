"""tsk-491: убрать хвост участий у учеников, откреплённых от слота.

Открепление от слота до этой задачи гасило только связь `lesson_slot_student`
и НЕ трогало уже созданные будущие занятия — ученик продолжал числиться в
списках явки слота, из которого его убрали. Код исправлен; здесь убирается то,
что успело накопиться.

Трогаем только строки, где ученик ничего не решал сам (`status='scheduled'`) —
подтверждённая явка, отказ и «не пришёл» остаются историей.

Запуск (на прод-сервере, под app):
    python scripts/tsk491_cleanup_stale_participants.py            # dry-run
    DBCHECK_OK=1 python scripts/tsk491_cleanup_stale_participants.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from app.db.session import async_session_factory

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk491")

# Хвост: занятие в будущем, слот тот же, а связь ученика со слотом погашена.
_SELECT_STALE = text(
    """
    SELECT lop.id AS participant_id,
           lop.occurrence_id,
           lop.student_id,
           lop.status,
           lo.slot_id,
           lo.scheduled_at,
           u.full_name
    FROM lesson_occurrence_participant lop
    JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
    JOIN users u ON u.id = lop.student_id
    JOIN lesson_slot_student lss
      ON lss.slot_id = lo.slot_id AND lss.student_id = lop.student_id
    WHERE lss.is_active = false
      AND lo.scheduled_at > now()
      AND lop.status = 'scheduled'
    ORDER BY lo.scheduled_at
    """
)

# Явка привязана к occurrence + актору, а не к строке участника. Проверяем, что
# по этой паре ничего не отмечалось — иначе удалять нельзя, это чужая история.
_SELECT_ATTENDANCE = text(
    """
    SELECT count(*) FROM attendance_event
    WHERE occurrence_id = :occurrence_id AND actor_user_id = :student_id
    """
)

_DELETE = text("DELETE FROM lesson_occurrence_participant WHERE id = ANY(:ids)")


async def main(apply: bool) -> int:
    async with async_session_factory() as db:
        rows = (await db.execute(_SELECT_STALE)).all()
        if not rows:
            logger.info("Хвостов нет — чистить нечего.")
            return 0

        logger.info("Найдено строк: %d", len(rows))
        safe_ids: list[int] = []
        for r in rows:
            events = (
                await db.execute(
                    _SELECT_ATTENDANCE,
                    {"occurrence_id": r.occurrence_id, "student_id": r.student_id},
                )
            ).scalar_one()
            mark = "УДАЛИТЬ" if events == 0 else "ПРОПУСК (есть явка)"
            logger.info(
                "  [%s] участие id=%s · %s (id=%s) · слот %s · занятие %s · %s",
                mark, r.participant_id, r.full_name, r.student_id,
                r.slot_id, r.occurrence_id, r.scheduled_at,
            )
            if events == 0:
                safe_ids.append(r.participant_id)

        if not apply:
            logger.info("\nDry-run. Для записи: DBCHECK_OK=1 ... --apply")
            return 0

        if not safe_ids:
            logger.info("Нечего удалять — всё с историей явки.")
            return 0

        await db.execute(_DELETE, {"ids": safe_ids})
        left = (await db.execute(_SELECT_STALE)).all()
        remaining = [r.participant_id for r in left if r.participant_id in safe_ids]
        if remaining:
            await db.rollback()
            logger.error("Строки остались после удаления: %s — откат.", remaining)
            return 1
        await db.commit()
        logger.info("Удалено строк: %d. Проверка после записи: хвостов %d.",
                    len(safe_ids), len(left))
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить удаление")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
