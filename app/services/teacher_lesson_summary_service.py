"""
Сводки преподавателя по occurrence — ПЕРЕД занятием (tsk-022) и ПОСЛЕ занятия
(tsk-410, кнопка «Подвести итоги»). Один и тот же расчёт для обеих сводок
(решение оператора 2026-07-27): SPW встраивает этот же ответ и в
разворачиваемый блок карточки occurrence (до занятия), и в результат кнопки
«Подвести итоги» (после) — формат и источники не должны разойтись между двумя
точками входа фронта.

Переиспользует:
- ``lesson_occurrence_service.get_occurrence_for_teacher`` — ownership-гейт;
- ``manual_progress_service.list_accessible_student_courses``/
  ``get_student_progress`` — дерево курса (заблокированные задания, % прогресса);
- ``help_requests_service.get_help_request_detail`` — текст открытой заявки.

Разведка (2026-07-27) подтвердила, что ни один существующий сервис не считает
"выполнено/с 1 раза/запросил помощь" за окно между занятиями и не отдаёт
"предыдущее occurrence ученика + серию пропусков подряд" — это единственные
новые SQL-агрегации модуля, `get_student_progress` — не оконный снепшот,
`get_activity_feed` поддерживает только курсор `before`.

Важно: в реальном потоке сдачи ответа (``app/api/v1/attempts.py``)
``task_results.count_retry`` НИКОГДА не передаётся явно — всегда дефолт 0,
значение не отражает номер попытки. "С первого раза" здесь считается по
факту (нет более раннего результата по этому заданию у этого ученика), а не
по колонке ``count_retry``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.repos.lesson_calendar_repository import LessonOccurrenceParticipantRepository
from app.services import help_requests_service, lesson_occurrence_service, manual_progress_service
from app.utils.task_title import humanize_task_title

#: Сколько последних occurrence ученика поднимаем для поиска предыдущего
#: занятия и подсчёта серии пропусков подряд.
_MISSED_STREAK_LOOKBACK = 12

#: Провенанс синтетических (ручных) результатов — не считаются реальной сдачей.
_MANUAL_SOURCE = "manual_teacher"

_participant_repo = LessonOccurrenceParticipantRepository()


async def _load_prev_occurrence_and_streak(
    db: AsyncSession, *, student_id: int, before: datetime,
) -> tuple[Optional[datetime], int]:
    """(конец предыдущего occurrence ученика | None, серия пропусков подряд).

    Один запрос: последние ``_MISSED_STREAK_LOOKBACK`` occurrence ученика
    (ЛЮБОЙ преподаватель) строго ДО текущего, по убыванию времени. Первая
    строка — предыдущее занятие (окно ДЗ начинается с его конца); серия
    пропусков — подряд идущие ``no_show`` от начала списка.
    """
    rows = (
        await db.execute(
            text(
                "SELECT lo.scheduled_at, lo.duration_minutes, lop.status "
                "FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lop.student_id = :student_id AND lo.scheduled_at < :before "
                "ORDER BY lo.scheduled_at DESC "
                "LIMIT :lookback"
            ),
            {"student_id": student_id, "before": before, "lookback": _MISSED_STREAK_LOOKBACK},
        )
    ).mappings().fetchall()

    if not rows:
        return None, 0

    prev = rows[0]
    window_from = prev["scheduled_at"] + timedelta(minutes=int(prev["duration_minutes"]))

    streak = 0
    for row in rows:
        if row["status"] == "no_show":
            streak += 1
        else:
            break
    return window_from, streak


async def _load_last_activity(db: AsyncSession, *, student_id: int) -> Optional[dict[str, Any]]:
    """Последнее реальное (не ручной зачёт) выполненное задание ИЛИ материал —
    что из двух свежее. Та же семантика источников, что
    ``teacher_activity_feed_service._fetch_task_solved``/``_fetch_material_studied``,
    но выборка по ОДНОМУ ученику без ACL (ownership уже проверен на occurrence)."""
    task_row = (
        await db.execute(
            text(
                "SELECT t.task_id, t.submitted_at, tk.external_uid, "
                "       tk.task_content->>'title' AS title_raw, tk.task_content->>'stem' AS stem, "
                "       c.title AS course_title "
                "FROM ( "
                "    SELECT tr.task_id, tr.submitted_at FROM task_results tr "
                "    WHERE tr.user_id = :student_id AND tr.is_correct = true "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "    ORDER BY tr.submitted_at DESC LIMIT 1 "
                ") t "
                "JOIN tasks tk ON tk.id = t.task_id "
                "LEFT JOIN courses c ON c.id = tk.course_id"
            ),
            {"student_id": student_id, "manual_source": _MANUAL_SOURCE},
        )
    ).mappings().fetchone()

    material_row = (
        await db.execute(
            text(
                "SELECT smp.material_id, smp.completed_at, m.title, c.title AS course_title "
                "FROM student_material_progress smp "
                "JOIN materials m ON m.id = smp.material_id "
                "LEFT JOIN courses c ON c.id = m.course_id "
                "WHERE smp.student_id = :student_id AND smp.status = 'completed' "
                "  AND smp.completed_at IS NOT NULL "
                "  AND smp.source IS DISTINCT FROM :manual_source "
                "ORDER BY smp.completed_at DESC LIMIT 1"
            ),
            {"student_id": student_id, "manual_source": _MANUAL_SOURCE},
        )
    ).mappings().fetchone()

    candidates: list[dict[str, Any]] = []
    if task_row is not None:
        candidates.append({
            "kind": "task",
            "title": humanize_task_title(
                task_row["task_id"], task_row["title_raw"], task_row["stem"], task_row["external_uid"],
            ),
            "course_title": task_row["course_title"],
            "timestamp": task_row["submitted_at"],
        })
    if material_row is not None:
        candidates.append({
            "kind": "material",
            "title": material_row["title"],
            "course_title": material_row["course_title"],
            "timestamp": material_row["completed_at"],
        })
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["timestamp"])


async def _load_homework_window(
    db: AsyncSession, *, student_id: int, window_from: Optional[datetime], window_to: datetime,
) -> dict[str, int]:
    """Метрики ДЗ за окно: сколько заданий сдано верно, сколько из них с
    первого раза (нет более раннего результата по этому заданию у ученика —
    ``count_retry`` не годится, см. docstring модуля), сколько заявок помощи
    создано."""
    completed_row = (
        await db.execute(
            text(
                "WITH first_success AS ( "
                "    SELECT DISTINCT ON (tr.task_id) tr.task_id, tr.submitted_at "
                "    FROM task_results tr "
                "    JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "    WHERE tr.user_id = :student_id AND tr.is_correct = true "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "      AND tr.submitted_at >= COALESCE(:window_from, '-infinity'::timestamptz) "
                "      AND tr.submitted_at <= :window_to "
                "    ORDER BY tr.task_id, tr.submitted_at ASC "
                ") "
                "SELECT COUNT(*) AS completed, "
                "       COUNT(*) FILTER ( "
                "           WHERE NOT EXISTS ( "
                "               SELECT 1 FROM task_results tr2 "
                "               WHERE tr2.user_id = :student_id AND tr2.task_id = first_success.task_id "
                "                 AND tr2.submitted_at < first_success.submitted_at "
                "           ) "
                "       ) AS first_try "
                "FROM first_success"
            ),
            {
                "student_id": student_id,
                "manual_source": _MANUAL_SOURCE,
                "window_from": window_from,
                "window_to": window_to,
            },
        )
    ).mappings().fetchone()

    help_count = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM help_requests "
                "WHERE student_id = :student_id "
                "  AND created_at >= COALESCE(:window_from, '-infinity'::timestamptz) "
                "  AND created_at <= :window_to"
            ),
            {"student_id": student_id, "window_from": window_from, "window_to": window_to},
        )
    ).scalar()

    return {
        "completed": int(completed_row["completed"] or 0) if completed_row else 0,
        "first_try": int(completed_row["first_try"] or 0) if completed_row else 0,
        "help_requested": int(help_count or 0),
    }


async def _load_open_help_requests(
    db: AsyncSession, *, teacher_id: int, student_id: int,
) -> list[dict[str, Any]]:
    """Открытые заявки помощи ЭТОГО ученика с текстом (не только счётчик) —
    `list_help_requests` не фильтрует по студенту, фильтруем в Python; заявок
    на одного ученика единицы, стоимость незначительна."""
    items, _total = await help_requests_service.list_help_requests(
        db, teacher_id, status_filter="open", limit=200, offset=0,
    )
    own = [i for i in items if i["student_id"] == student_id]
    result: list[dict[str, Any]] = []
    for item in own:
        detail, error = await help_requests_service.get_help_request_detail(
            db, item["request_id"], teacher_id,
        )
        if error is not None or detail is None:
            continue
        result.append({
            "request_id": detail["request_id"],
            "task_title": detail.get("task_title"),
            "message": detail.get("message"),
            "created_at": detail["created_at"],
        })
    return result


async def _load_course_progress_and_blocked(
    db: AsyncSession, *, current_user: CurrentUser, student_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """% прогресса по каждому доступному курсу ученика + список заблокированных
    лимитом попыток заданий (текущий снепшот, не оконный) — оба берутся из уже
    посчитанного `get_student_progress`, без новой агрегации."""
    courses = await manual_progress_service.list_accessible_student_courses(
        db, current_user, student_id,
    )
    progress: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for course in courses:
        course_id = course["course_id"]
        data = await manual_progress_service.get_student_progress(
            db, student_id=student_id, course_id=course_id,
        )
        items = data["items"]
        countable = [i for i in items if i["item_type"] != "course"]
        done = sum(1 for i in countable if i["status"] in ("PASSED", "COMPLETED", "SKIPPED"))
        total = len(countable)
        percent = round(done / total * 100) if total else 0
        progress.append({
            "course_id": course_id, "title": course["title"], "percent_complete": percent,
        })
        for i in countable:
            if i["item_type"] == "task" and i["status"] == "BLOCKED_LIMIT":
                blocked.append({
                    "task_id": i["item_id"], "title": i["title"], "course_title": course["title"],
                })
    return progress, blocked


async def get_occurrence_summary(
    db: AsyncSession,
    *,
    occurrence_id: int,
    teacher_id: int,
    current_user: CurrentUser,
    no_show_threshold_minutes: int,
) -> dict[str, Any]:
    """Сводка по всем участникам occurrence — общий источник для сводки ДО
    занятия (встраивается в карточку) и кнопки «Подвести итоги» ПОСЛЕ."""
    occurrence = await lesson_occurrence_service.get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id,
    )
    participants = await _participant_repo.list_for_occurrence(db, occurrence_id)

    now_utc = datetime.now(timezone.utc)
    threshold = timedelta(minutes=no_show_threshold_minutes)

    student_ids = [p.student_id for p in participants]
    profiles: dict[int, dict[str, Any]] = {}
    if student_ids:
        rows = (
            await db.execute(
                text("SELECT id, full_name, tg_id FROM users WHERE id = ANY(:ids)"),
                {"ids": student_ids},
            )
        ).mappings().fetchall()
        profiles = {int(r["id"]): dict(r) for r in rows}

    result_participants: list[dict[str, Any]] = []
    for p in participants:
        profile = profiles.get(p.student_id, {})
        is_overdue = p.status == "scheduled" and (occurrence.scheduled_at + threshold) < now_utc

        window_from, missed_streak = await _load_prev_occurrence_and_streak(
            db, student_id=p.student_id, before=occurrence.scheduled_at,
        )
        last_activity = await _load_last_activity(db, student_id=p.student_id)
        days_since = None
        if last_activity is not None:
            days_since = max(0, (now_utc - last_activity["timestamp"]).days)
        homework = await _load_homework_window(
            db, student_id=p.student_id, window_from=window_from, window_to=now_utc,
        )
        open_help = await _load_open_help_requests(db, teacher_id=teacher_id, student_id=p.student_id)
        course_progress, blocked_tasks = await _load_course_progress_and_blocked(
            db, current_user=current_user, student_id=p.student_id,
        )

        result_participants.append({
            "student_id": p.student_id,
            "full_name": profile.get("full_name"),
            "tg_id": profile.get("tg_id"),
            "status": p.status,
            "is_overdue": is_overdue,
            "last_activity": last_activity,
            "days_since_last_activity": days_since,
            "window_from": window_from,
            "homework": homework,
            "blocked_tasks": blocked_tasks,
            "open_help_requests": open_help,
            "missed_streak": missed_streak,
            "course_progress": course_progress,
        })

    return {
        "occurrence_id": occurrence.id,
        "is_ad_hoc": occurrence.slot_id is None,
        "window_to": now_utc,
        "participants": result_participants,
    }


__all__ = ["get_occurrence_summary"]
