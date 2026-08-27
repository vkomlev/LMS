"""Сервис guest-mode (Phase Y-5).

ACL: только курсы с `courses.is_public_demo=TRUE` доступны гостям без auth.
Из guest-payload явным whitelist'ом исключены поля `correct_answer`,
`solution_rules`, `is_correct` для опций — защита от слива ответов.

См. tech-spec Y-5 §6.2.
"""
from __future__ import annotations

import asyncio
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


def is_task_visible_to_guest(task: Tasks, *, surface: str) -> bool:
    """Пустить гостя только к АКТИВНОМУ заданию демо-курса (tsk-702).

    Хвост линии tsk-695 (материал) → tsk-697 (одна ручка задания) → tsk-699
    (список заданий курса) → tsk-701 (приём ответа). Там закрыли УЧЕНИКА, на
    чтении и на записи. Гостевой контур ходит своей веткой и ученических гейтов
    не касается вовсе: обе выборки здесь и обе выборки embed-API фильтровали
    только по `courses.is_public_demo`, но не по `tasks.is_active`. То есть
    снятое с публикации задание открывалось анонимно, без всякой авторизации, и
    принимало ответ. На проде 26.08.2026 — 63 выключенных задания из 4146 в
    публичных демо-курсах.

    Витрина от фильтра не пустеет: выключенные задания есть всего у 2 демо-курсов
    из 464 (1179 `wp:oge-z14` — 50 из 78, 1253 `wp:vst-it-r1-t4` — 13 из 26), в
    обоих активные остаются. Гостевых попыток на проде 12, по выключенным
    заданиям — ни одной, чистить нечего.

    Ролей и привилегий в гостевом контуре нет (это анонимный вызывающий), поэтому
    исключения `privileged`, как у `_deny_if_inactive_for_student` (tsk-697) и
    `assert_task_active_for_student` (tsk-701), здесь нет: методист смотрит
    выключенное задание из своего кабинета, под своей учётной записью.

    Предикат, а не raise: у четырёх точек вызова разные способы сказать «не
    найдено» — `None` → 404 роутера в чтении гостя, `DomainError` 404 в приёме
    ответа, `HTTPException` 404 с двумя разными текстами в embed. Общее у них —
    решение и запись в журнал, они и живут здесь.

    Отказ логируется: 63 выключенных задания встроены ссылками на страницы WP, и
    строка в журнале — единственный способ узнать, какая витринная страница
    показывает погасший блок.

    :param task: строка `tasks` (нужны `is_active`, `id`, `course_id`).
    :param surface: точка вызова для журнала (`read` / `submit` / `embed_issue` /
        `embed_read`).
    :return: True — задание можно отдавать гостю.
    """
    if task.is_active:
        return True
    logger.info(
        "tsk-702: deny guest %s task_id=%s course_id=%s (is_active=false)",
        surface, task.id, task.course_id,
    )
    return False


async def _enforce_demo_task_limit(
    db: AsyncSession,
    guest_session_id: Optional[UUID],
    course_id: int,
    task_id: int,
    limit: Optional[int],
) -> None:
    """Проверить лимит "сколько РАЗНЫХ заданий этого курса гость уже проверял" (tsk-423).

    NULL `limit` (по умолчанию) — без лимита, ничего не проверяем (курс 651
    «Пробное занятие» и любой другой курс без настроенного лимита не затронуты).
    Без `guest_session_id` (гость ещё не создал сессию — например, первый GET
    задания до POST /learning/guest/session) считать историю не по чему —
    трактуем как «использовано 0», не блокируем.

    Повторная проверка УЖЕ использованного `task_id` в лимит не считается —
    иначе гость терял бы доступ к своей же задаче после ошибочного первого
    ответа.

    Raises:
        DomainError 403 (payload.code=demo_limit_reached): лимит исчерпан для
            НОВОГО (ранее не встречавшегося) задания.
    """
    if limit is None or guest_session_id is None:
        return

    from app.models.guest_attempt import GuestAttempt  # noqa: PLC0415

    result = await db.execute(
        select(GuestAttempt.task_id)
        .join(Tasks, Tasks.id == GuestAttempt.task_id)
        .where(
            GuestAttempt.guest_session_id == guest_session_id,
            Tasks.course_id == course_id,
        )
        .distinct()
    )
    used_ids = {row[0] for row in result if row[0] is not None}
    if task_id in used_ids:
        return
    if len(used_ids) >= limit:
        raise DomainError(
            detail="Демо-лимит заданий исчерпан. Зарегистрируйтесь или купите курс целиком.",
            status_code=403,
            payload={"code": "demo_limit_reached", "limit": limit, "used": len(used_ids)},
        )


