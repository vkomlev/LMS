from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, Body, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.schemas.user_courses import (
    UserCourseBulkCreate,
    UserCourseListResponse,
    UserCourseReorderRequest,
    UserCourseRead,
    UserCourseWithCourse,
)
from app.services.user_courses_service import UserCoursesService
from app.services.teacher_courses_service import TeacherCoursesService
from app.services.user_roles_service import UserRolesService
from app.models.user_courses import UserCourses
from app.models.users import Users
from sqlalchemy import select
from app.schemas.courses import CourseRead

router = APIRouter(tags=["user_courses"])

user_courses_service = UserCoursesService()
teacher_courses_service = TeacherCoursesService()
user_roles_service = UserRolesService()


def _is_teacher_role(role_name: str) -> bool:
    """Проверяет, является ли роль преподавательской."""
    role_lower = role_name.lower().strip()
    return role_lower in ["teacher", "преподаватель"]


async def _user_has_teacher_role(db: AsyncSession, user_id: int) -> bool:
    """Проверяет, имеет ли пользователь роль преподавателя."""
    from app.models.association_tables import t_user_roles
    from app.models.roles import Roles
    
    stmt = (
        select(Roles.name)
        .join(t_user_roles, Roles.id == t_user_roles.c.role_id)
        .where(t_user_roles.c.user_id == user_id)
    )
    result = await db.execute(stmt)
    roles = [row[0] for row in result.fetchall()]
    
    return any(_is_teacher_role(role) for role in roles)


async def _get_teacher_courses(
    db: AsyncSession,
    user_id: int,
    order_by_order: bool = True
) -> List[UserCourseWithCourse]:
    """Получить курсы преподавателя из teacher_courses."""
    from app.models.association_tables import t_teacher_courses
    from app.models.courses import Courses
    
    stmt = (
        select(Courses, t_teacher_courses.c.linked_at)
        .join(t_teacher_courses, Courses.id == t_teacher_courses.c.course_id)
        .where(t_teacher_courses.c.teacher_id == user_id)
    )
    
    # Сортировка
    if order_by_order:
        stmt = stmt.order_by(t_teacher_courses.c.linked_at.desc())
    else:
        stmt = stmt.order_by(t_teacher_courses.c.linked_at.asc())
    
    result = await db.execute(stmt)
    rows = result.all()
    
    courses_list = []
    for course, linked_at in rows:
        course_read = CourseRead.model_validate(course)
        course_data = UserCourseWithCourse(
            user_id=user_id,
            course_id=course.id,
            added_at=linked_at,  # Используем linked_at как added_at для совместимости
            order_number=None,  # У преподавателей нет order_number
            course=course_read,
        )
        courses_list.append(course_data)
    
    return courses_list


async def _get_student_courses(
    db: AsyncSession,
    user_id: int,
    order_by_order: bool = True
) -> List[UserCourseWithCourse]:
    """Получить курсы студента из user_courses."""
    stmt = select(UserCourses).where(UserCourses.user_id == user_id)
    
    if order_by_order:
        stmt = stmt.order_by(
            UserCourses.order_number.asc().nulls_last(),
            UserCourses.added_at.asc()
        )
    else:
        stmt = stmt.order_by(UserCourses.added_at.asc())
    
    # Явно загружаем связанные курсы
    stmt = stmt.options(selectinload(UserCourses.course))
    
    result = await db.execute(stmt)
    user_courses = list(result.scalars().all())
    
    courses_list = []
    for uc in user_courses:
        # Проверяем, что курс загружен
        if uc.course is None:
            # Если курс не найден, пропускаем эту запись
            continue
        
        # Преобразуем курс в схему
        course_read = CourseRead.model_validate(uc.course)
        
        course_data = UserCourseWithCourse(
            user_id=uc.user_id,
            course_id=uc.course_id,
            added_at=uc.added_at,
            order_number=uc.order_number,
            course=course_read,
        )
        courses_list.append(course_data)
    
    return courses_list


