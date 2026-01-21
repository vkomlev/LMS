from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, Body, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.courses import (
    CourseRead,
    CourseReadWithChildren,
    CourseTreeRead,
    CourseMoveRequest,
)
from app.services.courses_service import CoursesService
from app.utils.exceptions import DomainError

router = APIRouter(tags=["courses"])

courses_service = CoursesService()


@router.get(
    "/courses/by-code/{code}",
    response_model=CourseRead,
    summary="Получить курс по его коду (course_uid)",
    responses={
        200: {
            "description": "Курс найден",
            "content": {
                "application/json": {
                    "example": {
                        "id": 1,
                        "title": "Основы Python",
                        "access_level": "auto_check",
                        "description": "Введение в Python",
                        "parent_course_id": None,
                        "created_at": "2025-02-06T11:42:52.674613Z",
                        "is_required": False,
                        "course_uid": "COURSE-PY-01",
                    }
                }
            },
        },
        404: {
            "description": "Курс с указанным code не найден",
            "content": {
                "application/json": {
                    "example": {
                        "error": "domain_error",
                        "detail": "Курс с указанным кодом не найден",
                        "payload": {"course_uid": "COURSE-NOT-FOUND"},
                    }
                }
            },
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_course_by_code_endpoint(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> CourseRead:
    """
    Вернуть курс по его внешнему коду (course_uid).

    Статусы:
    - 200 — если курс найден;
    - 404 — если курс не найден (DomainError обрабатывается глобальным хэндлером).
    """
    course = await courses_service.get_by_course_uid(db, course_uid=code)
    return course


@router.get(
    "/courses/{course_id}/children",
    response_model=List[CourseRead],
    summary="Получить прямых детей курса",
    responses={
        200: {
            "description": "Список прямых детей курса",
        },
        404: {
            "description": "Курс не найден",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_course_children_endpoint(
    course_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[CourseRead]:
    """
    Получить прямых детей курса (потомки первого уровня).

    Возвращает список курсов, у которых parent_course_id равен указанному course_id.
    """
    children = await courses_service.get_children(db, course_id)
    return [CourseRead.model_validate(child) for child in children]


@router.get(
    "/courses/{course_id}/tree",
    response_model=CourseTreeRead,
    summary="Получить дерево курса с детьми всех уровней",
    responses={
        200: {
            "description": "Дерево курса с вложенными детьми",
        },
        404: {
            "description": "Курс не найден",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_course_tree_endpoint(
    course_id: int,
    db: AsyncSession = Depends(get_db),
) -> CourseTreeRead:
    """
    Получить дерево курса с детьми всех уровней (рекурсивная структура).

    Возвращает курс с загруженными детьми всех уровней вложенности.
    """
    tree = await courses_service.get_course_tree(db, course_id)
    if tree is None:
        raise DomainError(
            detail="Курс не найден",
            status_code=404,
            payload={"course_id": course_id},
        )
    return CourseTreeRead.model_validate(tree)


@router.get(
    "/courses/roots",
    response_model=List[CourseRead],
    summary="Получить корневые курсы",
    responses={
        200: {
            "description": "Список корневых курсов (без родителя)",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_root_courses_endpoint(
    db: AsyncSession = Depends(get_db),
) -> List[CourseRead]:
    """
    Получить корневые курсы (курсы без родителя, parent_course_id IS NULL).
    """
    root_courses = await courses_service.get_root_courses(db)
    return [CourseRead.model_validate(course) for course in root_courses]


@router.patch(
    "/courses/{course_id}/move",
    response_model=CourseRead,
    summary="Переместить курс в иерархии",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Курс успешно перемещен",
        },
        400: {
            "description": "Ошибка валидации (цикл в иерархии, курс не может быть родителем самому себе)",
            "content": {
                "application/json": {
                    "examples": {
                        "cycle": {
                            "summary": "Попытка создать цикл",
                            "value": {
                                "error": "domain_error",
                                "detail": "Нельзя создать цикл в иерархии курсов",
                                "payload": {"course_id": 10, "new_parent_id": 11},
                            },
                        },
                        "self_parent": {
                            "summary": "Курс пытается стать родителем сам себе",
                            "value": {
                                "error": "domain_error",
                                "detail": "Курс не может быть родителем самому себе",
                                "payload": {"course_id": 10},
                            },
                        },
                    }
                }
            },
        },
        404: {
            "description": "Курс или родительский курс не найден",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def move_course_endpoint(
    course_id: int,
    payload: CourseMoveRequest = Body(
        ...,
        description="Данные для перемещения курса",
        examples=[
            {
                "summary": "Переместить в подкурс",
                "value": {"new_parent_id": 5},
            },
            {
                "summary": "Сделать корневым курсом",
                "value": {"new_parent_id": None},
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> CourseRead:
    """
    Переместить курс в иерархии (изменить parent_course_id).

    Правила:
    - Если new_parent_id указан, курс становится дочерним для указанного курса.
    - Если new_parent_id = None, курс становится корневым (без родителя).
    - Валидация циклов выполняется триггером БД.

    Ошибки:
    - 404: Курс или родительский курс не найден.
    - 400: Обнаружен цикл в иерархии или курс пытается стать родителем самому себе.
    """
    updated_course = await courses_service.move_course(
        db,
        course_id,
        payload.new_parent_id,
    )
    return CourseRead.model_validate(updated_course)
