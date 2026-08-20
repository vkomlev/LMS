"""Запись критериев оценивания в задание (tsk-590).

**Почему отдельный сервис, а не общий апдейт задания.** `bulk_upsert`
перезаписывает `solution_rules` целиком значением из payload: заполнять им
критерии значило бы каждый раз пересылать эталон, штрафы и настройки
нормализации — и терять их при первой же неполной посылке (тот же класс, что
затирание `task_content` импортом). Здесь меняется РОВНО один блок правила,
остальное читается из базы и возвращается на место.

**Что здесь про безопасность ученика.** Единственная точка, где критерии
получают `status="approved"`, — `apply`, и только когда вызывающий передал
`approve=True` вместе с идентификатором человека. Модель подтвердить свою же
заготовку не может: генератор (`grading_criteria_draft`) статуса не ставит
вовсе, он всегда пишет `draft`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Tasks
from app.schemas.solution_rules import GradingCriteria, SolutionRules

logger = logging.getLogger(__name__)


class CriteriaWriteError(RuntimeError):
    """Критерии записать нельзя — текст пригоден для показа методисту."""


@dataclass(frozen=True)
class CriteriaUpdate:
    """Что пришло на запись по одному заданию.

    Списки `None` означают «не менять» — тогда правится только статус. Это
    нужно очереди вычитки: методист часто ничего не правит, а просто
    подтверждает прочитанную заготовку.
    """

    task_id: int
    must: Optional[list[str]] = None
    accept: Optional[list[str]] = None
    reject: Optional[list[str]] = None
    notes: Optional[str] = None
    #: Подтвердить критерии от имени `reviewer_id`. False оставляет черновик
    #: черновиком, даже если методист поправил текст: вычитка — отдельное
    #: осознанное действие (решение оператора 2026-08-20).
    approve: bool = False


@dataclass(frozen=True)
class CriteriaWriteResult:
    """Итог по одному заданию — для построчного отчёта пакетной загрузки."""

    task_id: int
    ok: bool
    state: Optional[str] = None
    error: Optional[str] = None


async def apply(
    db: AsyncSession,
    update: CriteriaUpdate,
    *,
    reviewer_id: Optional[int],
    origin: str = "manual",
    commit: bool = True,
) -> CriteriaWriteResult:
    """Записать критерии одного задания, не трогая остальное правило проверки.

    :param db: асинхронная сессия.
    :param update: что менять.
    :param reviewer_id: `users.id` того, кто подтверждает (нужен при
        `approve=True`).
    :param origin: происхождение текста — `manual` для правки руками,
        `import` для пакетной загрузки файлом.
    :param commit: коммитить ли здесь. Пакетная загрузка ставит False и
        коммитит один раз в конце, чтобы отчёт «принято/отклонено» и
        состояние базы совпадали.
    :returns: результат по заданию; исключение не бросается — пакетная
        загрузка обязана дойти до конца и отчитаться по каждой строке.
    """
    task = await db.scalar(select(Tasks).where(Tasks.id == update.task_id).with_for_update())
    if task is None:
        return CriteriaWriteResult(task_id=update.task_id, ok=False, error="задание не найдено")

    try:
        rules = _rules_of(task)
    except CriteriaWriteError as exc:
        return CriteriaWriteResult(task_id=update.task_id, ok=False, error=str(exc))

    current = rules.grading_criteria
    must = update.must if update.must is not None else (list(current.must) if current else [])
    if not must:
        return CriteriaWriteResult(
            task_id=update.task_id,
            ok=False,
            error="критерии без обязательных требований (must) не отличают верный ответ от неверного",
        )

    accept = update.accept if update.accept is not None else (list(current.accept) if current else [])
    reject = update.reject if update.reject is not None else (list(current.reject) if current else [])
    notes = update.notes if update.notes is not None else (current.notes if current else None)

    if update.approve and reviewer_id is None:
        return CriteriaWriteResult(
            task_id=update.task_id,
            ok=False,
            error="подтвердить критерии может только названный человек",
        )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        criteria = GradingCriteria(
            must=must,
            accept=accept,
            reject=reject,
            notes=notes,
            status="approved" if update.approve else "draft",
            # Правка человеком перебивает происхождение заготовки: дальше это
            # уже его текст, и на вычитке он не должен видеть чужую метку.
            origin=origin if update.must is not None else (current.origin if current else origin),
            generated_by_model=current.generated_by_model if current else None,
            generated_at=current.generated_at if current else None,
            reviewed_by=reviewer_id if update.approve else None,
            reviewed_at=now if update.approve else None,
            draft_warning=current.draft_warning if current else None,
        )
    except ValidationError as exc:
        return CriteriaWriteResult(task_id=update.task_id, ok=False, error=_first_error(exc))

    rules.grading_criteria = criteria
    task.solution_rules = rules.model_dump()
    await db.flush()
    if commit:
        await db.commit()
    logger.info(
        "tsk-590: критерии задания %s записаны, состояние=%s, происхождение=%s",
        update.task_id,
        criteria.status,
        criteria.origin,
    )
    return CriteriaWriteResult(task_id=update.task_id, ok=True, state=rules.criteria_state())


async def store_draft(
    db: AsyncSession,
    *,
    task_id: int,
    criteria: GradingCriteria,
    commit: bool = True,
) -> CriteriaWriteResult:
    """Положить черновик модели в задание, не затирая подтверждённые критерии.

    Отказ вместо перезаписи — намеренно: подтверждённые критерии стоили
    методисту вычитки, и молча заменить их свежей заготовкой значит потерять
    эту работу.

    :param db: асинхронная сессия.
    :param task_id: задание.
    :param criteria: черновик (`status="draft"`, `origin="ai_draft"`).
    :param commit: коммитить ли здесь.
    :returns: результат по заданию.
    """
    task = await db.scalar(select(Tasks).where(Tasks.id == task_id).with_for_update())
    if task is None:
        return CriteriaWriteResult(task_id=task_id, ok=False, error="задание не найдено")
    try:
        rules = _rules_of(task)
    except CriteriaWriteError as exc:
        return CriteriaWriteResult(task_id=task_id, ok=False, error=str(exc))

    if rules.criteria_state() == "approved":
        return CriteriaWriteResult(
            task_id=task_id,
            ok=False,
            state="approved",
            error="у задания уже есть подтверждённые критерии — черновик их не заменяет",
        )

    rules.grading_criteria = criteria
    task.solution_rules = rules.model_dump()
    await db.flush()
    if commit:
        await db.commit()
    logger.info("tsk-590: черновик критериев записан для задания %s", task_id)
    return CriteriaWriteResult(task_id=task_id, ok=True, state="draft")


def _rules_of(task: Tasks) -> SolutionRules:
    """Прочитать правило задания схемой.

    Нечитаемое правило — рабочий случай (правки в БД мимо API, прецедент
    tsk-396), и ответ на него отказ с внятным текстом, а не 500: иначе
    методист видит «ошибка сервера» там, где надо чинить данные задания.
    """
    raw: Any = task.solution_rules
    if isinstance(raw, SolutionRules):
        return raw
    if not isinstance(raw, dict):
        raise CriteriaWriteError("у задания нет правила проверки (solution_rules)")
    try:
        return SolutionRules.model_validate(raw)
    except ValidationError as exc:
        raise CriteriaWriteError(
            f"правило проверки задания не разбирается: {_first_error(exc)}"
        ) from exc


def _first_error(exc: ValidationError) -> str:
    """Первая ошибка валидации простыми словами — остальные методист увидит следом."""
    errors = exc.errors()
    if not errors:
        return "правило не прошло проверку"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
    message = str(first.get("msg", "")).removeprefix("Value error, ")
    return f"{location}: {message}" if location else message
