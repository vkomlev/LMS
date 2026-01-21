from __future__ import annotations
from typing import Optional, List, Dict
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class AccessLevel(str, Enum):
    self_guided = "self_guided"
    auto_check = "auto_check"
    manual_check = "manual_check"
    group_sessions = "group_sessions"
    personal_teacher = "personal_teacher"


class CourseCreate(BaseModel):
    """
    Создание курса.

    Правила:
    - `parent_course_id` может быть `null` → курс корневой.
    - Валидация циклов в иерархии выполняется триггером БД.
    """
    title: str = Field(..., description="Название курса", examples=["Основы Python"])
    access_level: AccessLevel = Field(..., description="Тип доступа/проверки", examples=["auto_check"])
    description: Optional[str] = Field(None, description="Описание курса", examples=["Введение в Python: переменные, типы, условия, циклы"])
    parent_course_id: Optional[int] = Field(
        None,
        description="ID родительского курса (если курс является модулем внутри другого курса). null = корневой курс.",
        examples=[None, 1],
    )
    is_required: bool = Field(False, description="Обязательный ли курс", examples=[False])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Основы Python",
                    "access_level": "auto_check",
                    "description": "Введение в Python: переменные, типы, условия, циклы",
                    "parent_course_id": None,
                    "is_required": False,
                }
            ]
        }
    )


class CourseUpdate(BaseModel):
    """
    Частичное/полное обновление курса.

    Правила:
    - Любое поле может быть опущено.
    - Валидация циклов (если меняется parent) выполняется триггером БД.
    """
    title: Optional[str] = Field(None, description="Название курса", examples=["Python: продвинутый уровень"])
    access_level: Optional[AccessLevel] = Field(None, description="Тип доступа/проверки", examples=["manual_check"])
    description: Optional[str] = Field(None, description="Описание курса")
    parent_course_id: Optional[int] = Field(
        None,
        description="ID родительского курса. null = сделать курс корневым.",
        examples=[None, 1],
    )
    is_required: Optional[bool] = Field(None, description="Обязательный ли курс", examples=[True, False])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Python: продвинутый уровень",
                    "description": "Генераторы, декораторы, контекстные менеджеры",
                    "is_required": False,
                }
            ]
        }
    )


class CourseRead(BaseModel):
    id: int
    title: str
    access_level: AccessLevel
    description: Optional[str]
    parent_course_id: Optional[int]
    created_at: datetime
    is_required: bool
    course_uid: str | None = Field(
        None,
        description="Внешний код курса (для импорта/интеграций), например 'COURSE-PY-01'. Может быть null для старых курсов.",
        examples=["COURSE-PY-01", None],
    )

    model_config = ConfigDict(from_attributes=True)


class CourseReadWithChildren(CourseRead):
    """Схема курса с вложенными детьми (прямые потомки)."""
    children: List["CourseReadWithChildren"] = []

    model_config = ConfigDict(from_attributes=True)


class CourseTreeRead(CourseRead):
    """Схема курса с полным деревом потомков (рекурсивная структура)."""
    children: List["CourseTreeRead"] = []

    model_config = ConfigDict(from_attributes=True)


# Обновление моделей для корректной работы рекурсивных типов
CourseReadWithChildren.model_rebuild()
CourseTreeRead.model_rebuild()


class CourseMoveRequest(BaseModel):
    """
    Перемещение курса в иерархии.

    Правила:
    - `new_parent_id = null` → курс становится корневым.
    - Триггер БД запрещает циклы и самоссылки.
    """
    new_parent_id: Optional[int] = Field(
        None,
        description="ID нового родительского курса. Если null, курс становится корневым.",
        examples=[None, 10],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"new_parent_id": 10},
                {"new_parent_id": None},
            ]
        }
    )


# ---------- Импорт из Google Sheets ----------


