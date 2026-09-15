"""tsk-944: вернуть ученику 4505 занятие, случайно отменённое во время живой
проверки фильтра мест (прод).

Контекст: для живой проверки гипотезы «переполненные слоты предлагаются к
записи наравне со свободными» (tsk-944) оператор зашёл под учеником 4505
(admin login-link, tsk-930) и нажал «Отменить занятие» на своём ближайшем
занятии — просто чтобы увидеть экран переноса. Гипотеза подтвердилась
(слот id=27, вт 16:00, 9 участников при пороге записи 8, реально предложен
к записи), а отменённое занятие осталось в статусе `declined` и должно
вернуться как было.

Найдено read-only через `GET /teacher/lesson-occurrences` (сессия
оператора, он же один из двух преподавателей слота): occurrence id=16544
(слот id=32, ученик 4505, ср 16.09 17:00 МСК = 14:00 UTC), участник
id=99747, `status='declined'`, `updated_at` — 15.09 (момент теста).
Соседние будущие occurrence того же ученика и слота (id=19502, id=23628)
остались нетронутыми в статусе `scheduled` — это и есть исходное состояние,
в которое нужно вернуть строку 99747.

Почему сырой UPDATE, а не `POST /lesson-occurrences/{id}/attendance`
(action=joined) или teacher `manual_present`:
- `attendance` требует сессию САМОГО ученика (ownership-проверка на
  `current_user.id`) — её решили не трогать повторно, чтобы не плодить
  новых входов под чужой учёткой;
- `manual_present` (teacher-эндпоинт) поставил бы статус `confirmed`
  (а не `scheduled`, как у соседних occurrence) и, что важнее,
  автоматически запустил бы `homework_service.auto_issue_after_lesson` —
  прод-настройка `homework_auto_issue_enabled` включена оператором
  01.09.2026, и это реально выдало бы ученику домашнюю работу за занятие,
  которое ещё не было (16.09, в будущем). Такого побочного эффекта здесь
  быть не должно.

Протокол (`/db-check`, режим записи):
    python scripts/fix_tsk944_restore_declined_lesson.py            # сухой прогон
    DBCHECK_OK=1 python scripts/fix_tsk944_restore_declined_lesson.py --apply

Запускать на сервере под `app` (тот же R-009 operator-runbook, что и у
`fix_third_slot_tsk832.py`) — из локальной сессии до боевой БД нет прямого
сетевого доступа:
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/fix_tsk944_restore_declined_lesson.py --apply"'
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk944.fix")

PARTICIPANT_ID = 99747
OCCURRENCE_ID = 16544
STUDENT_ID = 4505


async def _row(db) -> dict | None:
    r = (
        await db.execute(
            text(
                "SELECT p.id, p.occurrence_id, p.student_id, p.status, p.updated_at, "
                "       o.scheduled_at "
                "  FROM lesson_occurrence_participant p "
                "  JOIN lesson_occurrence o ON o.id = p.occurrence_id "
                " WHERE p.id = :pid"
            ),
            {"pid": PARTICIPANT_ID},
        )
    ).first()
    if r is None:
        return None
    return {
        "id": r.id,
        "occurrence_id": r.occurrence_id,
        "student_id": r.student_id,
        "status": r.status,
        "updated_at": r.updated_at,
        "scheduled_at": r.scheduled_at,
    }


async def main(apply: bool) -> int:
    async with async_session_factory() as db:
        before = await _row(db)
        if before is None:
            logger.error("Строка участника id=%s не найдена — останов", PARTICIPANT_ID)
            return 2
        logger.info("До правки: %s", before)

        if before["occurrence_id"] != OCCURRENCE_ID or before["student_id"] != STUDENT_ID:
            logger.error(
                "Строка %s указывает на occurrence=%s/student=%s, а в плане "
                "occurrence=%s/student=%s — останов",
                PARTICIPANT_ID, before["occurrence_id"], before["student_id"],
                OCCURRENCE_ID, STUDENT_ID,
            )
            return 2

        if before["status"] != "declined":
            logger.error(
                "Статус строки уже не 'declined' (сейчас %r) — кто-то поправил "
                "раньше, останов без записи",
                before["status"],
            )
            return 2

        logger.info(
            "План: participant id=%s occurrence=%s (ученик %s, занятие %s) — "
            "'declined' -> 'scheduled'",
            PARTICIPANT_ID, OCCURRENCE_ID, STUDENT_ID, before["scheduled_at"],
        )

        if not apply:
            logger.info("Сухой прогон. Записи не было. Повторить с --apply.")
            return 0

        await db.execute(
            text(
                "UPDATE lesson_occurrence_participant "
                "   SET status = 'scheduled', updated_at = now() "
                " WHERE id = :pid AND status = 'declined'"
            ),
            {"pid": PARTICIPANT_ID},
        )
        await db.commit()

        after = await _row(db)
        logger.info("После правки: %s", after)
        ok = after is not None and after["status"] == "scheduled"
        logger.info("Итог: %s", "ОК" if ok else "ПРОБЛЕМА — статус не 'scheduled'")
        return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
