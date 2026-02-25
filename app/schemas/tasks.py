from __future__ import annotations

from typing import Any, Optional, List, Literal, Dict, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


def extract_hints_from_task_content(task_content: Any) -> Tuple[List[str], List[str], bool]:
    """
    Извлечь hints_text, hints_video из task_content (JSON).
    Нормализация: только строковые элементы; null и не-строки отфильтровываются.
    has_hints = (len(hints_text) > 0 or len(hints_video) > 0).
    Не падает при отсутствии/невалидном типе полей — возвращает [], [], False.
    """
    if not isinstance(task_content, dict):
        return ([], [], False)
    hints_text_raw = task_content.get("hints_text")
    hints_video_raw = task_content.get("hints_video")
    hints_text: List[str] = []
    hints_video: List[str] = []
    if isinstance(hints_text_raw, list):
        hints_text = [x for x in hints_text_raw if isinstance(x, str)]
    if isinstance(hints_video_raw, list):
        hints_video = [x for x in hints_video_raw if isinstance(x, str)]
    has_hints = len(hints_text) > 0 or len(hints_video) > 0
    return (hints_text, hints_video, has_hints)


class TaskCreate(BaseModel):
    """
    Схема создания задания.

    external_uid и max_score опциональны, чтобы не ломать существующие клиенты:
    - external_uid — устойчивый ID из внешней системы (для импорта),
    - max_score   — максимальный балл за задачу.
    """
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any] = None

    external_uid: Optional[str] = None
    max_score: Optional[int] = None


class TaskUpdate(BaseModel):
    """
    Схема обновления задания (полевая, все поля опциональны).
    """
    task_content: Optional[Any] = None
    course_id: Optional[int] = None
    difficulty_id: Optional[int] = None
    solution_rules: Optional[Any] = None

    external_uid: Optional[str] = None
    max_score: Optional[int] = None


class TaskRead(BaseModel):
    """
    Схема чтения задания.
    Learning Engine V1, этап 5: hints_text, hints_video, has_hints из task_content.
    """
    id: int
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any]

    external_uid: Optional[str] = None
    max_score: Optional[int] = None

    hints_text: List[str] = Field(
        default_factory=list,
        description="Текстовые подсказки из task_content.hints_text.",
    )
    hints_video: List[str] = Field(
        default_factory=list,
        description="Ссылки на видео-подсказки из task_content.hints_video.",
    )
    has_hints: bool = Field(
        default=False,
        description="True, если есть хотя бы одна подсказка (текст или видео).",
    )

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def fill_hints_from_task_content(self) -> "TaskRead":
        if isinstance(self.task_content, dict):
            ht, hv, hh = extract_hints_from_task_content(self.task_content)
            object.__setattr__(self, "hints_text", ht)
            object.__setattr__(self, "hints_video", hv)
            object.__setattr__(self, "has_hints", hh)
        return self

class TaskUpsertItem(BaseModel):
    """
    Один элемент для массового upsert'а задачи.
    Поля соответствуют структуре TaskCreate, плюс обязательный external_uid.
    """
    external_uid: str
    course_id: int
    difficulty_id: int
    task_content: Any
    solution_rules: Any | None = None
    max_score: int | None = None


class TaskBulkUpsertRequest(BaseModel):
    """
    Тело запроса для массового upsert'а задач.
    """
    items: List[TaskUpsertItem]


class TaskBulkUpsertResultItem(BaseModel):
    """
    Один элемент результата bulk-upsert'а.
    """
    external_uid: str
    action: Literal["created", "updated"]
    id: int


class TaskBulkUpsertResponse(BaseModel):
    """
    Ответ bulk-upsert'а задач.
    """
    results: List[TaskBulkUpsertResultItem]

class TaskValidateRequest(BaseModel):
    """
    Запрос на предварительную валидацию задания перед импортом.

    Можно передавать либо difficulty_code, либо difficulty_id (или оба).
    """
    task_content: Any
    solution_rules: Any | None = None

    difficulty_code: str | None = None
    difficulty_id: int | None = None

    course_code: str | None = None
    external_uid: str | None = None


class TaskValidateResponse(BaseModel):
    """
    Результат предварительной валидации.
    """
    is_valid: bool
    errors: List[str]

class TaskFindByExternalRequest(BaseModel):
    uids: list[str]


class TaskFindByExternalItem(BaseModel):
    external_uid: str
    id: int


class TaskFindByExternalResponse(BaseModel):
    items: list[TaskFindByExternalItem]


# ---------- Импорт из Google Sheets ----------


