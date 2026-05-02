"""Сервис guest-mode (Phase Y-5).

ACL: только курсы с `courses.is_public_demo=TRUE` доступны гостям без auth.
Из guest-payload явным whitelist'ом исключены поля `correct_answer`,
`solution_rules`, `is_correct` для опций — защита от слива ответов.

См. tech-spec Y-5 §6.2.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.tasks import Tasks
from app.schemas.checking import CheckResult, StudentAnswer
from app.schemas.learning_guest import (
    GuestCourseInfoResponse,
    GuestTaskOption,
    GuestTaskResponse,
)
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_GUEST_ALLOWED_TYPES: tuple[str, ...] = ("SA", "SC", "MC")
_checking_service = CheckingService()


async def get_demo_course_info(
    db: AsyncSession, course_uid: str
) -> Optional[GuestCourseInfoResponse]:
    """Вернуть info о demo-курсе или None если не существует / не публичный."""
    result = await db.execute(
        select(Courses).where(
            Courses.course_uid == course_uid,
            Courses.is_public_demo.is_(True),
        )
    )
    course = result.scalar_one_or_none()
    if course is None:
        return None
    return GuestCourseInfoResponse(
        course_uid=course.course_uid or "",
        title=course.title,
        is_public_demo=True,
    )


async def get_demo_task(db: AsyncSession, task_id: int) -> Optional[GuestTaskResponse]:
    """Загрузить задачу из public-demo курса; вернуть None если task не в demo.

    Sanitizes payload: возвращает только whitelist полей (без correct_answer,
    solution_rules, options[].is_correct, options[].explanation).
    """
    result = await db.execute(
        select(Tasks, Courses)
        .join(Courses, Tasks.course_id == Courses.id)
        .where(
            Tasks.id == task_id,
            Courses.is_public_demo.is_(True),
        )
    )
    row = result.first()
    if row is None:
        return None
    task, course = row

    try:
        content = TaskContent.model_validate(task.task_content)
    except Exception:
        logger.warning(
            "guest.get_demo_task: некорректный task_content для task_id=%s",
            task_id,
        )
        return None

    if content.type not in _GUEST_ALLOWED_TYPES:
        # SA_COM/TA не отдаём гостям — нет teacher review без user
        return None

    options: Optional[list[GuestTaskOption]] = None
    if content.type in ("SC", "MC") and content.options:
        options = [
            GuestTaskOption(id=opt.id, text=opt.text)
            for opt in content.options
            if opt.is_active
        ]

    return GuestTaskResponse(
        task_id=task.id,
        external_uid=task.external_uid,
        course_id=course.id,
        course_uid=course.course_uid,
        type=content.type,  # type: ignore[arg-type]
        stem=content.stem,
        options=options,
        max_score=task.max_score,
        max_attempts=task.max_attempts,
    )


async def submit_guest_attempt(
    db: AsyncSession,
    guest_session_id: UUID,
    task_id: int,
    answer: StudentAnswer,
) -> tuple[int, CheckResult]:
    """Проверить ответ гостя и записать guest_attempt.

    Returns:
        (attempt_id, CheckResult) — id новой записи + результат проверки.

    Raises:
        DomainError 400: task не в public-demo / SA_COM / type mismatch.
    """
    # 1. Проверить ACL: task ∈ public-demo course
    result = await db.execute(
        select(Tasks, Courses)
        .join(Courses, Tasks.course_id == Courses.id)
        .where(
            Tasks.id == task_id,
            Courses.is_public_demo.is_(True),
        )
    )
    row = result.first()
    if row is None:
        raise DomainError(
            detail="Задача не найдена среди публичных демо-курсов.",
            status_code=404,
            payload={"task_id": task_id},
        )
    task, _course = row

    # 2. Валидировать тип задачи (SA/SC/MC only)
    try:
        content = TaskContent.model_validate(task.task_content)
        rules = SolutionRules.model_validate(task.solution_rules or {})
    except Exception as exc:
        logger.warning(
            "guest.submit_guest_attempt: невалидный task_content/solution_rules task_id=%s: %s",
            task_id,
            exc,
        )
        raise DomainError(
            detail="Структура задачи повреждена.",
            status_code=400,
        ) from exc

    if content.type not in _GUEST_ALLOWED_TYPES:
        raise DomainError(
            detail="Тип задачи не поддерживается в гостевом режиме.",
            status_code=400,
            payload={"task_type": content.type},
        )

    if answer.type not in _GUEST_ALLOWED_TYPES:
        raise DomainError(
            detail="В гостевом режиме разрешены только типы ответов SA/SC/MC.",
            status_code=400,
            payload={"answer_type": answer.type},
        )

    # 3. Проверить ответ через тот же checking_service что в /attempts
    check_result = _checking_service.check_task(
        task_content=content,
        solution_rules=rules,
        answer=answer,
    )

    # 4. INSERT guest_attempt (импорт здесь, чтобы избежать circular)
    from app.models.guest_attempt import GuestAttempt  # noqa: PLC0415

    attempt = GuestAttempt(
        guest_session_id=guest_session_id,
        task_id=task.id,
        answer_json=answer.model_dump(mode="json"),
        is_correct=bool(check_result.is_correct),
    )
    db.add(attempt)
    await db.flush()
    return attempt.id, check_result
