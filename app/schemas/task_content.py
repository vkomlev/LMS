from __future__ import annotations

from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TaskType = Literal["SC", "MC", "SA", "SA_COM", "TA", "SC_Qw", "MC_Qw", "TBL_COM"]

# Типы квиз-вопросов со шкалами (tsk-122, ADR-0003): без «правильного» варианта,
# за каждый выбор начисляются баллы по шкалам.
QUIZ_TASK_TYPES: tuple[str, ...] = ("SC_Qw", "MC_Qw")

# Типы с кратким ответом в поле `value` и правилами в `solution_rules.short_answer`
# (tsk-366: TBL_COM — тот же блок правил, но сравнение поячеечное).
SHORT_ANSWER_TASK_TYPES: tuple[str, ...] = ("SA", "SA_COM", "TBL_COM")

# Типы «с комментарием»: ученик прикладывает пояснение/код и файл, преподаватель
# может пересмотреть ответ вручную. TBL_COM заведён по образцу SA_COM (tsk-366),
# поэтому везде, где whitelist перечисляет SA_COM, он идёт рядом.
COMMENT_TASK_TYPES: tuple[str, ...] = ("SA_COM", "TBL_COM")

# Типы, чьи ответы проходят через очередь ручной проверки преподавателя
# (обязательную при manual_review_required, опциональную — вторичным просмотром).
MANUAL_REVIEW_TASK_TYPES: tuple[str, ...] = ("SA_COM", "TBL_COM", "TA")

# Типы с табличным ответом (tsk-366): значения разделены пробельными символами —
# ячейки в ряду пробелом, ряды переводом строки.
TABLE_TASK_TYPES: tuple[str, ...] = ("TBL_COM",)


class TaskMedia(BaseModel):
    """
    Мультимедийные ресурсы, связанные с заданием.
    """

    image_url: Optional[str] = Field(
        default=None,
        description="URL изображения для задания (если есть).",
    )
    audio_url: Optional[str] = Field(
        default=None,
        description="URL аудио для задания (если есть).",
    )
    video_url: Optional[str] = Field(
        default=None,
        description="URL видео для задания (если есть).",
    )


class TaskOption(BaseModel):
    """
    Вариант ответа для задач с выбором (SC/MC/SA_COM).
    """

    id: str = Field(
        ...,
        description="Устойчивый ID варианта ответа (используется в правилах проверки и ответах ученика). Обычно A, B, C, D...",
        examples=["A", "B", "C", "opt1", "opt2"],
    )
    text: str = Field(
        ...,
        description="Текст варианта ответа.",
        examples=[
            "Именованная область памяти для хранения данных",
            "Функция для вывода данных",
            "Тип данных",
        ],
    )
    explanation: Optional[str] = Field(
        default=None,
        description="Пояснение методиста к варианту (опционально, для обратной связи). Показывается ученику после ответа.",
        examples=[
            "Правильно! Переменная действительно хранит данные в памяти.",
            "Неверно. Функция print() используется для вывода, а не переменная.",
            None,
        ],
    )
    is_active: bool = Field(
        default=True,
        description="Флаг активности варианта. Можно использовать для временного скрытия варианта.",
        examples=[True, False],
    )
    scores: Optional[Dict[str, int]] = Field(
        default=None,
        description=(
            "Вклад варианта в баллы по шкалам для квиз-вопросов (SC_Qw/MC_Qw, tsk-122). "
            "Ключи — имена шкал (должны входить в TaskContent.scales), значения — баллы. "
            "Для обычных SC/MC не используется."
        ),
        examples=[{"информатика": 2}, {"информатика": 1, "python": 1}, None],
    )


