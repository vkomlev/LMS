from __future__ import annotations

from fastapi import APIRouter, Depends, Body, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Any, List, Literal, Optional, Dict
from pydantic import BaseModel
import logging

from app.api.deps import get_db
from app.schemas.tasks import (
    TaskRead, 
    TaskBulkUpsertRequest, 
    TaskBulkUpsertResponse, 
    TaskBulkUpsertResultItem, 
    TaskValidateResponse, 
    TaskValidateRequest,
    TaskFindByExternalResponse,
    TaskFindByExternalItem,
    TaskFindByExternalRequest,
    GoogleSheetsImportRequest,
    GoogleSheetsImportResponse,
    GoogleSheetsImportError,
)
from app.services.tasks_service import TasksService
from app.services.google_sheets_service import GoogleSheetsService
from app.services.sheets_parser_service import SheetsParserService
from app.services.courses_service import CoursesService
from app.services.difficulty_levels_service import DifficultyLevelsService
from fastapi import HTTPException, status
import logging


router = APIRouter(tags=["tasks"])

tasks_service = TasksService()


@router.get(
    "/tasks/by-external/{external_uid}",
    response_model=TaskRead,
    summary="Получить задачу по внешнему идентификатору",
    responses={
        200: {
            "description": "Задача найдена",
            "content": {
                "application/json": {
                    "example": {
                        "id": 1,
                        "external_uid": "TASK-SC-001",
                        "task_content": {
                            "type": "SC",
                            "stem": "Что такое переменная в Python?",
                            "options": [
                                {"id": "A", "text": "Именованная область памяти", "is_active": True},
                                {"id": "B", "text": "Функция для вывода", "is_active": True},
                            ],
                        },
                        "solution_rules": {
                            "max_score": 10,
                            "correct_options": ["A"],
                            "penalties": {"wrong_answer": 0, "missing_answer": 0, "extra_wrong_mc": 0},
                        },
                        "course_id": 1,
                        "difficulty_id": 3,
                        "max_score": 10,
                    }
                }
            }
        },
        404: {
            "description": "Задача с указанным external_uid не найдена",
            "content": {
                "application/json": {
                    "example": {
                        "error": "domain_error",
                        "detail": "Задача с указанным external_uid не найдена",
                        "payload": {"external_uid": "TASK-NOT-FOUND"},
                    }
                }
            }
        },
    },
)
async def get_task_by_external_uid(
    external_uid: str,
    db: AsyncSession = Depends(get_db),
) -> TaskRead:
    """
    Вернуть задачу по внешнему устойчивому идентификатору.

    Статусы:
    - 200 — если задача найдена;
    - 404 — если задача не найдена (генерируется DomainError в сервисе).
    """
    task = await tasks_service.get_by_external_uid(db, external_uid=external_uid)
    return task

