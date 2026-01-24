from __future__ import annotations

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Body, status, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.courses import (
    CourseRead,
    CourseWithOrderNumber,
    CourseReadWithChildren,
    CourseTreeRead,
    CourseMoveRequest,
    GoogleSheetsImportRequest,
    GoogleSheetsImportResponse,
    GoogleSheetsImportError,
)
from app.schemas.course_parents import CourseParentOrderUpdate
from app.schemas.user_courses import CourseUsersResponse, UserCourseWithUser
from app.services.courses_service import CoursesService
from app.services.user_courses_service import UserCoursesService
from app.services.google_sheets_service import GoogleSheetsService
from app.services.courses_sheets_parser_service import CoursesSheetsParserService
from app.utils.exceptions import DomainError
from app.models.courses import Courses
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["courses"])

courses_service = CoursesService()
user_courses_service = UserCoursesService()


@router.get(
    "/courses/search",
    response_model=List[CourseRead],
    summary="Поиск курсов по названию или коду",
    responses={
        200: {
            "description": "Список найденных курсов",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "title": "Основы Python",
                            "access_level": "auto_check",
                            "description": "Введение в Python",
                            "parent_course_ids": [],
                            "created_at": "2025-02-06T11:42:52.674613Z",
                            "is_required": False,
                            "course_uid": "COURSE-PY-01",
                        }
                    ]
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def search_courses_endpoint(
    q: str = Query(..., min_length=2, description="Поисковый запрос (поиск по title и course_uid)"),
    limit: int = Query(20, ge=1, le=200, description="Максимум результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    db: AsyncSession = Depends(get_db),
) -> List[CourseRead]:
    """
    Поиск курсов по названию (title) или коду (course_uid).
    
    Поиск выполняется с использованием ILIKE (регистронезависимый).
    Ищет вхождение запроса в поле title или course_uid.
    
    Примеры:
    - q="Python" - найдет курсы с "Python" в названии или коде
    - q="COURSE-PY" - найдет курсы с кодом, содержащим "COURSE-PY"
    """
    courses = await courses_service.search_text(
        db,
        field=["title", "course_uid"],
        query=q,
        mode="contains",
        case_insensitive=True,
        limit=limit,
        offset=offset,
        order_by=Courses.title,
    )
    return [CourseRead.model_validate(course) for course in courses]


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
    response_model=List[CourseWithOrderNumber],
    summary="Получить прямых детей курса",
    description=(
        "Получить прямых детей курса (потомки первого уровня) с порядковыми номерами.\n\n"
        "**Возвращает:**\n"
        "- Список курсов, у которых указанный `course_id` является родителем\n"
        "- Каждый курс включает порядковый номер (`order_number`) внутри родительского курса\n"
        "- Сортировка: по `order_number` (NULL в конце), затем по `id`\n\n"
        "**Порядковые номера:**\n"
        "- Автоматически управляются триггерами БД (см. `docs/database-triggers-contract.md`)\n"
        "- Могут быть `null`, если порядковый номер не установлен\n"
        "- Уникальны в рамках одного родительского курса"
    ),
    responses={
        200: {
            "description": "Список прямых детей курса с порядковыми номерами",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 2,
                            "title": "Анализ данных",
                            "access_level": "manual_check",
                            "description": "Курс по анализу данных",
                            "parent_course_ids": [1],
                            "created_at": "2026-01-24T12:00:00Z",
                            "is_required": False,
                            "course_uid": None,
                            "order_number": 1
                        },
                        {
                            "id": 6,
                            "title": "Test Course 1",
                            "access_level": "self_guided",
                            "description": "Test course",
                            "parent_course_ids": [1],
                            "created_at": "2026-01-24T12:00:00Z",
                            "is_required": False,
                            "course_uid": None,
                            "order_number": 3
                        }
                    ]
                }
            }
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
) -> List[CourseWithOrderNumber]:
    """
    Получить прямых детей курса (потомки первого уровня).

    Возвращает список курсов, у которых указанный course_id является родителем.
    Каждый курс включает порядковый номер (order_number) внутри родительского курса.
    """
    children_with_order = await courses_service.get_children(db, course_id)
    result = []
    for course, order_number in children_with_order:
        course_data = CourseRead.model_validate(course).model_dump()
        course_data["order_number"] = order_number
        result.append(CourseWithOrderNumber(**course_data))
    return result


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
    Получить корневые курсы (курсы без родителей).
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
                "value": {"new_parent_ids": [5]},
            },
            {
                "summary": "Установить несколько родителей",
                "value": {"new_parent_ids": [5, 6]},
            },
            {
                "summary": "Сделать корневым курсом",
                "value": {"new_parent_ids": []},
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> CourseRead:
    """
    Переместить курс в иерархии (изменить родительские курсы).

    Правила:
    - Если new_parent_ids указан, курс становится дочерним для указанных курсов.
    - Если new_parent_ids = [] или null, курс становится корневым (без родителей).
    - Курс может иметь несколько родителей.
    - Валидация циклов выполняется триггером БД.

    Ошибки:
    - 404: Курс или родительский курс не найден.
    - 400: Обнаружен цикл в иерархии или курс пытается стать родителем самому себе.
    """
    # Преобразуем new_parent_courses в список словарей, если это Pydantic модели
    new_parent_courses_dict = None
    if payload.new_parent_courses is not None:
        new_parent_courses_dict = [
            pc.model_dump() if hasattr(pc, 'model_dump') else pc
            for pc in payload.new_parent_courses
        ]
    
    updated_course = await courses_service.move_course(
        db,
        course_id,
        new_parent_ids=payload.new_parent_ids,
        new_parent_courses=new_parent_courses_dict,
    )
    return CourseRead.model_validate(updated_course)


@router.patch(
    "/courses/{course_id}/parents/{parent_course_id}/order",
    response_model=CourseRead,
    summary="Изменить порядковый номер подкурса у родителя",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Порядковый номер успешно обновлен",
        },
        404: {
            "description": "Курс, родительский курс или связь не найдены",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def update_course_parent_order_endpoint(
    course_id: int,
    parent_course_id: int,
    payload: CourseParentOrderUpdate = Body(
        ...,
        description="Новый порядковый номер подкурса",
        examples=[
            {
                "summary": "Установить порядковый номер 1",
                "value": {"order_number": 1},
            },
            {
                "summary": "Установить порядковый номер 2",
                "value": {"order_number": 2},
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> CourseRead:
    """
    Изменить порядковый номер подкурса у конкретного родительского курса.
    
    Правила:
    - Если order_number указан, триггер БД автоматически пересчитает порядковые номера остальных подкурсов.
    - Если order_number = null, триггер установит следующий доступный номер.
    - Порядковые номера уникальны в рамках одного родительского курса.
    
    ⚠️ ВАЖНО: Пересчет order_number выполняется автоматически триггером БД.
    См. docs/database-triggers-contract.md
    
    Ошибки:
    - 404: Курс, родительский курс или связь между ними не найдены.
    """
    from app.utils.exceptions import DomainError
    from app.models.association_tables import t_course_parents
    from sqlalchemy import select
    
    # Проверяем существование курса
    course = await courses_service.get_by_id(db, course_id)
    if course is None:
        raise DomainError(
            detail="Курс не найден",
            status_code=404,
            payload={"course_id": course_id},
        )
    
    # Проверяем существование родительского курса
    parent_course = await courses_service.get_by_id(db, parent_course_id)
    if parent_course is None:
        raise DomainError(
            detail="Родительский курс не найден",
            status_code=404,
            payload={"parent_course_id": parent_course_id},
        )
    
    # Проверяем существование связи
    stmt = select(t_course_parents).where(
        (t_course_parents.c.course_id == course_id) &
        (t_course_parents.c.parent_course_id == parent_course_id)
    )
    result = await db.execute(stmt)
    link = result.first()
    if link is None:
        raise DomainError(
            detail="Связь между курсом и родительским курсом не найдена",
            status_code=404,
            payload={"course_id": course_id, "parent_course_id": parent_course_id},
        )
    
    # Обновляем порядковый номер
    await courses_service.repo.update_course_parent_order(
        db, course_id, parent_course_id, payload.order_number
    )
    
    # Перезагружаем курс с relationships
    updated_course = await courses_service.get_by_id(db, course_id)
    return CourseRead.model_validate(updated_course)


@router.get(
    "/courses/{course_id}/users",
    response_model=CourseUsersResponse,
    summary="Получить список студентов курса",
    responses={
        200: {
            "description": "Список студентов курса с информацией о пользователях",
            "content": {
                "application/json": {
                    "example": {
                        "course_id": 1,
                        "course_title": "Основы Python",
                        "total": 5,
                        "users": [
                            {
                                "user_id": 3,
                                "course_id": 1,
                                "added_at": "2025-01-15T10:30:00Z",
                                "order_number": 1,
                                "user": {
                                    "id": 3,
                                    "email": "student@example.com",
                                    "full_name": "Иван Иванов",
                                    "tg_id": 123456789,
                                    "created_at": "2025-01-10T08:00:00Z"
                                }
                            }
                        ]
                    }
                }
            }
        },
        404: {
            "description": "Курс не найден",
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_course_users_endpoint(
    course_id: int,
    limit: int = Query(100, ge=1, le=1000, description="Максимум результатов на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
    db: AsyncSession = Depends(get_db),
) -> CourseUsersResponse:
    """
    Получить список студентов (пользователей), привязанных к курсу.
    
    Возвращает список пользователей с полной информацией о каждом пользователе
    и датой/порядком привязки к курсу.
    
    Параметры:
    - course_id: ID курса
    - limit: Максимум результатов (по умолчанию 100, максимум 1000)
    - offset: Смещение для пагинации
    
    Использование:
    - Для управления студентами курса со стороны методиста
    - Для просмотра текущих студентов курса в боте
    """
    # Проверяем существование курса
    course = await courses_service.get_by_id(db, course_id)
    if course is None:
        raise DomainError(
            detail="Курс не найден",
            status_code=404,
            payload={"course_id": course_id},
        )
    
    # Получаем список пользователей курса
    user_courses = await user_courses_service.get_course_users(
        db, course_id, limit=limit, offset=offset
    )
    
    # Загружаем информацию о пользователях
    from sqlalchemy import select, func
    from app.models.user_courses import UserCourses as UserCoursesModel
    from app.models.users import Users
    
    # Получаем user_ids из связей
    user_ids = [uc.user_id for uc in user_courses]
    
    if not user_ids:
        # Если нет студентов, возвращаем пустой список
        return CourseUsersResponse(
            course_id=course.id,
            course_title=course.title,
            users=[],
            total=0,
        )
    
    # Загружаем пользователей одним запросом
    users_stmt = select(Users).where(Users.id.in_(user_ids))
    users_result = await db.execute(users_stmt)
    users_dict = {user.id: user for user in users_result.scalars().all()}
    
    # Формируем ответ
    from app.schemas.users import UserRead
    users_list = []
    for uc in user_courses:
        user = users_dict.get(uc.user_id)
        if user is None:
            continue  # Пропускаем, если пользователь не найден
        
        user_read = UserRead.model_validate(user)
        user_course_with_user = UserCourseWithUser(
            user_id=uc.user_id,
            course_id=uc.course_id,
            added_at=uc.added_at,
            order_number=uc.order_number,
            user=user_read,
        )
        users_list.append(user_course_with_user)
    
    # Получаем общее количество студентов курса для total
    total_stmt = select(func.count(UserCoursesModel.user_id)).where(
        UserCoursesModel.course_id == course_id
    )
    total_result = await db.execute(total_stmt)
    total = total_result.scalar() or 0
    
    return CourseUsersResponse(
        course_id=course.id,
        course_title=course.title,
        users=users_list,
        total=total,
    )


@router.post(
    "/courses/import/google-sheets",
    response_model=GoogleSheetsImportResponse,
    summary="Импорт курсов из Google Sheets",
    description=(
        "Массовый импорт курсов из Google Sheets таблицы в систему LMS.\n\n"
        "**Поддерживаемые функции:**\n"
        "- Иерархия курсов (parent_course_uid)\n"
        "- Зависимости между курсами (required_courses_uid)\n"
        "- Upsert по course_uid (если курс существует - обновляется, иначе создается)\n\n"
        "**Процесс импорта:**\n"
        "1. Извлекает spreadsheet_id из URL\n"
        "2. Читает данные из указанного листа через Google Sheets API\n"
        "3. Парсит каждую строку данных в структуру курса\n"
        "4. Валидирует данные (структура, ссылочная целостность)\n"
        "5. Импортирует курсы через bulk_upsert (создает новые или обновляет существующие по course_uid)\n"
        "6. Обрабатывает зависимости между курсами\n"
        "7. Возвращает детальный отчет с результатами\n\n"
        "**Рекомендации:**\n"
        "- Используйте `dry_run: true` для предварительной проверки данных\n"
        "- Убедитесь, что Service Account имеет доступ к таблице\n"
        "- Проверьте формат данных в таблице (см. документацию)\n\n"
        "**Требования к таблице:**\n"
        "- Первая строка должна содержать заголовки колонок\n"
        "- Обязательные колонки: `course_uid`, `title`, `access_level`\n"
        "- Опциональные колонки: `description`, `parent_course_uid`, `required_courses_uid`, `is_required`\n"
        "- `required_courses_uid` - список course_uid через запятую (например, 'COURSE-PY-01,COURSE-MATH-01')\n\n"
        "**Обработка ошибок:**\n"
        "- Импорт продолжается даже при ошибках в отдельных строках\n"
        "- Все ошибки возвращаются в массиве `errors` с указанием номера строки\n"
        "- Частичный успех: некоторые курсы могут быть импортированы, другие - нет"
    ),
    responses={
        200: {
            "description": "Импорт выполнен (возможно с ошибками в отдельных строках)",
            "content": {
                "application/json": {
                    "example": {
                        "imported": 10,
                        "updated": 0,
                        "errors": [],
                        "total_rows": 10,
                    }
                }
            }
        },
        400: {
            "description": "Неверные параметры запроса",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Таблица пуста или не найдена"
                    }
                }
            }
        },
        403: {
            "description": "Неверный или отсутствующий API ключ",
        },
        500: {
            "description": "Ошибка при чтении Google Sheets или обработке данных",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Ошибка при чтении Google Sheet: <HttpError 403 when requesting ... returned \"The caller does not have permission\">"
                    }
                }
            }
        },
    },
    tags=["courses", "import"],
)
async def import_courses_from_google_sheets(
    payload: GoogleSheetsImportRequest = Body(
        ...,
        description="Параметры импорта курсов из Google Sheets",
        examples=[
            {
                "summary": "Минимальный запрос",
                "description": "Базовый пример с обязательными полями",
                "value": {
                    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
                }
            },
            {
                "summary": "С указанием листа и dry_run",
                "description": "Пример с явным указанием листа и режимом проверки",
                "value": {
                    "spreadsheet_url": "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
                    "sheet_name": "Courses",
                    "dry_run": True,
                }
            },
            {
                "summary": "С кастомным маппингом колонок",
                "description": "Пример с явным указанием маппинга колонок",
                "value": {
                    "spreadsheet_url": "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
                    "column_mapping": {
                        "Код": "course_uid",
                        "Название": "title",
                        "Описание": "description",
                        "Уровень доступа": "access_level",
                        "Родитель": "parent_course_uid",
                        "Зависимости": "required_courses_uid",
                        "Обязательный": "is_required",
                    },
                }
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> GoogleSheetsImportResponse:
    """
    Импортирует курсы из Google Sheets.
    
    Подробное описание процесса импорта и требований см. в summary эндпойнта.
    """
    logger = logging.getLogger("api.courses_extra")
    
    # Инициализация сервисов
    gsheets_service = GoogleSheetsService()
    parser_service = CoursesSheetsParserService()
    
    # 1. Извлекаем spreadsheet_id
    try:
        spreadsheet_id = parser_service.extract_spreadsheet_id(payload.spreadsheet_url)
        logger.info("Extracted spreadsheet_id: %s", spreadsheet_id)
    except Exception as e:
        logger.exception("Ошибка при извлечении spreadsheet_id: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка при извлечении spreadsheet_id: {str(e)}",
        ) from e
    
    # 2. Читаем данные из Google Sheets
    try:
        # Если sheet_name не указан, используем "Courses" по умолчанию
        sheet_name = payload.sheet_name or "Courses"
        range_name = f"{sheet_name}!A:Z"
        
        logger.info("Reading sheet: %s, range: %s", sheet_name, range_name)
        rows = gsheets_service.read_sheet(
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
        )
        logger.info("Read %d rows from Google Sheet", len(rows))
    except Exception as e:
        logger.exception("Ошибка при чтении Google Sheet: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при чтении Google Sheet: {str(e)}",
        ) from e
    
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Таблица пуста или не найдена",
        )
    
    # 3. Парсим заголовки (первая строка)
    headers = rows[0] if rows else []
    if not headers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Таблица не содержит заголовков",
        )
    
    # Создаем маппинг колонок
    column_mapping = payload.column_mapping or {}
    if not column_mapping:
        # Стандартный маппинг: ищем колонки по названиям
        column_mapping = {}
        for idx, header in enumerate(headers):
            header_lower = header.lower().strip()
            # Маппинг стандартных названий
            if header_lower in ("course_uid", "uid", "id", "код", "course code"):
                column_mapping["course_uid"] = header
            elif header_lower in ("title", "название", "name"):
                column_mapping["title"] = header
            elif header_lower in ("description", "описание", "desc"):
                column_mapping["description"] = header
            elif header_lower in ("access_level", "access level", "уровень доступа", "тип доступа"):
                column_mapping["access_level"] = header
            elif header_lower in ("parent_course_uid", "parent", "родитель", "parent course"):
                column_mapping["parent_course_uid"] = header
            elif header_lower in ("order_number", "order number", "порядковый номер", "порядок", "order"):
                column_mapping["order_number"] = header
            elif header_lower in ("required_courses_uid", "required courses", "зависимости", "dependencies"):
                column_mapping["required_courses_uid"] = header
            elif header_lower in ("is_required", "required", "обязательный", "mandatory"):
                column_mapping["is_required"] = header
    
    # 4. Парсим строки данных
    parsed_courses: List[Dict[str, Any]] = []
    dependencies_map: Dict[str, List[str]] = {}
    errors: List[GoogleSheetsImportError] = []
    
    for row_index, row_data in enumerate(rows[1:], start=1):  # Пропускаем заголовок
        # Преобразуем список в словарь
        row_dict = {}
        for idx, value in enumerate(row_data):
            if idx < len(headers):
                row_dict[headers[idx]] = str(value) if value else ""
        
        # Пропускаем пустые строки
        if not any(row_dict.values()):
            continue
        
        try:
            # Парсим строку
            course_data, required_courses_uid_list = parser_service.parse_course_row(
                row=row_dict,
                column_mapping=column_mapping,
            )
            
            # Сохраняем зависимости для последующей обработки
            if required_courses_uid_list:
                dependencies_map[course_data["course_uid"]] = required_courses_uid_list
            
            parsed_courses.append(course_data)
            
        except DomainError as e:
            # Ошибка валидации - добавляем в список ошибок
            errors.append(GoogleSheetsImportError(
                row_index=row_index,
                course_uid=row_dict.get(column_mapping.get("course_uid", ""), None),
                error=str(e.detail) if hasattr(e, 'detail') else str(e),
            ))
            continue
        except Exception as e:
            logger.exception("Ошибка при парсинге строки %d: %s", row_index, e)
            errors.append(GoogleSheetsImportError(
                row_index=row_index,
                course_uid=None,
                error=f"Ошибка парсинга: {str(e)}",
            ))
            continue
    
    if not parsed_courses:
        return GoogleSheetsImportResponse(
            imported=0,
            updated=0,
            errors=errors,
            total_rows=len(rows) - 1,  # Без заголовка
        )
    
    # 5. Импортируем курсы (если не dry_run)
    imported = 0
    updated = 0
    
    if not payload.dry_run:
        try:
            results, import_errors = await courses_service.bulk_upsert(
                db, 
                parsed_courses,
                dependencies_map=dependencies_map if dependencies_map else None,
            )
            
            # Обрабатываем успешно импортированные курсы
            for course_uid, action, course_id in results:
                if action == "created":
                    imported += 1
                elif action == "updated":
                    updated += 1
            
            # Добавляем ошибки из bulk_upsert в общий список ошибок
            for import_error in import_errors:
                # Пытаемся найти номер строки по course_uid
                row_index = 0
                course_uid_from_error = None
                if hasattr(import_error, 'payload') and import_error.payload:
                    course_uid_from_error = import_error.payload.get('course_uid')
                    # Ищем номер строки в parsed_courses
                    for idx, course_data in enumerate(parsed_courses, start=1):
                        if course_data.get("course_uid") == course_uid_from_error:
                            row_index = idx
                            break
                
                errors.append(GoogleSheetsImportError(
                    row_index=row_index,
                    course_uid=course_uid_from_error,
                    error=str(import_error.detail) if hasattr(import_error, 'detail') else str(import_error),
                ))
            
            logger.info("Imported: %d, Updated: %d, Errors: %d", imported, updated, len(import_errors))
        except Exception as e:
            logger.exception("Ошибка при импорте курсов: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка при импорте курсов: {str(e)}",
            ) from e
    else:
        # В dry_run режиме считаем все как "would be imported"
        imported = len(parsed_courses)
        logger.info("Dry run: would import %d courses", imported)
    
    return GoogleSheetsImportResponse(
        imported=imported,
        updated=updated,
        errors=errors,
        total_rows=len(rows) - 1,  # Без заголовка
    )
