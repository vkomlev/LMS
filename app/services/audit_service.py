"""Сервис записи append-only событий в audit_event."""
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent

logger = logging.getLogger(__name__)

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


async def log_event(
    db: AsyncSession,
    event_type: str,
    user_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Записать событие в audit_event (INSERT только, UPDATE/DELETE запрещены триггером)."""
    event = AuditEvent(
        event_type=event_type,
        user_id=user_id,
        ip=ip,
        user_agent=user_agent,
        details=details,
    )
    db.add(event)
    try:
        await db.flush()
    except Exception:
        logger.exception("audit_event flush failed для %s", event_type)
        raise
