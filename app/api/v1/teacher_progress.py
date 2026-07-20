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
from app.services import manual_progress_service

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


class StudentCourseRef(BaseModel):
    """Курс ученика для селектора в карточке."""

    course_id: int
    title: Optional[str] = None


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