class GoogleSheetsImportRequest(BaseModel):
    """
    Запрос на импорт курсов из Google Sheets.
    
    Импортирует курсы из указанной Google Sheets таблицы в систему LMS.
    Поддерживает иерархию курсов (parent_course_uid) и зависимости (required_courses_uid).
    
    **Обязательные параметры:**
    - `spreadsheet_url` - URL таблицы или spreadsheet_id
    
    **Рекомендации:**
    - Используйте `dry_run: true` для предварительной проверки данных
    - Убедитесь, что Service Account имеет доступ к таблице
    - Проверьте формат данных в таблице перед импортом
    - Курсы импортируются как upsert по `course_uid` (если курс существует - обновляется, иначе создается)
    """
    spreadsheet_url: str = Field(
        ...,
        description=(
            "URL таблицы Google Sheets или spreadsheet_id. "
            "Примеры: "
            "'https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit' "
            "или '1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk'"
        ),
        examples=[
            "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
            "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
        ],
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description=(
            "Название листа в таблице. "
            "Если не указано, используется 'Courses' по умолчанию. "
            "Примеры: 'Courses', 'Курсы', 'Sheet1'"
        ),
        examples=["Courses", "Курсы"],
    )
    column_mapping: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Кастомный маппинг колонок таблицы на поля курса. "
            "Если не указан, используется автоматический маппинг по стандартным названиям. "
            "Формат: {'название_колонки_в_таблице': 'поле_курса'}. "
            "Доступные поля: course_uid, title, description, access_level, "
            "parent_course_uid, required_courses_uid, is_required"
        ),
        examples=[
            {
                "Код": "course_uid",
                "Название": "title",
                "Описание": "description",
                "Уровень доступа": "access_level",
                "Родитель": "parent_course_uid",
                "Зависимости": "required_courses_uid",
                "Обязательный": "is_required",
            }
        ],
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Режим проверки без сохранения. "
            "Если True, данные валидируются, но не сохраняются в БД. "
            "Рекомендуется использовать для предварительной проверки перед реальным импортом. "
            "В ответе imported будет показывать количество курсов, которые были бы импортированы."
        ),
        examples=[True, False],
    )


class GoogleSheetsImportError(BaseModel):
    """
    Информация об ошибке при импорте одного курса.
    
    Содержит детали ошибки для конкретной строки таблицы,
    что позволяет быстро найти и исправить проблему.
    """
    row_index: int = Field(
        ...,
        description=(
            "Номер строки в таблице, где произошла ошибка. "
            "Начинается с 1 (первая строка данных, не считая заголовок). "
            "Пример: если ошибка в строке 3 таблицы (после заголовка), row_index = 3"
        ),
        examples=[1, 3, 5],
    )
    course_uid: Optional[str] = Field(
        default=None,
        description=(
            "course_uid курса, если удалось извлечь из строки. "
            "Может быть None, если ошибка произошла до парсинга course_uid."
        ),
        examples=["COURSE-PY-01", None],
    )
    error: str = Field(
        ...,
        description=(
            "Текст ошибки с описанием проблемы. "
            "Примеры: 'Обязательное поле course_uid пустое', "
            "'Родительский курс с course_uid COURSE-PY-01 не найден'"
        ),
        examples=[
            "Обязательное поле 'course_uid' (колонка 'course_uid') пустое",
            "Родительский курс с course_uid 'COURSE-PY-01' не найден",
        ],
    )


class GoogleSheetsImportResponse(BaseModel):
    """
    Ответ на запрос импорта курсов из Google Sheets.
    
    Содержит детальный отчет об импорте: количество успешно импортированных курсов,
    обновленных курсов, список ошибок и общее количество обработанных строк.
    
    **Интерпретация результатов:**
    - `imported` - новые курсы, добавленные в БД
    - `updated` - существующие курсы, обновленные по course_uid
    - `errors` - список ошибок для строк, которые не удалось импортировать
    - `total_rows` - общее количество строк данных (без заголовка)
    
    **Успешный импорт:** `errors.length === 0` и `imported + updated === total_rows`
    """
    imported: int = Field(
        ...,
        description=(
            "Количество успешно импортированных (созданных) курсов. "
            "В режиме dry_run показывает количество курсов, которые были бы импортированы."
        ),
        examples=[10, 0],
        ge=0,
    )
    updated: int = Field(
        ...,
        description=(
            "Количество обновленных курсов. "
            "Курс обновляется, если course_uid уже существует в БД. "
            "В режиме dry_run всегда 0."
        ),
        examples=[0, 3],
        ge=0,
    )
    errors: List[GoogleSheetsImportError] = Field(
        default_factory=list,
        description=(
            "Список ошибок при импорте. "
            "Каждая ошибка содержит номер строки, course_uid (если удалось извлечь) "
            "и текст ошибки. "
            "Пустой список означает, что все курсы успешно импортированы."
        ),
        examples=[
            [],
            [
                {
                    "row_index": 3,
                    "course_uid": "COURSE-PY-03",
                    "error": "Родительский курс с course_uid 'COURSE-PY-01' не найден"
                }
            ],
        ],
    )
    total_rows: int = Field(
        ...,
        description=(
            "Общее количество обработанных строк данных (без учета заголовка). "
            "Включает как успешно импортированные, так и строки с ошибками."
        ),
        examples=[10, 15],
        ge=0,
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "imported": 10,
                    "updated": 0,
                    "errors": [],
                    "total_rows": 10,
                },
                {
                    "imported": 8,
                    "updated": 2,
                    "errors": [
                        {
                            "row_index": 3,
                            "course_uid": "COURSE-PY-03",
                            "error": "Родительский курс с course_uid 'COURSE-PY-01' не найден"
                        }
                    ],
                    "total_rows": 10,
                },
            ]
        }
    )
