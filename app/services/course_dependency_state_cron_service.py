# app/services/course_dependency_state_cron_service.py
"""tsk-541: фоновый пересчёт `student_course_state` для целей `course_dependencies`.

**Зачем.** `student_course_state` — кеш прогресса, на который смотрит
`me_service._BLOCKED_COURSES_SQL`: подкурс считается заблокированным, если в
кеше НЕТ строки `state='COMPLETED'` для его `required_course_id`. Строку
пишет только `LearningEngineService.compute_course_state(update_state_table=
True)`, а её вызывают только `resolve_next_item` (зависимости КОРНЯ) и
`manual_progress_service._refresh_course_state` (состояние корня
touched-узла) — оба пути пишут кеш ТОЛЬКО для корневых курсов. Если
`course_dependencies` ссылается на ПОДКУРС (`course_id`/`required_course_id`
внутри одного дерева, не корень), кеш для него не пишет никто фоново — новая
зависимость молча блокирует всех активных студентов по этому подкурсу, даже
уже прошедших пререквизит (tsk-523: 34 студента × 10 подкурсов курса 88,
340 строк, обнаружено только живой проверкой после деплоя).

`CourseDependenciesService.add_dependency`/`bulk_add_dependencies` теперь
делают синхронный бэкфилл при записи через API (см. `learning_engine_service.
backfill_dependency_state`) — но `course_dependencies` для курса 88 в tsk-523
были записаны ПРЯМЫМ SQL-скриптом в обход API/сервисного слоя (см.
`reviews/2026-08-02-tsk523-course88-fixes.md`, `tsk523_apply.py`) — то есть
для этого, ключевого по факту прецедента, пути записи, синхронный бэкфилл в
сервисе НЕ сработал бы. Этот тик — единственная защита, не зависящая от того,
как физически появилась строка `course_dependencies` (API, прямой SQL,
будущий импорт из ContentBackbone).

Паттерн периодического тика и advisory-lock — тот же, что у соседних сервисов
(`escalation_service`, `lesson_attendance_cron_service`, `link_audit_service`):
один worker за тик делает работу, остальные отступают мгновенно.

tsk-626: тик пишет по ученику и коммитит после каждого, а не копит весь проход
в одной транзакции. Замер на проде 17.08.2026: 460 строк кеша по 40 ученикам
имели одинаковый `updated_at`, то есть один тик держал 460 блокировок строк до
самого конца прохода. Параллельный `GET /learning/next-item` того же ученика
захватывал свои строки в другом порядке — 17.08 в 12:56 UTC это дало
`DeadlockDetectedError` и 500 ученику. Блокировка одного worker'а поэтому
переведена с транзакционной на сессионную: коммит внутри прохода снял бы
транзакционную, и два worker'а пошли бы одновременно.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services.learning_engine_service import LearningEngineService

logger = logging.getLogger("app.course_dependency_state_cron")

# ascii "CDST" (Course-Dependency STate) — не пересекается с соседними
# ключами: Y-6 (0x59365453), генератор occurrence (0x4C534E43), attendance
# (0x4C534E41), link_audit (0x4C494E4B).
_COURSE_DEPENDENCY_STATE_LOCK_KEY = 0x43445354

_scheduler: Optional[AsyncIOScheduler] = None

_engine = LearningEngineService()


async def course_dependency_state_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход. Возвращает summary для логов/тестов.

    Для каждой различной пары (course_id, required_course_id) из
    `course_dependencies` находит активных студентов, у кого `course_id`
    входит в дерево активного корня, и пересчитывает
    `student_course_state[required_course_id]` для каждого из них.
    Пары с общим `required_course_id` дедуплицируются по множеству студентов
    до пересчёта — один и тот же кеш не считается дважды за тик, даже если
    несколько курсов ссылаются на один и тот же пререквизит.

    tsk-626: запись идёт по ученикам в порядке возрастания `student_id`, и
    после каждого ученика — коммит. Прерванный тик поэтому оставляет часть
    учеников непересчитанной; для кеша это допустимо, следующий тик доберёт
    остальных.
    """
    factory = session_factory or async_session_factory
    summary = {"locked": False, "pairs_checked": 0, "students_recomputed": 0}

    async with factory() as db:
        # tsk-626: блокировка СЕССИОННАЯ, не транзакционная — ниже тик коммитит
        # после каждого ученика, и транзакционная слетела бы на первом же
        # коммите, пустив второго worker'а в тот же проход.
        got_row = await db.execute(
            text("SELECT pg_try_advisory_lock(:k) AS locked"),
            {"k": _COURSE_DEPENDENCY_STATE_LOCK_KEY},
        )
        if not bool(got_row.scalar()):
            logger.debug("tsk-541: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        try:
            pairs = (
                await db.execute(
                    text("SELECT DISTINCT course_id, required_course_id FROM course_dependencies")
                )
            ).fetchall()
            summary["pairs_checked"] = len(pairs)
            if not pairs:
                return summary

            needed: Dict[int, Set[int]] = {}
            for course_id, required_course_id in pairs:
                student_ids = await _engine.list_active_students_with_node_in_tree(
                    db, int(course_id)
                )
                needed.setdefault(int(required_course_id), set()).update(student_ids)

            # tsk-626: раскладываем по ученикам и обходим по возрастанию
            # `student_id`, курсы внутри ученика — по возрастанию `course_id`.
            # Прежний обход шёл по курсам, а внутри — по МНОЖЕСТВУ учеников,
            # то есть в порядке хеша: он менялся от прогона к прогону, и
            # согласовать его с порядком любого другого писателя было нельзя
            # в принципе.
            by_student: Dict[int, Set[int]] = {}
            for required_course_id, needed_for in needed.items():
                for student_id in needed_for:
                    by_student.setdefault(int(student_id), set()).add(
                        int(required_course_id)
                    )
            plan: List[Tuple[int, List[int]]] = [
                (sid, sorted(by_student[sid])) for sid in sorted(by_student)
            ]

            for student_id, course_ids in plan:
                for required_course_id in course_ids:
                    await _engine.compute_course_state(
                        db, student_id, required_course_id, update_state_table=True
                    )
                    summary["students_recomputed"] += 1
                # tsk-626: коммит на ученика. Блокировки его строк живут ровно
                # столько, сколько считается он один, а не весь проход —
                # параллельный next-item ждёт миллисекунды вместо всего тика.
                await db.commit()

            logger.info(
                "tsk-541 course_dependency_state_cron_tick done pairs=%s recomputed=%s students=%s",
                summary["pairs_checked"], summary["students_recomputed"], len(plan),
            )
        finally:
            # Сессионную блокировку обязан снять тот, кто взял: сама она
            # доживёт до закрытия соединения, а соединение возвращается в пул.
            await db.rollback()
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": _COURSE_DEPENDENCY_STATE_LOCK_KEY},
            )
            await db.commit()

    return summary


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднимает периодический тик (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.course_dependency_state_cron_enabled:
        logger.info(
            "tsk-541: тик пересчёта student_course_state выключен "
            "(COURSE_DEPENDENCY_STATE_CRON_ENABLED)"
        )
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    interval_min = int(settings.course_dependency_state_cron_interval_min)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        course_dependency_state_cron_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="tsk541_course_dependency_state_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-541 course_dependency_state scheduler started: interval=%smin", interval_min
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-541 course_dependency_state scheduler stopped")
    _scheduler = None