async def _demo_tasks_used(
    db: AsyncSession, guest_session_id: Optional[UUID], course_id: int
) -> int:
    """Сколько РАЗНЫХ заданий курса гость уже проверял.

    Считается тем же способом, что и в `_enforce_demo_task_limit`: иначе счётчик
    на экране и лимит на сервере разошлись бы, и человек упирался бы в стену,
    видя перед собой «осталось 2».
    """
    if guest_session_id is None:
        return 0

    from app.models.guest_attempt import GuestAttempt  # noqa: PLC0415

    result = await db.execute(
        select(GuestAttempt.task_id)
        .join(Tasks, Tasks.id == GuestAttempt.task_id)
        .where(
            GuestAttempt.guest_session_id == guest_session_id,
            Tasks.course_id == course_id,
        )
        .distinct()
    )
    return len({row[0] for row in result if row[0] is not None})


async def get_demo_course_info(
    db: AsyncSession, course_uid: str, guest_session_id: Optional[UUID] = None
) -> Optional[GuestCourseInfoResponse]:
    """Вернуть info о demo-курсе или None если не существует / не публичный.

    tsk-301: вместе с курсом отдаём остаток демо-лимита. До этого гость узнавал о
    лимите, только упёршись в него, — то есть в худший момент для первого
    разговора о цене.
    """
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
        demo_task_limit=course.demo_task_limit,
        demo_tasks_used=await _demo_tasks_used(db, guest_session_id, course.id),
    )


async def get_demo_task(
    db: AsyncSession, task_id: int, guest_session_id: Optional[UUID] = None
) -> Optional[GuestTaskResponse]:
    """Загрузить задачу из public-demo курса; вернуть None если task не в demo.

    Sanitizes payload: возвращает только whitelist полей (без correct_answer,
    solution_rules, options[].is_correct, options[].explanation).

    tsk-702: выключенное задание (`is_active = false`) гостю не отдаётся —
    см. `is_task_visible_to_guest`. Проверка идёт ДО демо-лимита: снятое с
    публикации задание не должно ни расходовать лимит, ни отвечать про него.

    Raises:
        DomainError 403 (tsk-423): курс настроен с `demo_task_limit`, гость его
            исчерпал, и запрошенное задание — новое (не из уже использованных).
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
    if row is None or not is_task_visible_to_guest(row[0], surface="read"):
        return None
    task, course = row

    await _enforce_demo_task_limit(db, guest_session_id, course.id, task.id, course.demo_task_limit)

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

    tsk-702: выключенное задание (`is_active = false`) ответ гостя не принимает —
    тот же 404, что и «задания нет среди публичных демо». Проверка ДО демо-лимита
    и до проверки ответа.

    Raises:
        DomainError 400: task не в public-demo / SA_COM / type mismatch.
        DomainError 403 (tsk-423): `demo_task_limit` курса исчерпан для нового
            задания (payload.code=demo_limit_reached).
    """
    # 1. Проверить ACL: task ∈ public-demo course + задание не снято с публикации
    result = await db.execute(
        select(Tasks, Courses)
        .join(Courses, Tasks.course_id == Courses.id)
        .where(
            Tasks.id == task_id,
            Courses.is_public_demo.is_(True),
        )
    )
    row = result.first()
    if row is None or not is_task_visible_to_guest(row[0], surface="submit"):
        raise DomainError(
            detail="Задача не найдена среди публичных демо-курсов.",
            status_code=404,
            payload={"task_id": task_id},
        )
    task, course = row

    # 1b. Лимит гостевых заданий на курс (tsk-423) — до проверки ответа,
    # чтобы не тратить checking_service на заведомо заблокированную попытку.
    await _enforce_demo_task_limit(db, guest_session_id, course.id, task.id, course.demo_task_limit)

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
    # (asyncio.to_thread — на случай turtle_sim/tsk-412, блокирующий вызов
    # песочницы; для SA/SC/MC guest-типов это по-прежнему быстрый sync-путь)
    check_result = await asyncio.to_thread(
        _checking_service.check_task,
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