class TaskTableLayout(BaseModel):
    """
    Раскладка табличного ответа (TBL_COM, tsk-366).

    Живёт в `task_content`, а НЕ в правилах проверки, потому что это то, что
    клиент обязан показать ученику: сколько столбцов в ряду и как они называются.
    `solution_rules` клиенту не отдаётся (защита от слива ответа), поэтому
    раскладка обязана лежать здесь.

    Число СТОЛБЦОВ раскрывать безопасно: оно задано условием задачи («выпишите
    число и результат деления»). Число РЯДОВ намеренно не хранится — в задании
    №25 ЕГЭ количество найденных чисел само по себе часть ответа, и фиксированная
    сетка выдала бы его ученику.
    """

    columns: int = Field(
        default=2,
        ge=1,
        le=9,
        description=(
            "Количество столбцов в ряду ответа. Задано условием задачи, "
            "показывается ученику. Ограничение 9 — практический предел таблицы ЕГЭ."
        ),
        examples=[1, 2, 3],
    )
    column_titles: Optional[List[str]] = Field(
        default=None,
        description=(
            "Подписи столбцов для сетки ввода (опционально). Длина должна совпадать "
            "с `columns`."
        ),
        examples=[["число", "результат деления"], None],
    )


class TaskContent(BaseModel):
    """
    Структура JSON-поля tasks.task_content.

    Описывает то, что видит ученик: формулировка, подсказки, варианты, теги и т.п.
    """

    model_config = ConfigDict(extra="allow")

    type: TaskType = Field(
        ...,
        description="Тип задачи: SC | MC | SA | SA_COM | TA | SC_Qw | MC_Qw | TBL_COM.",
        examples=["SC", "MC", "SA", "SA_COM", "TA", "SC_Qw", "MC_Qw", "TBL_COM"],
    )
    code: Optional[str] = Field(
        default=None,
        description="Внутренний код задачи (опционально, может дублировать external_uid).",
        examples=["PY-VAR-001", "TASK-001", None],
    )
    title: Optional[str] = Field(
        default=None,
        description="Краткое название задания (для списков, навигации).",
        examples=["Переменные Python", "Основы программирования", None],
    )
    stem: str = Field(
        ...,
        description="Основная формулировка вопроса/задачи.",
        examples=[
            "Что такое переменная в Python?",
            "Какой оператор используется для целочисленного деления?",
            "Объясните разницу между методами append() и extend() для списков.",
        ],
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Дополнительное пояснение или подсказка для ученика.",
        examples=[
            "Переменная хранит значение, которое можно изменять",
            "Оператор // возвращает целую часть от деления",
            None,
        ],
    )

    media: Optional[TaskMedia] = Field(
        default=None,
        description="Мультимедийные материалы для задания (изображение, аудио, видео).",
    )

    @field_validator("media", mode="before")
    @classmethod
    def _coerce_empty_media(cls, v: object) -> object:
        """tsk-325: импортированные задания ЕГЭ хранят media = [] (пустой список),
        а схема ждёт объект TaskMedia (dict) или null. Пустой список данных не несёт —
        приводим к None, иначе TaskContent.model_validate падает 500 и роняет приём
        ответа на строке attempts.py:446 (найдено живым прогоном; 1080 заданий,
        весь импортный ЕГЭ). Непустой список НЕ трогаем: это реальная потеря
        данных, её надо увидеть (валидация упадёт), а не спрятать."""
        if isinstance(v, list) and not v:
            return None
        return v

    options: Optional[List[TaskOption]] = Field(
        default=None,
        description="Варианты ответа для задач с выбором (SC/MC/SA_COM). Обязательно для SC/MC (минимум 2 варианта).",
        examples=[
            [
                {"id": "A", "text": "Именованная область памяти для хранения данных", "is_active": True},
                {"id": "B", "text": "Функция для вывода данных", "is_active": True},
                {"id": "C", "text": "Тип данных", "is_active": True},
            ],
            [
                {"id": "A", "text": "list()", "is_active": True},
                {"id": "B", "text": "[]", "is_active": True},
                {"id": "C", "text": "[1, 2, 3]", "is_active": True},
                {"id": "D", "text": "list(range(3))", "is_active": True},
            ],
            None,
        ],
    )

    table: Optional[TaskTableLayout] = Field(
        default=None,
        description=(
            "Раскладка табличного ответа (TBL_COM, tsk-366): сколько столбцов в ряду "
            "и как они подписаны. Для остальных типов не используется. Отсутствие "
            "блока у TBL_COM означает раскладку по умолчанию (2 столбца)."
        ),
    )

    scales: Optional[List[str]] = Field(
        default=None,
        description=(
            "Явное объявление шкал квиз-вопроса (SC_Qw/MC_Qw, tsk-122). Ключи scores "
            "у вариантов валидируются против этого списка: неизвестная шкала = ошибка. "
            "Обязательно для SC_Qw/MC_Qw, для остальных типов не используется."
        ),
        examples=[["информатика", "python"], None],
    )

    tags: Optional[List[str]] = Field(
        default=None,
        description="Список тегов (темы, EGE-коды, произвольные метки).",
    )
    difficulty_code: Optional[str] = Field(
        default=None,
        description="Код сложности (дублирует привязку к difficulties.code, например 'Easy', 'Normal').",
    )
    course_uid: Optional[str] = Field(
        default=None,
        description="Внешний код курса (courses.course_uid), используется для импорта.",
    )

    hints_text: List[str] = Field(
        default_factory=list,
        description=(
            "Текстовые подсказки для задания. Сохраняются в tasks.task_content "
            "и поднимаются в TaskRead.hints_text (см. extract_hints_from_task_content)."
        ),
    )
    hints_video: List[str] = Field(
        default_factory=list,
        description=(
            "Ссылки на видео-подсказки (VK/YouTube/etc) для задания. Сохраняются "
            "в tasks.task_content и поднимаются в TaskRead.hints_video."
        ),
    )
    has_hints: bool = Field(
        default=False,
        description=(
            "Кешированный флаг наличия подсказок (text или video). В TaskRead "
            "пересчитывается по факту наличия hints_text/hints_video; в task_content "
            "хранится для удобства внешних читателей."
        ),
    )

    @field_validator("title", mode="before")
    @classmethod
    def _empty_title_to_none(cls, value: object) -> object:
        """Пустую или пробельную строку названия привести к None.

        Часть импортов (D4-конвейер ContentBackbone: kompege/yandex/polyakov/
        sdamgia) присылает title="", из-за чего фронт SPW (tc.title ?? "Задача
        #N") не подставляет автоподпись и задание выглядит безымянным.
        Нормализуем на границе LMS, чтобы хранить единообразный null.
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_by_type(self) -> "TaskContent":
        """
        Базовая валидация структуры в зависимости от типа задачи.

        - SC/MC: должны быть как минимум 2 варианта;
        - SC_Qw/MC_Qw: минимум 2 варианта + объявление scales + scores с ключами из scales;
        - SA/TA: options не обязательны.
        - TBL_COM: подписи столбцов, если заданы, должны совпадать по числу со `columns`.
        - Проверка уникальности options[].id.
        """
        if self.table and self.table.column_titles is not None:
            if len(self.table.column_titles) != self.table.columns:
                raise ValueError(
                    f"table.column_titles содержит {len(self.table.column_titles)} подписей "
                    f"при table.columns={self.table.columns} — числа должны совпадать."
                )

        if self.type in ("SC", "MC"):
            if not self.options or len(self.options) < 2:
                raise ValueError(
                    "Для задач типов SC/MC необходимо указать минимум два варианта ответа в поле 'options'."
                )

        if self.type in QUIZ_TASK_TYPES:
            if not self.options or len(self.options) < 2:
                raise ValueError(
                    "Для квиз-задач (SC_Qw/MC_Qw) необходимо указать минимум два варианта ответа в поле 'options'."
                )
            if not self.scales:
                raise ValueError(
                    "Для квиз-задач (SC_Qw/MC_Qw) необходимо явно объявить шкалы в поле 'scales'."
                )
            declared = set(self.scales)
            for opt in self.options:
                if not opt.scores:
                    continue
                unknown = set(opt.scores.keys()) - declared
                if unknown:
                    raise ValueError(
                        f"Вариант '{opt.id}' ссылается на необъявленные шкалы: "
                        f"{', '.join(sorted(unknown))}. Объявленные шкалы: "
                        f"{', '.join(sorted(declared))}."
                    )

        # Валидация уникальности options[].id
        if self.options:
            option_ids = [opt.id for opt in self.options]
            if len(option_ids) != len(set(option_ids)):
                duplicates = [opt_id for opt_id in option_ids if option_ids.count(opt_id) > 1]
                raise ValueError(
                    f"ID вариантов ответа должны быть уникальными. "
                    f"Найдены дубликаты: {', '.join(set(duplicates))}"
                )

        return self
