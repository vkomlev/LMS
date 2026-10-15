"""API штатной правки прогресса ученика преподавателем (tsk-297).

Базовый префикс: ``/api/v1/teacher/students/{student_id}/progress``.

* ``GET    ?course_id=``            — дерево курса ученика со статусами и флагом `manual`
* ``POST   /tasks/{task_id}``       — зачесть задание
* ``DELETE /tasks/{task_id}``       — снять зачёт задания
* ``POST   /materials/{id}``        — отметить материал пройденным
* ``DELETE /materials/{id}``        — снять отметку материала
* ``POST   /courses/{course_id}``   — массово зачесть дерево узла
* ``DELETE /courses/{course_id}``   — массово снять зачёты в дереве узла

Гейт: роль ``teacher`` / ``methodist`` / ``admin`` (или сервисный токен) плюс
scoped-ACL `ensure_can_edit_progress` — наличия роли мало, teacher правит только
своих учеников или учеников на закреплённых за ним курсах.

Все операции идемпотентны: повторный вызов не создаёт дубль и не ошибается —
возвращает ``already=true``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.code_review import CodeReviewBadge
from app.schemas.teacher_help_requests import HelpRequestType
from app.schemas.content_requirement import RequirementReason
from app.services import manual_progress_service, requirement_override_service

logger = logging.getLogger("api.teacher_progress")

router = APIRouter(tags=["teacher_progress"])

_PROGRESS_GATE = require_role("teacher", "methodist", "admin")
_BASE = "/teacher/students/{student_id}/progress"


# ─── Схемы ──────────────────────────────────────────────────────────────────


class ProgressGrantRequest(BaseModel):
    """Тело POST-операций: необязательная причина/пояснение преподавателя."""

    comment: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Причина правки прогресса (до 500 символов), попадает в аудит",
    )


class ProgressItemResponse(BaseModel):
    """Ответ единичной операции над заданием/материалом."""

    student_id: int
    item_type: Literal["task", "material"]
    item_id: int
    granted: bool = Field(description="True — элемент отмечен пройденным, False — отметка снята")
    already: bool = Field(description="True — состояние уже было таким, ничего не менялось")
    source: str = Field(description="Провенанс отметки (`manual_teacher`)")
    attempt_id: Optional[int] = Field(
        default=None, description="ID созданной синтетической попытки (только для задания)"
    )


class ProgressSkipResponse(BaseModel):
    """Ответ операции «пропустить / вернуть задание ученику» (tsk-919)."""

    student_id: int
    item_type: Literal["task"]
    item_id: int
    skipped: bool = Field(description="True — задание пропущено для ученика, False — возвращено")
    already: bool = Field(description="True — состояние уже было таким, ничего не менялось")


class ProgressBulkResponse(BaseModel):
    """Ответ массовой операции по дереву узла."""

    student_id: int
    course_id: int
    tasks_affected: int
    materials_affected: int
    skipped_already: int
    skipped_quiz: int = Field(
        default=0,
        description=(
            "Квиз-вопросы дерева, пропущенные при массовом зачёте: их нельзя "
            "зачесть вручную (диагностика). У операции снятия всегда 0"
        ),
    )


class ProgressTreeItem(BaseModel):
    """Элемент дерева курса в карточке ученика.

    Порядок элементов в ответе — учебный (post-order обход дерева движком плюс
    ``order_position`` внутри узла); клиент его не пересортировывает.
    """

    item_type: Literal["course", "task", "material"]
    item_id: int
    course_id: int
    parent_course_id: Optional[int] = Field(
        default=None,
        description="Узел, которому элемент принадлежит; null у запрошенного корня",
    )
    title: Optional[str] = None
    full_title: Optional[str] = Field(
        default=None,
        description=(
            "Задание: полное условие (stem), без обрезки под ширину строки "
            "(в отличие от `title`) — для всплывающей подсказки при наведении. "
            "У материалов/узлов курса всегда null (их title уже не обрезан)."
        ),
    )
    status: str = Field(
        description=(
            "Задание: OPEN | IN_PROGRESS | FAILED | PASSED | BLOCKED_LIMIT. "
            "Материал: NOT_STARTED | COMPLETED | SKIPPED. "
            "Узел курса: NOT_STARTED | IN_PROGRESS | COMPLETED"
        )
    )
    manual: Optional[bool] = Field(
        default=None,
        description="True — отметка поставлена вручную; у узлов курса всегда null",
    )
    requirement_level: Optional[str] = Field(
        default=None,
        description=(
            "tsk-1361: итоговый уровень для ЭТОГО ученика (required|recommended|"
            "skippable); у узлов курса null"
        ),
    )
    course_requirement_level: Optional[str] = Field(
        default=None, description="tsk-1361: уровень в курсе; у узлов курса null"
    )
    requirement_reason: Optional[RequirementReason] = Field(
        default=None,
        description=(
            "tsk-1361: почему уровень ученика отличается от курса. Решение "
            "преподавателя отдаётся всегда, даже если пока ничего не меняет"
        ),
    )
    manual_grantable: bool = Field(
        default=True,
        description=(
            "False — ручной зачёт этому элементу запрещён (квиз-вопрос SC_Qw/MC_Qw: "
            "диагностика, ученик проходит её сам). Кнопку зачёта скрывать по этому "
            "флагу; снятие уже стоящей отметки он не ограничивает"
        ),
    )
    granted_by: Optional[int] = Field(
        default=None, description="Кто поставил ручную отметку (у материалов всегда null)"
    )
    granted_at: Optional[datetime] = None
    attempts_used: Optional[int] = Field(
        default=None, description="Задание: сколько попыток уже потрачено"
    )
    attempts_limit_effective: Optional[int] = Field(
        default=None, description="Задание: текущий эффективный лимит попыток"
    )
    last_submitted_at: Optional[datetime] = Field(
        default=None,
        description=(
            "tsk-1090: задание — когда ученик сдал последнюю попытку; null — "
            "не сдавал или последняя запись это ручной зачёт"
        ),
    )
    open_help_request_id: Optional[int] = Field(
        default=None,
        description="ID открытой заявки help_requests по заданию (последняя по created_at); null — заявок нет",
    )
    open_help_request_type: Optional[HelpRequestType] = Field(
        default=None,
        description="Тип открытой заявки; null — заявок нет",
    )
    pending_review: bool = Field(
        default=False,
        description="Задание: последний результат SA_COM/TA сдан и ждёт ручной проверки (checked_at IS NULL)",
    )
    returned_for_rework: bool = Field(
        default=False,
        description="Задание: есть непрочитанное уведомление 'возвращено на доработку'",
    )
    needs_attention: bool = Field(
        default=False,
        description=(
            "Требует внимания: BLOCKED_LIMIT, открытая заявка помощи, ожидает "
            "ручной проверки, или возвращено на доработку (tsk-336). У узла "
            "course — OR по всему поддереву"
        ),
    )
    code_review: Optional[CodeReviewBadge] = Field(
        default=None,
        description=(
            "tsk-302: машинная оценка кода по ПОСЛЕДНЕЙ попытке — компактный вид "
            "для значка в списке. Только у узлов task с кодовым заданием; у курсов, "
            "материалов и обычных заданий null. Ученику этот экран недоступен"
        ),
    )


class StudentCourseRef(BaseModel):
    """Курс ученика для селектора в карточке."""

    course_id: int
    title: Optional[str] = None
    is_current: bool = Field(
        default=False,
        description=(
            "Курс, над которым ученик работает сейчас (последняя активность "
            "по дереву курса, tsk-913/tsk-917) — ровно один True в списке"
        ),
    )


class ProgressTreeResponse(BaseModel):
    """Прогресс ученика по дереву курса + список доступных курсов для селектора."""

    student_id: int
    course_id: Optional[int] = Field(
        default=None, description="Запрошенный узел; null, если course_id не передан"
    )
    courses: list[StudentCourseRef] = Field(
        default_factory=list,
        description="Активные курсы ученика, доступные этому преподавателю",
    )
    items: list[ProgressTreeItem] = Field(
        default_factory=list,
        description="Плоское дерево в учебном порядке; пусто, если course_id не передан",
    )


# ─── Вспомогательное ────────────────────────────────────────────────────────


async def _course_of_task(db: AsyncSession, task_id: int) -> int:
    """Курс задания (нужен для scoped-ACL до самой операции). 404, если нет."""
    row = (
        await db.execute(
            text("SELECT course_id FROM tasks WHERE id = :task_id"), {"task_id": task_id}
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено")
    return int(row[0])


async def _course_of_material(db: AsyncSession, material_id: int) -> int:
    """Курс материала (нужен для scoped-ACL до самой операции). 404, если нет."""
    row = (
        await db.execute(
            text("SELECT course_id FROM materials WHERE id = :material_id"),
            {"material_id": material_id},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Материал {material_id} не найден")
    return int(row[0])


def _actor_id(current_user: CurrentUser) -> Optional[int]:
    """ID автора правки; для сервисного токена — None (пользователя нет)."""
    return None if current_user.is_service else current_user.id


# ─── Эндпоинты ──────────────────────────────────────────────────────────────


@router.get(
    _BASE,
    response_model=ProgressTreeResponse,
    summary="Прогресс ученика по дереву курса (для преподавателя)",
)
async def get_progress(
    student_id: int = Path(..., ge=1, description="ID ученика"),
    course_id: Optional[int] = Query(
        default=None,
        ge=1,
        description="ID узла курса (берётся всё его дерево). Без него вернётся только список курсов",
    ),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressTreeResponse:
    """Отдать дерево курса со статусами элементов, флагом ручной отметки и селектор курсов.

    Без ``course_id`` отдаётся только ``courses`` (ACL-фильтрованный список
    курсов ученика), ``items`` пуст: это первый запрос карточки, когда курс ещё
    не выбран. Ошибку доступа в этом режиме не бросаем — преподаватель просто
    увидит пустой список, если ни один курс ученика ему не доступен.
    """
    courses = await manual_progress_service.list_accessible_student_courses(
        db, current_user, student_id
    )
    if course_id is None:
        return ProgressTreeResponse(student_id=student_id, course_id=None, courses=courses)

    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    data: dict[str, Any] = await manual_progress_service.get_student_progress(
        db, student_id=student_id, course_id=course_id
    )
    # tsk-1361: уровень и причина — тем же путём, что видит ученик.
    targets = [
        (str(i["item_type"]), int(i["item_id"]))
        for i in data.get("items", [])
        if i.get("item_type") in ("task", "material")
    ]
    views = await requirement_override_service.requirement_view(db, student_id, targets)
    for item in data.get("items", []):
        view = views.get((str(item.get("item_type")), int(item.get("item_id", 0))))
        if view is not None:
            item["requirement_level"] = view.level
            item["course_requirement_level"] = view.course_level
            item["requirement_reason"] = requirement_override_service.reason_payload(view.reason)
    return ProgressTreeResponse(courses=courses, **data)


@router.post(
    _BASE + "/tasks/{task_id}",
    response_model=ProgressItemResponse,
    summary="Зачесть задание ученику",
)
async def grant_task(
    student_id: int = Path(..., ge=1),
    task_id: int = Path(..., ge=1),
    payload: ProgressGrantRequest = Body(default=ProgressGrantRequest()),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Идемпотентно отметить задание пройденным (синтетическая попытка + результат).

    422 для квиз-вопросов (``SC_Qw``/``MC_Qw``): их проходит сам ученик, см.
    `manual_progress_service.ensure_task_grantable`.
    """
    course_id = await _course_of_task(db, task_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.grant_task(
        db,
        student_id=student_id,
        task_id=task_id,
        granted_by=_actor_id(current_user),
        comment=payload.comment,
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.delete(
    _BASE + "/tasks/{task_id}",
    response_model=ProgressItemResponse,
    summary="Снять зачёт задания",
)
async def revoke_task(
    student_id: int = Path(..., ge=1),
    task_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Аннулировать синтетические попытки задания; реальные попытки не трогаются."""
    course_id = await _course_of_task(db, task_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.revoke_task(
        db, student_id=student_id, task_id=task_id, revoked_by=_actor_id(current_user)
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.post(
    _BASE + "/tasks/{task_id}/skip",
    response_model=ProgressSkipResponse,
    summary="Пропустить задание ученику (сделать необязательным для него)",
)
async def skip_task(
    student_id: int = Path(..., ge=1),
    task_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressSkipResponse:
    """Не зачёт: задание помечается пропущенным для этого ученика (tsk-919) —
    уходит из остатка программы, нормы ДЗ и «сейчас», но не считается
    решённым. Реальные попытки не трогаются."""
    course_id = await _course_of_task(db, task_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.skip_task(
        db, student_id=student_id, task_id=task_id, skipped_by=_actor_id(current_user),
    )
    await db.commit()
    return ProgressSkipResponse(**result)


@router.delete(
    _BASE + "/tasks/{task_id}/skip",
    response_model=ProgressSkipResponse,
    summary="Вернуть пропущенное задание в обязательные",
)
async def unskip_task(
    student_id: int = Path(..., ge=1),
    task_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressSkipResponse:
    course_id = await _course_of_task(db, task_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.unskip_task(
        db, student_id=student_id, task_id=task_id, unskipped_by=_actor_id(current_user),
    )
    await db.commit()
    return ProgressSkipResponse(**result)


@router.post(
    _BASE + "/materials/{material_id}",
    response_model=ProgressItemResponse,
    summary="Отметить материал пройденным",
)
async def grant_material(
    student_id: int = Path(..., ge=1),
    material_id: int = Path(..., ge=1),
    payload: ProgressGrantRequest = Body(default=ProgressGrantRequest()),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Идемпотентно отметить материал пройденным от лица преподавателя."""
    course_id = await _course_of_material(db, material_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.grant_material(
        db,
        student_id=student_id,
        material_id=material_id,
        granted_by=_actor_id(current_user),
        comment=payload.comment,
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.delete(
    _BASE + "/materials/{material_id}",
    response_model=ProgressItemResponse,
    summary="Снять отметку материала",
)
async def revoke_material(
    student_id: int = Path(..., ge=1),
    material_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Удалить только ручную отметку; прохождение самого ученика сохраняется."""
    course_id = await _course_of_material(db, material_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.revoke_material(
        db,
        student_id=student_id,
        material_id=material_id,
        revoked_by=_actor_id(current_user),
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.post(
    _BASE + "/courses/{course_id}",
    response_model=ProgressBulkResponse,
    summary="Массово зачесть всё дерево узла",
)
async def grant_course(
    student_id: int = Path(..., ge=1),
    course_id: int = Path(..., ge=1),
    payload: ProgressGrantRequest = Body(default=ProgressGrantRequest()),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressBulkResponse:
    """Зачесть все задания и материалы дерева узла (фильтр обязательности — как у движка).

    Квиз-вопросы пропускаются (``skipped_quiz``), а не роняют операцию.
    """
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.grant_course_subtree(
        db,
        student_id=student_id,
        course_id=course_id,
        granted_by=_actor_id(current_user),
        comment=payload.comment,
    )
    await db.commit()
    return ProgressBulkResponse(**result)


@router.delete(
    _BASE + "/courses/{course_id}",
    response_model=ProgressBulkResponse,
    summary="Массово снять зачёты в дереве узла",
)
async def revoke_course(
    student_id: int = Path(..., ge=1),
    course_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressBulkResponse:
    """Снять ручные зачёты по всему дереву узла; реальный прогресс сохраняется."""
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.revoke_course_subtree(
        db,
        student_id=student_id,
        course_id=course_id,
        revoked_by=_actor_id(current_user),
    )
    await db.commit()
    return ProgressBulkResponse(**result)



# ─── Обязательность для ученика (tsk-1361) ─────────────────────────────────


class RequirementSetRequest(BaseModel):
    """Решение преподавателя об обязательности элемента или подкурса для ученика."""

    target_kind: Literal["task", "material", "course"]
    target_id: int = Field(..., ge=1)
    level: Literal["required", "recommended"] = Field(
        ...,
        description=(
            "recommended — снять обязательность; required — вернуть в обязательные "
            "то, что обязательно по курсу (повышение необязательного по курсу пока не "
            "поддерживается — 422)"
        ),
    )
    comment: Optional[str] = Field(
        default=None, max_length=300, description="Причина — её увидят ученик и учитель"
    )


class RequirementChangeResponse(BaseModel):
    """Итог решения: сколько обязательных по курсу элементов оно задевает."""

    student_id: int
    target_kind: Literal["task", "material", "course"]
    target_id: int
    tasks_affected: int
    materials_affected: int
    override_id: Optional[int] = Field(
        default=None, description="ID записи решения; null у предпросмотра и снятия"
    )
    revoked: bool = Field(
        default=False, description="True — решение снято, элемент вернулся к курсу и правилам"
    )


async def _target_course(db: AsyncSession, target_kind: str, target_id: int) -> int:
    """Курс цели для scoped-ACL. 404, если цели нет."""
    if target_kind == "task":
        return await _course_of_task(db, target_id)
    if target_kind == "material":
        return await _course_of_material(db, target_id)
    exists = (
        await db.execute(text("SELECT 1 FROM courses WHERE id = :c"), {"c": target_id})
    ).scalar()
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Курс {target_id} не найден")
    return target_id


def _require_person(current_user: CurrentUser) -> int:
    """Решение об обязательности принимает человек: сервисному токену — 403."""
    actor = _actor_id(current_user)
    if actor is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Решение принимает преподаватель, не сервис"
        )
    return actor


@router.get(
    _BASE + "/requirement/preview",
    response_model=RequirementChangeResponse,
    summary="Сколько элементов задевает решение об обязательности (tsk-1361)",
)
async def preview_requirement(
    student_id: int = Path(..., ge=1),
    target_kind: Literal["task", "material", "course"] = Query(...),
    target_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> RequirementChangeResponse:
    """Показать масштаб до сохранения: для подкурса — всё поддерево."""
    course_id = await _target_course(db, target_kind, target_id)
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id, course_id)
    tasks_n, materials_n = await requirement_override_service.affected_counts(
        db, target_kind, target_id
    )
    return RequirementChangeResponse(
        student_id=student_id, target_kind=target_kind, target_id=target_id,
        tasks_affected=tasks_n, materials_affected=materials_n,
    )


@router.put(
    _BASE + "/requirement",
    response_model=RequirementChangeResponse,
    summary="Задать ученику обязательность элемента или подкурса (tsk-1361)",
)
async def set_requirement(
    body: RequirementSetRequest = Body(...),
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> RequirementChangeResponse:
    """Решение преподавателя главнее правил. Повторный вызов заменяет прежнее."""
    course_id = await _target_course(db, body.target_kind, body.target_id)
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id, course_id)
    actor = _require_person(current_user)
    if body.target_kind in ("task", "material"):
        sql = (
            "SELECT requirement_level FROM tasks WHERE id = :i"
            if body.target_kind == "task"
            else "SELECT requirement_level FROM materials WHERE id = :i"
        )
        base = (await db.execute(text(sql), {"i": body.target_id})).scalar()
        # Решение имеет смысл только для обязательного по курсу: понизить
        # необязательное некуда, повысить его потребители пока не умеют.
        if base not in requirement_override_service.REQUIRED_LEVELS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Элемент необязателен по курсу — решение для ученика ничего не "
                    "изменит (сделать его обязательным одному ученику пока нельзя)"
                ),
            )
    override_id = await requirement_override_service.set_human_override(
        db, student_id=student_id, target_kind=body.target_kind, target_id=body.target_id,
        level=body.level, by_user_id=actor, reason_text=body.comment,
    )
    await db.commit()
    tasks_n, materials_n = await requirement_override_service.affected_counts(
        db, body.target_kind, body.target_id
    )
    return RequirementChangeResponse(
        student_id=student_id, target_kind=body.target_kind, target_id=body.target_id,
        tasks_affected=tasks_n, materials_affected=materials_n, override_id=override_id,
    )


@router.delete(
    _BASE + "/requirement",
    response_model=RequirementChangeResponse,
    summary="Снять решение об обязательности — вернуть к курсу и правилам (tsk-1361)",
)
async def revoke_requirement(
    student_id: int = Path(..., ge=1),
    target_kind: Literal["task", "material", "course"] = Query(...),
    target_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> RequirementChangeResponse:
    """Идемпотентно: нечего снимать — `revoked=false`."""
    course_id = await _target_course(db, target_kind, target_id)
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id, course_id)
    actor = _require_person(current_user)
    revoked = await requirement_override_service.revoke_human_override(
        db, student_id=student_id, target_kind=target_kind, target_id=target_id, by_user_id=actor
    )
    await db.commit()
    tasks_n, materials_n = await requirement_override_service.affected_counts(
        db, target_kind, target_id
    )
    return RequirementChangeResponse(
        student_id=student_id, target_kind=target_kind, target_id=target_id,
        tasks_affected=tasks_n, materials_affected=materials_n, revoked=revoked,
    )