@router.post(
    "/tasks/validate",
    response_model=TaskValidateResponse,
    summary="Массовая предварительная валидация задания перед импортом",
    responses={
        200: {
            "description": "Валидация выполнена",
            "content": {
                "application/json": {
                    "examples": {
                        "valid": {
                            "summary": "Валидная задача",
                            "value": {
                                "is_valid": True,
                                "errors": [],
                            }
                        },
                        "invalid": {
                            "summary": "Задача с ошибками",
                            "value": {
                                "is_valid": False,
                                "errors": [
                                    "course_code not provided",
                                    "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2",
                                ],
                            }
                        },
                    }
                }
            }
        },
        422: {
            "description": "Ошибка валидации запроса (неверный формат данных)",
        },
    },
)
async def validate_task_endpoint(
    payload: TaskValidateRequest = Body(
        ...,
        description="Данные задания для предварительной валидации",
        examples=[
            {
                "summary": "Валидная задача SC",
                "value": {
                    "task_content": {
                        "type": "SC",
                        "stem": "Что такое переменная?",
                        "options": [
                            {"id": "A", "text": "Область памяти", "is_active": True},
                            {"id": "B", "text": "Функция", "is_active": True},
                        ],
                    },
                    "solution_rules": {
                        "max_score": 10,
                        "correct_options": ["A"],
                    },
                    "course_code": "PY",
                    "difficulty_code": "NORMAL",
                    "external_uid": "TASK-SC-001",
                }
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskValidateResponse:
    """
    Предварительная проверка структуры и ссылочных данных задания до записи в БД.

    Проверяем:
    - структуру task_content (например, наличие options[].id),
    - ключевые поля solution_rules (например, max_score),
    - существование difficulty по difficulty_code,
    - существование course по course_code.

    Запись в БД не выполняется, возвращается только флаг is_valid и список ошибок.
    """
    is_valid, errors = await tasks_service.validate_task_import(
        db,
        task_content=payload.task_content,
        solution_rules=payload.solution_rules,
        difficulty_code=payload.difficulty_code,
        difficulty_id=payload.difficulty_id,
        course_code=payload.course_code,
        external_uid=payload.external_uid,
    )
    return TaskValidateResponse(is_valid=is_valid, errors=errors)


@router.post(
    "/tasks/bulk-upsert",
    response_model=TaskBulkUpsertResponse,
    summary="Массовый upsert задач по external_uid",
    responses={
        200: {
            "description": "Upsert выполнен успешно",
            "content": {
                "application/json": {
                    "example": {
                        "results": [
                            {"external_uid": "TASK-SC-001", "action": "created", "id": 1},
                            {"external_uid": "TASK-SC-002", "action": "updated", "id": 2},
                        ]
                    }
                }
            }
        },
        400: {
            "description": "Ошибка валидации данных задач",
            "content": {
                "application/json": {
                    "example": {
                        "error": "domain_error",
                        "detail": "Ошибка валидации данных задачи: Для задач типа SC должен быть указан ровно один правильный вариант",
                    }
                }
            }
        },
        422: {
            "description": "Ошибка валидации запроса (неверный формат JSON)",
        },
    },
)
async def bulk_upsert_tasks_endpoint(
    payload: TaskBulkUpsertRequest = Body(
        ...,
        description="Список задач для массового upsert'а",
        examples=[
            {
                "summary": "Массовый импорт задач",
                "value": {
                    "items": [
                        {
                            "external_uid": "TASK-SC-001",
                            "course_id": 1,
                            "difficulty_id": 3,
                            "task_content": {
                                "type": "SC",
                                "stem": "Что такое переменная?",
                                "options": [
                                    {"id": "A", "text": "Область памяти", "is_active": True},
                                    {"id": "B", "text": "Функция", "is_active": True},
                                ],
                            },
                            "solution_rules": {
                                "max_score": 10,
                                "correct_options": ["A"],
                            },
                            "max_score": 10,
                        }
                    ]
                }
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskBulkUpsertResponse:
    """
    Массовый upsert задач.

    Правила:
    - если external_uid не найден → создаём задачу (action = 'created');
    - если найден → обновляем существующую задачу (action = 'updated').

    Это позволяет существенно ускорить импорт из Google Sheets:
    одно HTTP-обращение вместо сотен.
    """
    raw_results = await tasks_service.bulk_upsert(
        db,
        items=[item.model_dump() for item in payload.items],
    )

    results = [
        TaskBulkUpsertResultItem(
            external_uid=external_uid,
            action=action,  # "created" | "updated"
            id=task_id,
        )
        for external_uid, action, task_id in raw_results
    ]

    return TaskBulkUpsertResponse(results=results)

@router.post(
    "/tasks/find-by-external",
    response_model=TaskFindByExternalResponse,
    summary="Массовое получение задач по списку external_uid",
)
async def find_tasks_by_external_uid_endpoint(
    payload: TaskFindByExternalRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskFindByExternalResponse:
    """
    Массовое получение задач по external_uid.

    Возвращает только существующие задачи.
    Если часть UID отсутствует — они просто не попадут в список.
    """
    results = await tasks_service.find_by_external_uids(db, uids=payload.uids)

    items = [
        TaskFindByExternalItem(external_uid=uid, id=id_)
        for uid, id_ in results
    ]

    return TaskFindByExternalResponse(items=items)


@router.get(
    "/tasks/by-course/{course_id}",
    response_model=List[TaskRead],
    summary="Получить задачи курса",
    responses={
        200: {
            "description": "Список задач курса",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "external_uid": "TASK-SC-001",
                            "task_content": {
                                "type": "SC",
                                "stem": "Что такое переменная в Python?",
                                "options": [
                                    {"id": "A", "text": "Область памяти", "is_active": True},
                                    {"id": "B", "text": "Функция", "is_active": True},
                                ],
                            },
                            "solution_rules": {
                                "max_score": 10,
                                "correct_options": ["A"],
                            },
                            "course_id": 1,
                            "difficulty_id": 3,
                            "max_score": 10,
                        }
                    ]
                }
            }
        },
        404: {
            "description": "Курс не найден",
        },
    },
)
async def get_tasks_by_course(
    course_id: int,
    db: AsyncSession = Depends(get_db),
    difficulty_id: Optional[int] = Query(None, description="Фильтр по уровню сложности"),
    limit: int = Query(100, ge=1, le=1000, description="Максимум записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
) -> List[TaskRead]:
    """
    Получить список задач курса с пагинацией.

    Поддерживается опциональная фильтрация по уровню сложности.

    Args:
        course_id: ID курса.
        difficulty_id: Опциональный фильтр по уровню сложности.
        limit: Максимум записей на странице (1-1000).
        offset: Смещение для пагинации.

    Returns:
        Список задач курса.
    """
    tasks, total = await tasks_service.get_by_course(
        db,
        course_id=course_id,
        difficulty_id=difficulty_id,
        limit=limit,
        offset=offset,
    )
    return [TaskRead.model_validate(task) for task in tasks]


@router.get(
    "/tasks/search",
    response_model=List[TaskRead],
    summary="Поиск заданий по содержимому",
    responses={
        200: {
            "description": "Список найденных заданий",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "external_uid": "TASK-SC-001",
                            "task_content": {
                                "type": "SC",
                                "stem": "Что такое переменная в Python?",
                                "options": [
                                    {"id": "A", "text": "Область памяти", "is_active": True}
                                ]
                            },
                            "solution_rules": {
                                "max_score": 10,
                                "correct_options": ["A"]
                            },
                            "course_id": 1,
                            "difficulty_id": 3,
                            "max_score": 10
                        }
                    ]
                }
            }
        },
    },
)
async def search_tasks(
    q: str = Query(..., min_length=2, description="Поисковый запрос (минимум 2 символа)"),
    course_id: Optional[int] = Query(None, description="Фильтр по курсу"),
    limit: int = Query(20, ge=1, le=200, description="Максимум результатов"),
    offset: int = Query(0, ge=0, description="Смещение"),
    db: AsyncSession = Depends(get_db),
) -> List[TaskRead]:
    """
    Поиск заданий по содержимому.
    
    Поиск выполняется по:
    - task_content.stem (формулировка вопроса)
    - task_content.title (название задания, если указано)
    - external_uid (внешний идентификатор)
    
    Поиск регистронезависимый (case-insensitive).
    
    Args:
        q: Поисковый запрос (минимум 2 символа)
        course_id: Опциональный фильтр по курсу
        limit: Максимум результатов (1-200)
        offset: Смещение для пагинации
    
    Returns:
        Список найденных заданий
    """
    from app.models.tasks import Tasks
    
    # Поиск по JSONB полям task_content
    # Используем JSONB операторы PostgreSQL для поиска в task_content.stem и task_content.title
    search_conditions = [
        Tasks.task_content['stem'].astext.ilike(f'%{q}%'),
        Tasks.task_content['title'].astext.ilike(f'%{q}%'),
        Tasks.external_uid.ilike(f'%{q}%'),
    ]
    
    query = select(Tasks).where(or_(*search_conditions))
    
    if course_id is not None:
        query = query.where(Tasks.course_id == course_id)
    
    query = query.limit(limit).offset(offset).order_by(Tasks.id)
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return [TaskRead.model_validate(task) for task in tasks]


@router.post(
    "/tasks/import/google-sheets",
    response_model=GoogleSheetsImportResponse,
    summary="Импорт задач из Google Sheets",
    description=(
        "Массовый импорт задач из Google Sheets таблицы в систему LMS.\n\n"
        "**Поддерживаемые типы задач:** SC (Single Choice), MC (Multiple Choice), "
        "SA (Short Answer), SA_COM (Short Answer with Comments), TA (Text Answer).\n\n"
        "**Процесс импорта:**\n"
        "1. Извлекает spreadsheet_id из URL\n"
        "2. Читает данные из указанного листа через Google Sheets API\n"
        "3. Парсит каждую строку данных в структуру задачи\n"
        "4. Валидирует данные (структура, ссылочная целостность)\n"
        "5. Импортирует задачи через bulk_upsert (создает новые или обновляет существующие по external_uid)\n"
        "6. Возвращает детальный отчет с результатами\n\n"
        "**Рекомендации:**\n"
        "- Используйте `dry_run: true` для предварительной проверки данных\n"
        "- Убедитесь, что Service Account имеет доступ к таблице\n"
        "- Проверьте формат данных в таблице (см. документацию)\n\n"
        "**Требования к таблице:**\n"
        "- Первая строка должна содержать заголовки колонок\n"
        "- Обязательные колонки: `external_uid`, `type`, `stem`, `correct_answer`\n"
        "- Для SC/MC обязательно указать `options`\n\n"
        "**Обработка ошибок:**\n"
        "- Импорт продолжается даже при ошибках в отдельных строках\n"
        "- Все ошибки возвращаются в массиве `errors` с указанием номера строки\n"
        "- Частичный успех: некоторые задачи могут быть импортированы, другие - нет"
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
                        "detail": "Необходимо указать course_id или course_code"
                    }
                }
            }
        },
        403: {
            "description": "Неверный или отсутствующий API ключ",
        },
        404: {
            "description": "Курс или уровень сложности не найден",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Курс с кодом 'INVALID-CODE' не найден"
                    }
                }
            }
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
    tags=["tasks", "import"],
)
async def import_from_google_sheets(
    payload: GoogleSheetsImportRequest = Body(
        ...,
        description="Параметры импорта из Google Sheets",
        examples=[
            {
                "summary": "Минимальный запрос",
                "description": "Базовый пример с обязательными полями",
                "value": {
                    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
                    "course_code": "PY",
                    "difficulty_code": "NORMAL",
                }
            },
            {
                "summary": "С указанием листа и dry_run",
                "description": "Пример с явным указанием листа и режимом проверки",
                "value": {
                    "spreadsheet_url": "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
                    "sheet_name": "Лист1",
                    "course_code": "PY",
                    "difficulty_code": "NORMAL",
                    "dry_run": True,
                }
            },
            {
                "summary": "С кастомным маппингом колонок",
                "description": "Пример с явным указанием маппинга колонок",
                "value": {
                    "spreadsheet_url": "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
                    "column_mapping": {
                        "ID": "external_uid",
                        "Тип": "type",
                        "Вопрос": "stem",
                        "Варианты": "options",
                        "Правильный ответ": "correct_answer",
                    },
                    "course_code": "PY",
                    "difficulty_code": "NORMAL",
                }
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> GoogleSheetsImportResponse:
    """
    Импортирует задачи из Google Sheets.
    
    Подробное описание процесса импорта и требований см. в summary эндпойнта.
    """
    logger = logging.getLogger("api.tasks_extra")
    
    # Инициализация сервисов
    gsheets_service = GoogleSheetsService()
    parser_service = SheetsParserService()
    tasks_service = TasksService()
    courses_service = CoursesService()
    difficulty_service = DifficultyLevelsService()
    
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
    
    # 2. Определяем course_id и difficulty_id
    course_id = payload.course_id
    invalid_payload_course_code: str | None = None
    if not course_id and payload.course_code:
        try:
            course = await courses_service.get_by_course_uid(db, payload.course_code)
            course_id = course.id
        except Exception as e:
            # Если курс задан неверно, но в таблице есть course_uid "на строке",
            # импорт всё равно может быть выполнен для строк с валидными course_uid.
            logger.warning("Курс с кодом '%s' не найден: %s", payload.course_code, e)
            invalid_payload_course_code = payload.course_code
            course_id = None
    
    difficulty_id = payload.difficulty_id
    if not difficulty_id and payload.difficulty_code:
        try:
            difficulty = await difficulty_service.repo.get_by_keys(
                db,
                {"code": payload.difficulty_code},
            )
            if difficulty:
                difficulty_id = difficulty.id
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Уровень сложности с кодом '{payload.difficulty_code}' не найден",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Ошибка при поиске уровня сложности: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ошибка при поиске уровня сложности: {str(e)}",
            ) from e
    
    # 3. Читаем данные из Google Sheets
    try:
        # Если sheet_name не указан, используем из настроек или "Лист1" по умолчанию
        sheet_name = payload.sheet_name or gsheets_service.settings.gsheets_worksheet_name or "Лист1"
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
    
    # 4. Парсим заголовки (первая строка)
    headers = rows[0] if rows else []
    if not headers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Таблица не содержит заголовков",
        )
    
    # Создаем маппинг колонок
    column_mapping = payload.column_mapping or {}

    # Поддержка двух форматов column_mapping:
    # 1) field -> header (ожидаемый парсером)
    # 2) header -> field (встречается в примерах клиентов)
    if column_mapping:
        known_fields = {
            "external_uid",
            "type",
            "stem",
            "options",
            "correct_answer",
            "max_score",
            "course_uid",
            "difficulty_code",
            "difficulty_uid",
            "code",
            "title",
            "prompt",
            "input_link",
            "accepted_answers",
        }
        if not any(k in known_fields for k in column_mapping.keys()) and any(
            v in known_fields for v in column_mapping.values()
        ):
            # header -> field  ==>  field -> header
            column_mapping = {field: header for header, field in column_mapping.items()}

    if not column_mapping:
        # Стандартный маппинг: ищем колонки по названиям
        column_mapping = {}
        for idx, header in enumerate(headers):
            header_lower = header.lower().strip()
            # Маппинг стандартных названий
            if header_lower in ("external_uid", "uid", "id", "код"):
                column_mapping["external_uid"] = header
            elif header_lower in ("course_uid", "course_code", "course code", "курс", "course"):
                # курс для строки (если указан, переопределяет course_id/course_code из payload)
                column_mapping["course_uid"] = header
            elif header_lower in ("difficulty_uid", "difficulty uid"):
                # Маппинг через БД: значение — difficulties.uid (например theory, normal)
                column_mapping["difficulty_uid"] = header
            elif header_lower in ("difficulty_code", "difficulty", "difficulty code", "сложность"):
                column_mapping["difficulty_code"] = header
            elif header_lower in ("type", "тип", "task_type"):
                column_mapping["type"] = header
            elif header_lower in ("stem", "question", "вопрос", "задача"):
                column_mapping["stem"] = header
            elif header_lower in ("options", "варианты", "answers"):
                column_mapping["options"] = header
            elif header_lower in ("correct_answer", "correct", "правильный", "ответ"):
                column_mapping["correct_answer"] = header
            elif header_lower in ("max_score", "score", "балл", "баллы"):
                column_mapping["max_score"] = header
            elif header_lower in ("code", "код"):
                column_mapping["code"] = header
            elif header_lower in ("title", "название"):
                column_mapping["title"] = header
            elif header_lower in ("prompt", "подсказка"):
                column_mapping["prompt"] = header
            elif header_lower in ("input_link", "ссылка", "link"):
                column_mapping["input_link"] = header
            elif header_lower in ("accepted_answers", "принятые"):
                column_mapping["accepted_answers"] = header

    has_row_difficulty_code = "difficulty_code" in column_mapping and bool(column_mapping.get("difficulty_code"))
    has_row_difficulty_uid = "difficulty_uid" in column_mapping and bool(column_mapping.get("difficulty_uid"))
    has_row_difficulty = has_row_difficulty_code or has_row_difficulty_uid
    if not difficulty_id and not has_row_difficulty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо указать difficulty_id/difficulty_code в запросе или добавить колонку difficulty_uid/difficulty_code в таблицу",
        )

    # Если курс не задан в payload, допускаем курс "на строке" через колонку course_uid
    has_row_course_uid = "course_uid" in column_mapping and bool(column_mapping.get("course_uid"))
    if not course_id and not has_row_course_uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Необходимо указать course_id/course_code или добавить колонку course_uid в таблицу"
                + (f". course_code='{invalid_payload_course_code}' не найден" if invalid_payload_course_code else "")
            ),
        )

    # Если есть course_uid на строке — заранее получаем id курсов одним запросом
    course_uid_to_id: dict[str, int] = {}
    course_uid_column = column_mapping.get("course_uid") if has_row_course_uid else None
    if course_uid_column:
        from app.models.courses import Courses

        requested_uids: set[str] = set()
        for row_data in rows[1:]:
            if not row_data:
                continue
            # берем значение по индексу колонки
            row_dict: dict[str, str] = {}
            for idx, value in enumerate(row_data):
                if idx < len(headers):
                    row_dict[headers[idx]] = str(value) if value else ""
            uid = (row_dict.get(course_uid_column) or "").strip()
            if uid:
                requested_uids.add(uid)

        if requested_uids:
            res = await db.execute(
                select(Courses.course_uid, Courses.id).where(Courses.course_uid.in_(list(requested_uids)))
            )
            course_uid_to_id = {course_uid: course_id_ for course_uid, course_id_ in res.all()}

    # Сложность на строке: по difficulty_uid (маппинг через БД) или по difficulty_code
    difficulty_uid_to_id: dict[str, int] = {}
    difficulty_code_to_id: dict[str, int] = {}
    difficulty_uid_column = column_mapping.get("difficulty_uid") if has_row_difficulty_uid else None
    difficulty_code_column = column_mapping.get("difficulty_code") if has_row_difficulty_code else None
    if difficulty_uid_column or difficulty_code_column:
        from sqlalchemy import func
        from app.models.difficulty_levels import DifficultyLevels

        if difficulty_uid_column:
            requested_uids_diff: set[str] = set()
            for row_data in rows[1:]:
                if not row_data:
                    continue
                row_dict_tmp: dict[str, str] = {}
                for idx, value in enumerate(row_data):
                    if idx < len(headers):
                        row_dict_tmp[headers[idx]] = str(value) if value else ""
                uid_val = (row_dict_tmp.get(difficulty_uid_column) or "").strip()
                if uid_val:
                    requested_uids_diff.add(uid_val)
            if requested_uids_diff:
                res = await db.execute(
                    select(DifficultyLevels.id, DifficultyLevels.uid).where(
                        DifficultyLevels.uid.in_(list(requested_uids_diff))
                    )
                )
                difficulty_uid_to_id = {row.uid: row.id for row in res.all()}
        if difficulty_code_column:
            requested_codes: set[str] = set()
            for row_data in rows[1:]:
                if not row_data:
                    continue
                row_dict_tmp2: dict[str, str] = {}
                for idx, value in enumerate(row_data):
                    if idx < len(headers):
                        row_dict_tmp2[headers[idx]] = str(value) if value else ""
                code = (row_dict_tmp2.get(difficulty_code_column) or "").strip().upper()
                if code:
                    requested_codes.add(code)
            if requested_codes:
                res = await db.execute(
                    select(DifficultyLevels.id, DifficultyLevels.code).where(
                        func.upper(DifficultyLevels.code).in_(list(requested_codes))
                    )
                )
                difficulty_code_to_id = {row.code.upper(): row.id for row in res.all()}
    
    # 5. Парсим строки данных
    parsed_tasks: List[Dict[str, Any]] = []
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
            # Определяем курс для текущей строки:
            # - если в строке заполнен course_uid → используем его
            # - иначе берём course_id из payload (если задан)
            row_course_uid = None
            effective_course_id = course_id
            if course_uid_column:
                row_course_uid = (row_dict.get(course_uid_column) or "").strip() or None
                if row_course_uid:
                    effective_course_id = course_uid_to_id.get(row_course_uid)
                    if not effective_course_id:
                        errors.append(
                            GoogleSheetsImportError(
                                row_index=row_index,
                                external_uid=row_dict.get(column_mapping.get("external_uid", ""), None) or None,
                                error=f"Курс с course_uid='{row_course_uid}' не найден",
                            )
                        )
                        continue

            if not effective_course_id:
                errors.append(
                    GoogleSheetsImportError(
                        row_index=row_index,
                        external_uid=row_dict.get(column_mapping.get("external_uid", ""), None) or None,
                        error="Не указан курс: заполните course_uid в строке или передайте course_id/course_code в запросе",
                    )
                )
                continue

            # Сложность для текущей строки: из колонки difficulty_uid (маппинг через БД) или difficulty_code или из payload
            row_difficulty_code = None
            effective_difficulty_id = difficulty_id
            if difficulty_uid_column:
                row_difficulty_uid = (row_dict.get(difficulty_uid_column) or "").strip() or None
                if row_difficulty_uid:
                    effective_difficulty_id = difficulty_uid_to_id.get(row_difficulty_uid)
                    if not effective_difficulty_id:
                        errors.append(
                            GoogleSheetsImportError(
                                row_index=row_index,
                                external_uid=row_dict.get(column_mapping.get("external_uid", ""), None) or None,
                                error=f"Уровень сложности с uid '{row_difficulty_uid}' не найден в БД (difficulties.uid)",
                            )
                        )
                        continue
            elif difficulty_code_column:
                row_difficulty_code = (row_dict.get(difficulty_code_column) or "").strip().upper() or None
                if row_difficulty_code:
                    effective_difficulty_id = difficulty_code_to_id.get(row_difficulty_code)
                    if not effective_difficulty_id:
                        errors.append(
                            GoogleSheetsImportError(
                                row_index=row_index,
                                external_uid=row_dict.get(column_mapping.get("external_uid", ""), None) or None,
                                error=f"Уровень сложности с кодом '{row_difficulty_code}' не найден",
                            )
                        )
                        continue
            if not effective_difficulty_id:
                errors.append(
                    GoogleSheetsImportError(
                        row_index=row_index,
                        external_uid=row_dict.get(column_mapping.get("external_uid", ""), None) or None,
                        error="Не указана сложность: заполните difficulty_uid/difficulty_code в строке или передайте difficulty_id/difficulty_code в запросе",
                    )
                )
                continue

            # Парсим строку
            task_content, solution_rules, metadata = parser_service.parse_task_row(
                row=row_dict,
                column_mapping=column_mapping,
                course_id=effective_course_id,
                difficulty_id=effective_difficulty_id,
            )
            
            # Валидируем задачу
            is_valid, validation_errors = await tasks_service.validate_task_import(
                db,
                task_content=task_content.model_dump(),
                solution_rules=solution_rules.model_dump(),
                difficulty_id=effective_difficulty_id,
                difficulty_code=row_difficulty_code or payload.difficulty_code,
                course_code=row_course_uid or payload.course_code,
                external_uid=metadata["external_uid"],
            )
            
            if not is_valid:
                errors.append(GoogleSheetsImportError(
                    row_index=row_index,
                    external_uid=metadata.get("external_uid"),
                    error="; ".join(validation_errors),
                ))
                continue
            
            # Формируем данные для bulk_upsert
            task_data = {
                "external_uid": metadata["external_uid"],
                "course_id": effective_course_id,
                "difficulty_id": effective_difficulty_id,
                "task_content": task_content.model_dump(),
                "solution_rules": solution_rules.model_dump(),
                "max_score": metadata["max_score"],
            }
            
            parsed_tasks.append(task_data)
            
        except Exception as e:
            logger.exception("Ошибка при парсинге строки %d: %s", row_index, e)
            errors.append(GoogleSheetsImportError(
                row_index=row_index,
                external_uid=None,
                error=f"Ошибка парсинга: {str(e)}",
            ))
            continue
    
    if not parsed_tasks:
        return GoogleSheetsImportResponse(
            imported=0,
            updated=0,
            errors=errors,
            total_rows=len(rows) - 1,  # Без заголовка
        )
    
    # 6. Импортируем задачи (если не dry_run)
    imported = 0
    updated = 0
    
    if not payload.dry_run:
        try:
            results = await tasks_service.bulk_upsert(db, parsed_tasks)
            for external_uid, action, task_id in results:
                if action == "created":
                    imported += 1
                elif action == "updated":
                    updated += 1
            logger.info("Imported: %d, Updated: %d", imported, updated)
        except Exception as e:
            logger.exception("Ошибка при импорте задач: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка при импорте задач: {str(e)}",
            ) from e
    else:
        # В dry_run режиме считаем все как "would be imported"
        imported = len(parsed_tasks)
        logger.info("Dry run: would import %d tasks", imported)
    
    return GoogleSheetsImportResponse(
        imported=imported,
        updated=updated,
        errors=errors,
        total_rows=len(rows) - 1,  # Без заголовка
    )