@router.get(
    "/users/{user_id}/courses",
    response_model=UserCourseListResponse,
    summary="Получить список курсов пользователя",
    description=(
        "Получить список курсов пользователя с информацией о курсах.\n\n"
        "**Особенности:**\n"
        "- Пользователь может быть одновременно и преподавателем, и студентом\n"
        "- Если параметр `role` не указан - возвращаются курсы из обеих таблиц (объединенный список)\n"
        "- Если параметр `role=teacher` - возвращаются только курсы преподавателя из `teacher_courses`\n"
        "- Если параметр `role=student` - возвращаются только курсы студента из `user_courses`\n\n"
        "**Параметры:**\n"
        "- `role` (опционально): Фильтр по роли (`teacher` или `student`). Если не указан - возвращаются все курсы\n"
        "- `order_by_order`: Если True, сортировать по order_number (для студентов) или по linked_at (для преподавателей), иначе по added_at/linked_at\n\n"
        "**Примеры:**\n"
        "- `GET /api/v1/users/2/courses` - все курсы пользователя (и как преподавателя, и как студента)\n"
        "- `GET /api/v1/users/2/courses?role=teacher` - только курсы преподавателя\n"
        "- `GET /api/v1/users/2/courses?role=student` - только курсы студента"
    ),
    responses={
        200: {
            "description": "Список курсов пользователя с информацией о курсах",
        },
        404: {
            "description": "Пользователь не найден",
        },
        400: {
            "description": "Некорректное значение параметра role (должно быть 'teacher' или 'student')",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_user_courses_endpoint(
    user_id: int,
    role: Optional[str] = Query(
        None,
        description=(
            "Фильтр по роли: 'teacher' - только курсы преподавателя, "
            "'student' - только курсы студента. "
            "Если не указан - возвращаются курсы из обеих таблиц."
        ),
        examples=["teacher", "student"]
    ),
    order_by_order: bool = Query(True, description="Сортировать по order_number (True) или по added_at (False)"),
    db: AsyncSession = Depends(get_db),
) -> UserCourseListResponse:
    """
    Получить список курсов пользователя с информацией о курсах.
    
    Поддерживает пользователей с несколькими ролями одновременно.
    """
    # Проверяем существование пользователя
    user = await db.get(Users, user_id)
    if user is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Пользователь с ID {user_id} не найден")
    
    courses_list = []
    
    # Нормализуем параметр role
    role_normalized = None
    if role:
        role_lower = role.lower().strip()
        if role_lower in ["teacher", "преподаватель"]:
            role_normalized = "teacher"
        elif role_lower in ["student", "студент"]:
            role_normalized = "student"
        else:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail=f"Некорректное значение параметра role: '{role}'. Допустимые значения: 'teacher', 'student'"
            )
    
    # Если роль указана явно - возвращаем курсы только из соответствующей таблицы
    if role_normalized == "teacher":
        courses_list = await _get_teacher_courses(db, user_id, order_by_order)
    elif role_normalized == "student":
        courses_list = await _get_student_courses(db, user_id, order_by_order)
    else:
        # Если роль не указана - возвращаем курсы из обеих таблиц
        # Для объединенного списка используем сортировку по дате (added_at/linked_at)
        # независимо от order_by_order, так как у преподавателей нет order_number
        teacher_courses = await _get_teacher_courses(db, user_id, order_by_order=False)
        student_courses = await _get_student_courses(db, user_id, order_by_order=False)
        
        # Объединяем списки
        courses_list = teacher_courses + student_courses
        
        # Сортируем объединенный список по added_at/linked_at
        # (для преподавателей это linked_at, для студентов - added_at)
        # Применяем order_by_order к финальной сортировке
        if order_by_order:
            # Сортируем по убыванию даты (новые сначала)
            courses_list.sort(key=lambda x: x.added_at, reverse=True)
        else:
            # Сортируем по возрастанию даты (старые сначала)
            courses_list.sort(key=lambda x: x.added_at)
    
    return UserCourseListResponse(
        user_id=user_id,
        courses=courses_list,
    )


@router.post(
    "/users/{user_id}/courses/bulk",
    response_model=List[UserCourseRead],
    summary="Массовая привязка курсов к пользователю",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "Курсы успешно привязаны к пользователю",
        },
        400: {
            "description": "Ошибка валидации (пустой список курсов, дубликаты)",
        },
        404: {
            "description": "Пользователь не найден",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def bulk_assign_courses_endpoint(
    user_id: int,
    payload: UserCourseBulkCreate = Body(
        ...,
        description="Список ID курсов для привязки",
        examples=[
            {
                "summary": "Привязать несколько курсов",
                "value": {"course_ids": [1, 2, 3]},
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> List[UserCourseRead]:
    """
    Массовая привязка курсов к пользователю.
    
    Правила:
    - Курсы привязываются с автоматической нумерацией (order_number устанавливается триггером БД).
    - Если курс уже привязан к пользователю, он пропускается (не создается дубликат).
    - Порядок в списке course_ids определяет порядок order_number.
    
    Ошибки:
    - 400: Пустой список курсов или некорректные данные.
    - 404: Пользователь не найден.
    """
    created_user_courses = await user_courses_service.bulk_assign_courses(
        db, user_id, payload.course_ids
    )
    
    return [UserCourseRead.model_validate(uc) for uc in created_user_courses]


@router.patch(
    "/users/{user_id}/courses/reorder",
    response_model=List[UserCourseRead],
    summary="Переупорядочить курсы пользователя",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Курсы успешно переупорядочены",
        },
        400: {
            "description": "Ошибка валидации (некорректные порядковые номера, курсы не принадлежат пользователю)",
        },
        404: {
            "description": "Пользователь не найден",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def reorder_user_courses_endpoint(
    user_id: int,
    payload: UserCourseReorderRequest = Body(
        ...,
        description="Список курсов с их порядковыми номерами",
        examples=[
            {
                "summary": "Переупорядочить курсы",
                "value": {
                    "course_orders": [
                        {"course_id": 1, "order_number": 1},
                        {"course_id": 2, "order_number": 2},
                        {"course_id": 3, "order_number": 3},
                    ]
                },
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> List[UserCourseRead]:
    """
    Переупорядочить курсы пользователя (явное обновление order_number).
    
    Правила:
    - Каждый курс должен быть указан с его новым порядковым номером.
    - Порядковые номера должны быть уникальными и начинаться с 1.
    - Все указанные курсы должны быть привязаны к пользователю.
    
    Ошибки:
    - 400: Некорректные порядковые номера или курсы не принадлежат пользователю.
    - 404: Пользователь не найден.
    """
    # Преобразуем список CourseOrderItem в список словарей для сервиса
    course_orders = [
        {"course_id": item.course_id, "order_number": item.order_number}
        for item in payload.course_orders
    ]
    
    updated_user_courses = await user_courses_service.reorder_courses(
        db, user_id, course_orders
    )
    
    return [UserCourseRead.model_validate(uc) for uc in updated_user_courses]
