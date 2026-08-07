"""Y-6: уведомления методиста об эскалациях по проверкам.

Сценарии:
- timeout: pending review старше N часов (cron, см. escalation_service);
- course_completion: студент завершил все остальные задачи курса, но имеет
  pending TA/SA_COM (event-driven, из learning_engine_service.compute_course_state).

Rate-limit: не более METHODIST_RATE_LIMIT_PER_DAY_PER_COURSE push'ей в сутки
по одному курсу всем методистам в сумме. Реализовано через UNIQUE-constraint
free key в `notifications` (kind='review_escalated' или 'course_pending_review',
по payload.course_id и payload.day-marker).

Idempotency:
- timeout: gate `task_results.metrics.escalated_at IS NOT NULL`;
- completion: gate per (student_id, course_id) — `course_state.metrics.escalated_to_methodist_at`
  (переиспользуем `student_course_state.state_meta` или ставим маркер
  на отдельной key — для MVP используем `task_results` поле escalated_at, как
  индикатор что у этого pending уже триггерилось completion-уведомление).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import audit_service, inbox_service

logger = logging.getLogger(__name__)


async def _list_methodist_user_ids(db: AsyncSession) -> list[int]:
    """Все user_id с активной ролью methodist."""
    res = await db.execute(
        text(
            "SELECT ur.user_id FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE r.name = 'methodist'"
        )
    )
    return [int(row[0]) for row in res.fetchall()]


async def _course_has_recent_methodist_push(
    db: AsyncSession,
    *,
    course_id: int,
    kind: str,
    rate_limit_per_day: int,
) -> bool:
    """True, если за последние 24h уже отправлено `rate_limit_per_day` push'ей
    с таким kind для course_id (любому методисту)."""
    res = await db.execute(
        text(
            "SELECT COUNT(*) FROM notifications n "
            "WHERE n.kind = :kind "
            "  AND n.modified_at >= now() - interval '1 day' "
            "  AND (n.payload->>'course_id')::int = :course_id"
        ),
        {"kind": kind, "course_id": int(course_id)},
    )
    cnt = int(res.scalar() or 0)
    return cnt >= rate_limit_per_day


async def escalate_pending_timeout(
    db: AsyncSession,
    *,
    result_id: int,
    task_id: int,
    student_id: int,
    course_id: int | None,
    submitted_at: datetime,
    timeout_hours: int,
    rate_limit_per_day: int,
) -> int:
    """Эскалация по timeout: отправить методистам push, пометить
    `task_results.metrics.escalated_at` для idempotency.

    Возвращает количество созданных notification (0 если rate-limit или
    нет методистов в системе).
    """
    if course_id is not None and await _course_has_recent_methodist_push(
        db,
        course_id=course_id,
        kind="review_escalated",
        rate_limit_per_day=rate_limit_per_day,
    ):
        # Mark escalated anyway чтобы не возвращаться к этому result повторно
        await db.execute(
            text(
                "UPDATE task_results SET metrics = "
                "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
                "  || jsonb_build_object('escalated_at', CAST(:ts AS text)) "
                "WHERE id = :rid"
            ),
            {"rid": int(result_id), "ts": datetime.now(timezone.utc).isoformat()},
        )
        logger.info("methodist escalation rate-limited course_id=%s", course_id)
        return 0

    methodist_ids = await _list_methodist_user_ids(db)
    if not methodist_ids:
        logger.warning(
            "Y-6 escalation: нет методистов в БД (role=methodist), rid=%s skipped",
            result_id,
        )
        await db.execute(
            text(
                "UPDATE task_results SET metrics = "
                "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
                "  || jsonb_build_object('escalated_at', CAST(:ts AS text)) "
                "WHERE id = :rid"
            ),
            {"rid": int(result_id), "ts": datetime.now(timezone.utc).isoformat()},
        )
        return 0

    payload = {
        "result_id": int(result_id),
        "task_id": int(task_id),
        "student_id": int(student_id),
        "course_id": int(course_id) if course_id is not None else None,
        "submitted_at": submitted_at.isoformat() if submitted_at else None,
        "trigger": "timeout",
        "timeout_hours": int(timeout_hours),
    }

    created = 0
    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind="review_escalated",
            title="Эскалация: проверка зависла",
            content=(
                f"Заявка на проверку №{result_id} ждёт ответа учителя более "
                f"{timeout_hours} ч. Подключитесь, если требуется."
            ),
            payload=payload,
            created_by=None,
        )
        created += 1

    await db.execute(
        text(
            "UPDATE task_results SET metrics = "
            "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
            "  || jsonb_build_object('escalated_at', CAST(:ts AS text)) "
            "WHERE id = :rid"
        ),
        {"rid": int(result_id), "ts": datetime.now(timezone.utc).isoformat()},
    )

    await audit_service.log_event(
        db,
        audit_service.METHODIST_ESCALATION_TRIGGERED,
        user_id=None,
        details={
            "result_id": int(result_id),
            "task_id": int(task_id),
            "student_id": int(student_id),
            "course_id": int(course_id) if course_id is not None else None,
            "trigger": "timeout",
            "methodist_count": len(methodist_ids),
        },
    )
    return created


async def escalate_help_request(
    db: AsyncSession,
    *,
    request_id: int,
    student_id: int,
    task_id: int,
    course_id: int | None,
) -> int:
    """tsk-303, уровень 3: заявка помощи уходит методисту после разбора.

    Вызывается, когда ученик после индивидуального разбора отметил, что всё
    ещё не понял. Заявка остаётся ОТКРЫТОЙ — методист разбирается с учеником
    лично и закрывает её сам через уже существующий
    `POST /teacher/help-requests/{id}/close` (методист входит в ACL заявок по
    роли, отдельный канал закрытия не нужен).

    В отличие от `escalate_pending_timeout`/`escalate_course_completion`
    rate-limit здесь НЕ применяется: те две запускает cron и они способны
    сыпать пачками, а тут — осознанное действие ученика, ровно одно на заявку
    (повторную оценку сервис не принимает).

    Возвращает число созданных уведомлений (0, если методистов в системе нет).
    """
    methodist_ids = await _list_methodist_user_ids(db)
    if not methodist_ids:
        logger.warning(
            "tsk-303: нет методистов в БД (role=methodist), эскалация заявки %s не доставлена",
            request_id,
        )
        return 0

    payload = {
        "request_id": int(request_id),
        "student_id": int(student_id),
        "task_id": int(task_id),
        "course_id": int(course_id) if course_id is not None else None,
        "trigger": "individual_review_not_understood",
    }
    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind="help_request_escalated",
            title="Ученику не помог индивидуальный разбор",
            content=(
                f"Заявка №{request_id}: после разбора с преподавателем ученик "
                "всё ещё не разобрался. Нужен методист."
            ),
            payload=payload,
            created_by=student_id,
        )

    await audit_service.log_event(
        db,
        audit_service.METHODIST_ESCALATION_TRIGGERED,
        user_id=student_id,
        details={
            "request_id": int(request_id),
            "student_id": int(student_id),
            "task_id": int(task_id),
            "course_id": int(course_id) if course_id is not None else None,
            "trigger": "individual_review_not_understood",
            "methodist_count": len(methodist_ids),
        },
    )
    return len(methodist_ids)


async def escalate_course_completion(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    pending_result_ids: Iterable[int],
    rate_limit_per_day: int,
) -> int:
    """Эскалация course-completion: студент завершил курс, но висят pending
    TA/SA_COM. Push методистам, гард по `task_results.metrics.completion_escalated_at`
    (на каждом pending-result, чтобы повторно не триггерилось).
    """
    pending_ids = list(pending_result_ids)
    if not pending_ids:
        return 0

    # Idempotency: если ХОТЯ БЫ один из pending уже помечен `completion_escalated_at`
    # — считаем эскалацию для этой комбинации (student, course) уже сделанной.
    res = await db.execute(
        text(
            "SELECT 1 FROM task_results "
            "WHERE id = ANY(:ids) "
            "  AND metrics ? 'completion_escalated_at' "
            "LIMIT 1"
        ),
        {"ids": pending_ids},
    )
    if res.fetchone():
        logger.info(
            "course_completion escalation already triggered student=%s course=%s",
            student_id, course_id,
        )
        return 0

    if await _course_has_recent_methodist_push(
        db,
        course_id=course_id,
        kind="course_pending_review",
        rate_limit_per_day=rate_limit_per_day,
    ):
        logger.info(
            "course_completion rate-limited course=%s student=%s",
            course_id, student_id,
        )
        # Помечаем pending_ids чтобы перестать пробовать
        await db.execute(
            text(
                "UPDATE task_results SET metrics = "
                "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
                "  || jsonb_build_object('completion_escalated_at', CAST(:ts AS text)) "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": pending_ids, "ts": datetime.now(timezone.utc).isoformat()},
        )
        return 0

    methodist_ids = await _list_methodist_user_ids(db)
    if not methodist_ids:
        logger.warning(
            "course_completion escalation: нет methodist user'ов; student=%s course=%s",
            student_id, course_id,
        )
        await db.execute(
            text(
                "UPDATE task_results SET metrics = "
                "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
                "  || jsonb_build_object('completion_escalated_at', CAST(:ts AS text)) "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": pending_ids, "ts": datetime.now(timezone.utc).isoformat()},
        )
        return 0

    payload = {
        "student_id": int(student_id),
        "course_id": int(course_id),
        "pending_result_ids": pending_ids,
        "trigger": "course_completion",
    }

    created = 0
    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind="course_pending_review",
            title="Курс завершён — но есть непроверенные задания",
            content=(
                f"Студент {student_id} завершил курс {course_id}, "
                f"однако {len(pending_ids)} задач ждёт оценки учителя."
            ),
            payload=payload,
            created_by=None,
        )
        created += 1

    await db.execute(
        text(
            "UPDATE task_results SET metrics = "
            "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
            "  || jsonb_build_object('completion_escalated_at', CAST(:ts AS text)) "
            "WHERE id = ANY(:ids)"
        ),
        {"ids": pending_ids, "ts": datetime.now(timezone.utc).isoformat()},
    )

    await audit_service.log_event(
        db,
        audit_service.METHODIST_ESCALATION_TRIGGERED,
        user_id=None,
        details={
            "student_id": int(student_id),
            "course_id": int(course_id),
            "pending_count": len(pending_ids),
            "trigger": "course_completion",
            "methodist_count": len(methodist_ids),
        },
    )
    return created


async def escalate_learning_gap(
    db: AsyncSession,
    *,
    signal_id: int,
    course_id: int,
    course_title: str,
    student_id: int | None,
    student_name: str | None,
    teacher_id: int,
    wrong_percent: int,
    comment: str | None,
) -> int:
    """tsk-572: преподаватель передал методисту сигнал «нужно повторение».

    Rate-limit НЕ применяется — сознательно, по тому же основанию, что и в
    `escalate_help_request`: это осознанное действие человека, а не выстрел
    cron'а. Преподаватель нажал кнопку один раз по конкретному ученику, и
    придержать это письмо значит потерять единственный момент, когда он был
    готов рассказать, что видел на занятии.

    Комментарий преподавателя идёт ПЕРВЫМ абзацем, а не примечанием внизу:
    долю ошибок методист и так видит на своём экране, а вот «болел две недели»
    или «путает ввод и вывод, а не циклы» — это и есть причина, по которой
    письмо вообще имеет смысл.
    """
    methodist_ids = await _list_methodist_user_ids(db)
    if not methodist_ids:
        logger.warning(
            "tsk-572: некому передать сигнал %s — методистов в системе нет", signal_id
        )
        return 0

    who = student_name or (f"ученик #{student_id}" if student_id else "несколько учеников")
    lines = []
    if comment:
        lines.append(f"Преподаватель: {comment}")
    lines.append(f"{who} — «{course_title}», ошибок {wrong_percent}%.")
    lines.append("Преподаватель считает, что нужен мини-курс повторения.")

    payload = {
        "signal_id": int(signal_id),
        "course_id": int(course_id),
        "student_id": int(student_id) if student_id is not None else None,
        "teacher_id": int(teacher_id),
        "wrong_percent": int(wrong_percent),
        "trigger": "learning_gap_escalated",
    }
    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind="learning_gap_escalated",
            title="Преподаватель просит мини-курс повторения",
            content="\n".join(lines),
            payload=payload,
            created_by=teacher_id,
        )

    await audit_service.log_event(
        db,
        audit_service.METHODIST_ESCALATION_TRIGGERED,
        user_id=teacher_id,
        details={**payload, "methodist_count": len(methodist_ids)},
    )
    logger.info(
        "tsk-572: сигнал %s передан методистам (%s), ученик=%s, курс=%s",
        signal_id, len(methodist_ids), student_id, course_id,
    )
    return len(methodist_ids)
