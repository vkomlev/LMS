"""Сервис записи append-only событий в audit_event."""
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent

logger = logging.getLogger("audit")

# Y-4 event types (используются grep-friendly константами вместо сырых строк)
TEACHER_REVIEW_GRADED = "teacher.review.graded"
STUDENT_NOTIFICATION_CREATED = "student.notification.created"
STUDENT_NOTIFICATION_READ = "student.notification.read"
EMAIL_FAILED = "email.failed"

# Y-6 event types (review-loop)
TEACHER_REVIEW_REJECTED = "teacher.review.rejected"
TEACHER_REVIEW_REGRADED = "teacher.review.regraded"
METHODIST_ESCALATION_TRIGGERED = "methodist.escalation.triggered"

# Y-4 pre-S5 event types (auth role auto-assign + test session endpoint)
STUDENT_ROLE_AUTO_ASSIGNED = "student.role.auto_assigned"
AUTH_ROLE_MISSING_SELF_HEALED = "auth.role.missing_self_healed"
AUTH_TEST_SESSION_ISSUED = "auth.test.session_issued"

# tsk-297 event types (штатная правка прогресса ученика преподавателем)
TEACHER_PROGRESS_GRANTED = "teacher.progress.granted"
TEACHER_PROGRESS_REVOKED = "teacher.progress.revoked"

# tsk-335 event types (выдача попыток без ручного ввода числа + explicit-путь)
TEACHER_LIMIT_OVERRIDE = "teacher.limit.overridden"


async def log_event(
    db: AsyncSession,
    event_type: str,
    user_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Записать событие в audit_event (INSERT только, UPDATE/DELETE запрещены триггером).

    После Этапа 4 (tsk-004): обогащаем `details` текущим `request_id` из
    `RequestIDMiddleware` (если вызвано в HTTP-контексте) и дублируем
    запись в `logs/app.log` через structured logger 'audit' с теми же
    ключами. Это даёт двойной поиск (БД + grep файла) по одному
    `request_id` / `event_type`.
    """
    from app.api.middleware.request_id import get_request_id  # local-import: избежать cycle при тестах

    request_id = get_request_id()
    enriched_details: dict[str, Any] | None
    if request_id is not None:
        enriched_details = dict(details or {})
        enriched_details.setdefault("request_id", request_id)
    else:
        enriched_details = details

    event = AuditEvent(
        event_type=event_type,
        user_id=user_id,
        ip=ip,
        user_agent=user_agent,
        details=enriched_details,
    )
    db.add(event)
    try:
        await db.flush()
    except Exception:
        logger.exception(
            "audit_event flush failed для %s",
            event_type,
            extra={
                "event_type": event_type,
                "user_id": user_id,
                "ip": ip,
                "user_agent": user_agent,
                "details": enriched_details,
            },
        )
        raise

    # Дублируем в structured log — единый канал поиска для аналитики.
    # logger 'audit' с уровнем INFO; JsonFormatter подберёт все extra-поля.
    logger.info(
        "audit_event recorded",
        extra={
            "event_type": event_type,
            "user_id": user_id,
            "audit_id": event.id,
            "ip": ip,
            "user_agent": user_agent,
            "details": enriched_details,
        },
    )
