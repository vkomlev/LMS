from __future__ import annotations

from fastapi import APIRouter, Depends, Body, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
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
)
async def validate_task_endpoint(
    payload: TaskValidateRequest = Body(
        ...,
        description="Данные задания для предварительной валидации",
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
)
async def bulk_upsert_tasks_endpoint(
    payload: TaskBulkUpsertRequest = Body(
        ...,
        description="Список задач для массового upsert'а",
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
    if not course_id and payload.course_code:
        try:
            course = await courses_service.get_by_course_uid(db, payload.course_code)
            course_id = course.id
        except Exception as e:
            logger.exception("Ошибка при поиске курса: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Курс с кодом '{payload.course_code}' не найден",
            ) from e
    
    if not course_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо указать course_id или course_code",
        )
    
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
    
    if not difficulty_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо указать difficulty_id или difficulty_code",
        )
    
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
    if not column_mapping:
        # Стандартный маппинг: ищем колонки по названиям
        column_mapping = {}
        for idx, header in enumerate(headers):
            header_lower = header.lower().strip()
            # Маппинг стандартных названий
            if header_lower in ("external_uid", "uid", "id", "код"):
                column_mapping["external_uid"] = header
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
            # Парсим строку
            task_content, solution_rules, metadata = parser_service.parse_task_row(
                row=row_dict,
                column_mapping=column_mapping,
                course_id=course_id,
                difficulty_id=difficulty_id,
            )
            
            # Валидируем задачу
            is_valid, validation_errors = await tasks_service.validate_task_import(
                db,
                task_content=task_content.model_dump(),
                solution_rules=solution_rules.model_dump(),
                difficulty_id=difficulty_id,
                difficulty_code=payload.difficulty_code,  # Передаем для валидации
                course_code=payload.course_code,  # Передаем для валидации (если есть)
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
                "course_id": course_id,
                "difficulty_id": difficulty_id,
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