class GoogleSheetsImportRequest(BaseModel):
    """
    Запрос на импорт задач из Google Sheets.
    
    Импортирует задачи из указанной Google Sheets таблицы в систему LMS.
    Поддерживает все типы задач: SC, MC, SA, SA_COM, TA.
    
    **Обязательные параметры:**
    - `spreadsheet_url` - URL таблицы или spreadsheet_id
    - `difficulty_code` ИЛИ `difficulty_id` - уровень сложности

    **Курс для заданий:**
    - либо укажите `course_code` / `course_id` (один курс на весь импорт),
    - либо добавьте в таблицу колонку `course_uid` и заполняйте курс для каждой строки (курс на строке).
    
    **Рекомендации:**
    - Используйте `dry_run: true` для предварительной проверки данных
    - Убедитесь, что Service Account имеет доступ к таблице
    - Проверьте формат данных в таблице перед импортом
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
            "Если не указано, используется значение из настроек (GSHEETS_WORKSHEET_NAME) "
            "или 'Лист1' по умолчанию. "
            "Примеры: 'Лист1', 'Задания', 'Sheet1'"
        ),
        examples=["Лист1", "Задания"],
    )
    column_mapping: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Кастомный маппинг колонок таблицы на поля задачи. "
            "Если не указан, используется автоматический маппинг по стандартным названиям. "
            "Формат: {'поле_задачи': 'название_колонки_в_таблице'}. "
            "Доступные поля: external_uid, type, stem, options, correct_answer, max_score, "
            "course_uid, code, title, prompt, input_link, accepted_answers"
        ),
        examples=[
            {
                "external_uid": "ID",
                "type": "Тип",
                "stem": "Вопрос",
                "options": "Варианты",
                "correct_answer": "Правильный ответ",
                "course_uid": "Курс",
            }
        ],
    )
    course_code: Optional[str] = Field(
        default=None,
        description=(
            "Код курса (courses.course_uid) для импортируемых задач. "
            "Если в таблице нет колонки course_uid, обязательно указать либо course_code, либо course_id. "
            "Примеры: 'PY', 'COURSE-PY-01'. "
            "Получить список курсов: GET /api/v1/meta/tasks"
        ),
        examples=["PY", "COURSE-PY-01"],
    )
    course_id: Optional[int] = Field(
        default=None,
        description=(
            "ID курса. Если указан, используется вместо course_code. "
            "Если в таблице нет колонки course_uid, обязательно указать либо course_code, либо course_id."
        ),
        examples=[1, 2],
    )
    difficulty_code: Optional[str] = Field(
        default=None,
        description=(
            "Код уровня сложности (difficulties.code) для импортируемых задач. "
            "Обязательно указать либо difficulty_code, либо difficulty_id. "
            "Примеры: 'NORMAL', 'EASY', 'HARD', 'THEORY', 'PROJECT'. "
            "Получить список: GET /api/v1/meta/tasks"
        ),
        examples=["NORMAL", "EASY", "HARD"],
    )
    difficulty_id: Optional[int] = Field(
        default=None,
        description=(
            "ID уровня сложности. Если указан, используется вместо difficulty_code. "
            "Обязательно указать либо difficulty_code, либо difficulty_id."
        ),
        examples=[1, 3, 4],
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Режим проверки без сохранения. "
            "Если True, данные валидируются, но не сохраняются в БД. "
            "Рекомендуется использовать для предварительной проверки перед реальным импортом. "
            "В ответе imported будет показывать количество задач, которые были бы импортированы."
        ),
        examples=[True, False],
    )


class GoogleSheetsImportError(BaseModel):
    """
    Информация об ошибке при импорте одной задачи.
    
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
    external_uid: Optional[str] = Field(
        default=None,
        description=(
            "external_uid задачи, если удалось извлечь из строки. "
            "Может быть None, если ошибка произошла до парсинга external_uid."
        ),
        examples=["TASK-SC-001", None],
    )
    error: str = Field(
        ...,
        description=(
            "Текст ошибки с описанием проблемы. "
            "Примеры: 'Обязательное поле external_uid пустое', "
            "'Для задач типа SC должен быть указан ровно один правильный вариант'"
        ),
        examples=[
            "Обязательное поле 'external_uid' (колонка 'external_uid') пустое",
            "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2",
        ],
    )


class GoogleSheetsImportResponse(BaseModel):
    """
    Ответ на запрос импорта из Google Sheets.
    
    Содержит детальный отчет об импорте: количество успешно импортированных задач,
    обновленных задач, список ошибок и общее количество обработанных строк.
    
    **Интерпретация результатов:**
    - `imported` - новые задачи, добавленные в БД
    - `updated` - существующие задачи, обновленные по external_uid
    - `errors` - список ошибок для строк, которые не удалось импортировать
    - `total_rows` - общее количество строк данных (без заголовка)
    
    **Успешный импорт:** `errors.length === 0` и `imported + updated === total_rows`
    """
    imported: int = Field(
        ...,
        description=(
            "Количество успешно импортированных (созданных) задач. "
            "В режиме dry_run показывает количество задач, которые были бы импортированы."
        ),
        examples=[10, 0],
        ge=0,
    )
    updated: int = Field(
        ...,
        description=(
            "Количество обновленных задач. "
            "Задача обновляется, если external_uid уже существует в БД. "
            "В режиме dry_run всегда 0."
        ),
        examples=[0, 3],
        ge=0,
    )
    errors: List[GoogleSheetsImportError] = Field(
        default_factory=list,
        description=(
            "Список ошибок при импорте. "
            "Каждая ошибка содержит номер строки, external_uid (если удалось извлечь) "
            "и текст ошибки. "
            "Пустой список означает, что все задачи успешно импортированы."
        ),
        examples=[
            [],
            [
                {
                    "row_index": 3,
                    "external_uid": "TASK-SC-003",
                    "error": "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант"
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
        examples=[10, 25],
        ge=0,
    )