from __future__ import annotations

from typing import List, Optional, Literal, Dict, Any

from pydantic import BaseModel, Field, model_validator


ScoringMode = Literal["all_or_nothing", "partial", "custom"]

# Словарь шагов нормализации короткого ответа. Закрытый список намеренно:
# неизвестный шаг молча игнорировался бы движком, и опечатка ('code-ast' вместо
# 'code_ast') бесшумно выключила бы режим сравнения кода — задание вернулось бы
# к ложным незачётам, и никто бы не заметил. Лучше 422 на импорте (tsk-262).
NormalizationStep = Literal[
    "trim",
    "lower",
    "strip_punctuation",
    "collapse_spaces",
    "code_ast",
]


class PartialRule(BaseModel):
    """
    Правило частичного оценивания для задач с множественным выбором (MC)
    или сложных схем проверки.
    """

    selected: List[str] = Field(
        ...,
        description="Набор ID вариантов ответа, для которых применяется данное правило.",
    )
    score: int = Field(
        ...,
        description="Баллы, которые начисляются при таком наборе выбранных вариантов.",
    )


class ShortAnswerAccepted(BaseModel):
    """
    Допустимый вариант короткого ответа (SA/SA_COM).
    """

    value: str = Field(
        ...,
        description="Строковое представление корректного ответа (например '4' или 'четыре').",
        examples=["8", "28", "len", "len()", "двадцать восемь"],
    )
    score: int = Field(
        ...,
        description="Баллы за этот вариант ответа (может быть меньше максимума для частичных совпадений).",
        examples=[5, 10, 15],
        ge=0,
    )


class ShortAnswerRules(BaseModel):
    """
    Правила проверки короткого ответа (SA/SA_COM).

    tsk-366: тот же блок обслуживает табличный ответ TBL_COM — эталон лежит в
    `accepted_answers` строкой (ячейки через пробел, ряды через перевод строки),
    а шаги нормализации применяются к КАЖДОЙ ячейке отдельно.
    """

    normalization: List[NormalizationStep] = Field(
        default_factory=lambda: ["trim", "lower"],
        description=(
            "Список шагов нормализации строки перед сравнением. "
            "Текстовые шаги: trim, lower, strip_punctuation, collapse_spaces — "
            "применяются в фиксированном порядке: "
            "trim → lower → strip_punctuation → collapse_spaces. "
            "Шаг strip_punctuation ставит на место знака ПРОБЕЛ (tsk-694), поэтому "
            "'a=b(c,d)' и 'a = b(c, d)' сравниваются одинаково. Исключения, где знак "
            "не разделитель, а часть значения: внутри чисто числового куска "
            "('2.5', '25/12/2024', '192.168.1.0') и внутри слова ('кто-то', \"don't\") — "
            "там знак по-прежнему просто удаляется, иначе '2 5' засчиталось бы за '2.5'. "
            "Отдельный шаг 'code_ast' объявляет, что ответ на это задание — "
            "программа на Python: ответ и эталон сначала сравниваются как код "
            "(канон через разбор в AST), поэтому кавычки, пробелы вокруг "
            "синтаксиса и комментарии несущественны, а регистр имён существен. "
            "Если ответ или эталон не разбирается как Python (фрагмент, другой "
            "язык), сравнение падает обратно на текстовые шаги из этого же списка. "
            "Флаг ставится заданию явно и не выводится из содержимого эталона."
        ),
        examples=[
            ["trim", "lower"],
            ["trim", "lower", "collapse_spaces"],
            ["trim", "lower", "strip_punctuation", "collapse_spaces"],
            ["trim", "code_ast", "strip_punctuation", "collapse_spaces"],
        ],
    )
    accepted_answers: List[ShortAnswerAccepted] = Field(
        default_factory=list,
        description="Список допустимых ответов и баллов за них.",
        examples=[
            [{"value": "8", "score": 10}, {"value": "28", "score": 10}],
            [{"value": "len", "score": 5}, {"value": "len()", "score": 5}],
            [],
        ],
    )
    use_regex: bool = Field(
        default=False,
        description="Если true, допускается проверка по регулярному выражению.",
        examples=[False, True],
    )
    regex: Optional[str] = Field(
        default=None,
        description="Регулярное выражение для проверки ответа (если use_regex = true).",
        examples=[None, r"^\d+$", r"^[A-Z][a-z]+$"],
    )


class TableAnswerRules(BaseModel):
    """
    Настройки сравнения табличного ответа (TBL_COM, tsk-366).

    Сам эталон и шаги нормализации живут в общем блоке `short_answer` — том же,
    что у SA/SA_COM. Это осознанно: 210 заданий уже хранят табличный ответ строкой
    в `accepted_answers` и работают, поэтому перевод в TBL_COM обязан быть сменой
    ТИПА, а не переписыванием правил. Здесь — только то, чего у короткого ответа нет.

    Режим начисления баллов отдельным полем не заводится: для этого уже есть
    `SolutionRules.scoring_mode` (`all_or_nothing` по умолчанию — как на реальном
    ЕГЭ, где №25 оценивается в 1 балл целиком; `partial` даёт балл пропорционально
    числу верных рядов).
    """

    row_order_matters: bool = Field(
        default=True,
        description=(
            "Важен ли порядок рядов. True (по умолчанию) — ответ сверяется как "
            "последовательность: в №25 ряды упорядочены условием, в №26 порядок "
            "задан смыслом. False — ряды сверяются как мультимножество (порядок "
            "любой, но количество повторов значимо); ячейки ВНУТРИ ряда при этом "
            "всё равно упорядочены, потому что столбцы разные по смыслу."
        ),
        examples=[True, False],
    )


class TextRubricItem(BaseModel):
    """
    Критерий оценивания развёрнутого ответа (TA).
    """

    id: str = Field(
        ...,
        description="Устойчивый ID критерия (например 'content', 'style').",
        examples=["content", "style", "grammar", "logic"],
    )
    title: str = Field(
        ...,
        description="Человекочитаемое название критерия.",
        examples=["Содержание", "Стиль изложения", "Грамматика", "Логика рассуждений"],
    )
    max_score: int = Field(
        ...,
        description="Максимальный балл по данному критерию.",
        examples=[5, 10, 15],
        gt=0,
    )


class TextAnswerRules(BaseModel):
    """
    Настройки проверки развёрнутых ответов (TA).
    """

    auto_check: bool = Field(
        default=False,
        description="Флаг возможности автопроверки. Обычно false, оценка ручная.",
        examples=[False, True],
    )
    rubric: List[TextRubricItem] = Field(
        default_factory=list,
        description="Набор критериев оценивания для ручной или комбинированной проверки.",
        examples=[
            [
                {"id": "content", "title": "Содержание", "max_score": 10},
                {"id": "style", "title": "Стиль изложения", "max_score": 5},
            ],
            [],
        ],
    )


#: Минимальная длина пункта критерия. Порог не выдуман: у 472 существующих
#: пунктов рубрики TA на проде самый короткий — 22 символа, средний 60
#: (замер 2026-08-13). Всё, что короче десяти, — не критерий, а отметка
#: («ок», «верно», «см. урок»), и она молча делала бы задание пригодным для
#: машинной проверки, ничего проверяющему не сообщая.
CRITERION_MIN_LENGTH = 10
CRITERION_MAX_LENGTH = 500
CRITERIA_MAX_ITEMS = 20
CRITERIA_NOTES_MAX_LENGTH = 2000

#: Состояние критериев (tsk-590). `draft` — заготовка, которую человек ещё не
#: читал: она видна методисту в редакторе и в очереди вычитки, но проверяющей
#: машине НЕ отдаётся. `approved` — критерии вычитаны человеком и работают как
#: замена эталона.
#:
#: Значение по умолчанию — `draft`, и это осознанный выбор в сторону
#: безопасности: цена ошибки несимметрична. Лишний черновик в очереди стоит
#: методисту одного прочтения, а невычитанный критерий, попавший в машинную
#: проверку, стоит ученику незачёта по правилу, которого никто не видел.
CriteriaStatus = Literal["draft", "approved"]

#: Откуда критерии взялись. Нужно не для статистики, а для вычитки: методист
#: должен видеть, читает он свой текст или предложение модели, — доверие к
#: этим двум источникам разное.
CriteriaOrigin = Literal["manual", "ai_draft", "import"]


def _clean_criteria_list(values: List[str], *, field_name: str) -> List[str]:
    """Обрезать, проверить осмысленность и отсечь дубли в списке критериев.

    :param values: сырые пункты, как их прислал методист.
    :param field_name: имя поля — попадает в текст ошибки, чтобы методист
        видел, ГДЕ он ошибся, а не абстрактное «неверные критерии».
    :returns: очищенный список в исходном порядке.
    :raises ValueError: пустой/слишком короткий/слишком длинный пункт, дубль
        либо превышение числа пунктов.
    """
    if len(values) > CRITERIA_MAX_ITEMS:
        raise ValueError(
            f"{field_name}: не больше {CRITERIA_MAX_ITEMS} пунктов "
            f"(прислано {len(values)}). Длинный список не читает ни человек, "
            f"ни модель — он не помещается в осмысленный промпт."
        )
    cleaned: List[str] = []
    seen: set[str] = set()
    for raw in values:
        item = " ".join(raw.split())
        if len(item) < CRITERION_MIN_LENGTH:
            raise ValueError(
                f"{field_name}: пункт {item!r} короче {CRITERION_MIN_LENGTH} "
                f"символов. Критерий должен говорить, ЧТО именно проверяется "
                f"(«Приведены два примера с указанием органа чувств»), — "
                f"пометка «ок» проверяющему ничего не сообщает."
            )
        if len(item) > CRITERION_MAX_LENGTH:
            raise ValueError(
                f"{field_name}: пункт длиннее {CRITERION_MAX_LENGTH} символов. "
                f"Разбейте на несколько отдельных требований."
            )
        key = item.casefold()
        if key in seen:
            raise ValueError(f"{field_name}: пункт {item!r} повторяется.")
        seen.add(key)
        cleaned.append(item)
    return cleaned


class GradingCriteria(BaseModel):
    """
    Критерии оценивания задания, у которого нет формализуемого эталона (tsk-605).

    **Зачем поле существует.** Калибровка на 180 живых сдачах (tsk-590, отчёт
    `reviews/2026-08-08-tsk590-kalibrovka.md`) показала: разделяющий признак
    годности машинной проверки — не тип задания, а наличие эталона. С эталоном
    собственные ошибки лучшей модели 1.2 %, без эталона — 7.6–19.0 %. Причина
    системная: без эталона модель не пересчитывает задачу, а подтверждает
    предъявленное учеником число (зачла `8641` при верном `8641.5`). Там, где
    ответ нельзя записать в `short_answer.accepted_answers`, критерии — это
    единственная замена эталону, и без них задание в автономный (без
    преподавателя) трек пускать нельзя.

    **Форма.** Три списка вместо одного свободного текста: у проверяющего —
    человека или модели — разные вопросы к ответу, и слепив их в абзац, мы
    получили бы ровно то поведение, которое чиним, — «в целом похоже, зачёт».
    `reject` заведён отдельно именно против измеренного класса ошибок: он
    называет то, что выглядит правдоподобно, но ответом не является.

    Развёрнутые ответы (TA) хранят критерии в `text_answer.rubric` — 148
    заданий прода уже заполнены осмысленно. Они НЕ переносятся: предикат
    `SolutionRules.has_grading_criteria()` читает оба источника, поэтому
    миграция данных не нужна, а редактор рубрики TA продолжает работать.
    """

    must: List[str] = Field(
        ...,
        description=(
            "Что обязательно должно быть в ответе, чтобы он считался верным. "
            "Каждый пункт — проверяемое требование, а не тема."
        ),
        examples=[
            [
                "Программа читает количество чисел, а затем сами числа",
                "Максимум ищется среди чисел, кратных 5, а не среди всех",
                "Выводится одно число — сам максимум, без пояснений",
            ]
        ],
    )
    accept: List[str] = Field(
        default_factory=list,
        description=(
            "Что считать эквивалентным и засчитывать: другие формулировки, "
            "иная запись числа, другой верный способ решения."
        ),
        examples=[["Любые имена переменных", "Дробная часть через запятую или точку"]],
    )
    reject: List[str] = Field(
        default_factory=list,
        description=(
            "Что НЕ засчитывать, даже если ответ выглядит близким. Пишется "
            "против измеренного класса ошибок: модель склонна объявить чужую "
            "ошибку опечаткой и подтвердить предъявленное число."
        ),
        examples=[["Округлённый результат вместо точного", "Ответ без пересчёта — «примерно столько же»"]],
    )
    notes: Optional[str] = Field(
        default=None,
        description="Свободное пояснение проверяющему: контекст задания, частые ловушки.",
        examples=[None, "Ученик сдаёт вывод программы; поле ответа схлопывает переносы строк."],
    )

    # ── Вычитка человеком (tsk-590) ─────────────────────────────────────────
    status: CriteriaStatus = Field(
        default="draft",
        description=(
            "Вычитаны ли критерии человеком. `draft` — заготовка, машине не "
            "отдаётся; `approved` — методист прочитал и подтвердил. По "
            "умолчанию `draft`: критерии, про которые неизвестно, читал ли их "
            "человек, к оценке ученика не допускаются."
        ),
    )
    origin: CriteriaOrigin = Field(
        default="manual",
        description=(
            "Кто составил критерии: `manual` — методист руками, `ai_draft` — "
            "черновик модели, `import` — пакетная загрузка."
        ),
    )
    generated_by_model: Optional[str] = Field(
        default=None,
        description=(
            "Модель, составившая черновик (`origin=ai_draft`). Нужна, чтобы "
            "при следующей калибровке было видно, чьи заготовки методист "
            "правил чаще."
        ),
        examples=[None, "openai/gpt-5.4-mini"],
    )
    generated_at: Optional[str] = Field(
        default=None,
        description="Когда составлен черновик, ISO-8601. Строка, а не datetime: "
        "`solution_rules` уходит в jsonb через `model_dump()` без режима json.",
    )
    reviewed_by: Optional[int] = Field(
        default=None,
        description="`users.id` того, кто подтвердил критерии (`status=approved`).",
    )
    reviewed_at: Optional[str] = Field(
        default=None,
        description="Когда критерии подтверждены, ISO-8601 (см. `generated_at` про тип).",
    )
    draft_warning: Optional[str] = Field(
        default=None,
        description=(
            "Предупреждение о классе задания, проставленное КОДОМ, а не "
            "моделью (tsk-590): например, ответ подтверждается файлом, "
            "которого модель не видит. Модель на такие вопросы отвечать не "
            "умеет — замер показал, что вычисляющей себя она не признаёт."
        ),
        examples=[None, "Ответ подтверждается файлом — критерии машинную проверку не заменят."],
    )

    @model_validator(mode="after")
    def validate_criteria(self) -> "GradingCriteria":
        """Пустой или бессодержательный блок не должен считаться критериями.

        Тот же принцип, что у `partial_auto_check` (tsk-396): предпосылки
        проверяются на входе, а не выключаются молча. Блок с пустым `must`
        прошёл бы гейт допуска и открыл заданию автономный трек, не сказав
        проверяющему ничего, — то есть ровно та дыра, ради которой поле и
        заведено.
        """
        self.must = _clean_criteria_list(self.must, field_name="grading_criteria.must")
        if not self.must:
            raise ValueError(
                "grading_criteria.must не может быть пустым: критерии без "
                "обязательных требований не отличают верный ответ от неверного"
            )
        self.accept = _clean_criteria_list(self.accept, field_name="grading_criteria.accept")
        self.reject = _clean_criteria_list(self.reject, field_name="grading_criteria.reject")
        if self.notes is not None:
            notes = self.notes.strip()
            if len(notes) > CRITERIA_NOTES_MAX_LENGTH:
                raise ValueError(
                    f"grading_criteria.notes длиннее {CRITERIA_NOTES_MAX_LENGTH} символов"
                )
            self.notes = notes or None
        if self.status == "approved" and self.reviewed_by is None:
            raise ValueError(
                "grading_criteria.status=approved требует reviewed_by: "
                "подтверждение без имени подтвердившего не отличить от "
                "заготовки, а именно на этом отличии стоит допуск к машинной "
                "проверке"
            )
        return self

    def is_usable(self) -> bool:
        """Можно ли судить ответ по этим критериям — то есть вычитаны ли они.

        Отдельный метод, а не условие на месте: «критерии есть» спрашивают
        предикат допуска, промпт судьи и экран методиста, и разъехаться им
        нельзя (тот же довод, что в `ai_check_policy`).

        :returns: True — критерии непустые И подтверждены человеком.
        """
        return bool(self.must) and self.status == "approved"


class PenaltiesRules(BaseModel):
    """
    Правила штрафов за различные типы ошибок.
    """

    wrong_answer: int = Field(
        default=0,
        description="Штраф за заведомо неверный ответ. Вычитается из базового балла (0 для неправильного ответа).",
        examples=[0, 1, 2, 5],
        ge=0,
    )
    missing_answer: int = Field(
        default=0,
        description="Штраф за отсутствие ответа. Вычитается из базового балла (0 для отсутствия ответа).",
        examples=[0, 1, 3, 5],
        ge=0,
    )
    extra_wrong_mc: int = Field(
        default=0,
        description="Штраф за каждый лишний неверный вариант при множественном выборе (MC). Вычитается из частичного балла.",
        examples=[0, 1, 2, 4],
        ge=0,
    )


QuizMode = Literal["single", "multiple"]


class QuizRules(BaseModel):
    """
    Правила квиз-вопросов со шкалами (SC_Qw/MC_Qw, tsk-122, ADR-0003).

    Без `correct_options`: вместо проверки «верно/неверно» движок считает вклад
    по шкалам из выбранных вариантов и пишет `task_result.scale_scores`.
    """

    scales: List[str] = Field(
        ...,
        description=(
            "Объявление шкал квиза (дублирует task_content.scales для самодостаточности "
            "правил проверки). Ключи scores вариантов валидируются против этого списка."
        ),
        examples=[["информатика", "python"]],
    )
    mode: QuizMode = Field(
        default="single",
        description="Режим выбора: single (SC_Qw, ровно один вариант) | multiple (MC_Qw).",
        examples=["single", "multiple"],
    )


class TurtleSegment(BaseModel):
    """
    Один примитив трассы черепахи (tsk-412): отрезок линии или дуга circle().

    Записывается стабом `app/services/turtle_sandbox/stub_turtle.py` только при
    опущенном пере (pen down) — движение с поднятым пером на трассу не влияет,
    но всё равно расходует `max_steps` (см. TurtleSimRules).
    """

    kind: Literal["line", "circle"] = Field(
        ...,
        description="line — прямой отрезок (forward/backward/goto/setpos), circle — дуга circle().",
    )
    start: List[float] = Field(..., description="Координаты начала [x, y].", min_length=2, max_length=2)
    end: List[float] = Field(..., description="Координаты конца [x, y].", min_length=2, max_length=2)
    color_rgb: List[float] = Field(
        ...,
        description="Цвет пера в момент отрисовки, нормализованный к RGB [0..1].",
        min_length=3,
        max_length=3,
    )
    radius: Optional[float] = Field(
        default=None, description="Радиус дуги (только kind='circle')."
    )
    extent: Optional[float] = Field(
        default=None, description="Угол дуги в градусах (только kind='circle')."
    )


class TurtleFinalState(BaseModel):
    """Конечное состояние черепахи(-ах) после исполнения программы (tsk-412)."""

    position: List[float] = Field(..., description="Финальные координаты [x, y] ПЕРВОЙ созданной черепахи.", min_length=2, max_length=2)
    heading: float = Field(..., description="Финальный угол поворота в градусах [0, 360).")
    pen_down: bool = Field(..., description="Опущено ли перо в конце.")


class TurtleTrace(BaseModel):
    """Полная трасса исполнения программы в песочнице turtle (tsk-412)."""

    segments: List[TurtleSegment] = Field(default_factory=list)
    final_state: TurtleFinalState


class TurtleSimRules(BaseModel):
    """
    Правила проверки задания «нарисуй фигуру черепахой» (tsk-412, курс 165).

    В отличие от `short_answer.normalization=code_ast` (сравнение ИСХОДНОГО КОДА
    как программы), здесь сравнивается РЕЗУЛЬТАТ исполнения: ответ ученика —
    полная Python-программа в `response.value` (тип задачи `SA`, без comment —
    доказательством служит сам факт совпадения рисунка). Код исполняется в
    песочнице (`app/services/turtle_sandbox/`), полученная трасса сравнивается
    с `expected_trace`, вычисленной ОДИН РАЗ офлайн прогоном эталонного решения
    через тот же стаб (не хранится сам эталонный код — только трасса, чтобы не
    исполнять «доверенный» код на каждой проверке и не хранить решение в
    открытом виде рядом с заданием).
    """

    expected_trace: TurtleTrace = Field(
        ..., description="Эталонная трасса, вычисленная офлайн прогоном решения через стаб."
    )
    random_seed: Optional[int] = Field(
        default=None,
        description="Если задание использует random — сид, который ставится ПЕРЕД исполнением "
        "кода ученика (и ставился перед вычислением эталонной трассы). Без сида "
        "ответы со случайностью невоспроизводимы и не могут сравниваться.",
    )
    synthetic_clicks: List[List[float]] = Field(
        default_factory=list,
        description="Синтетические клики [[x,y], ...], поданные по очереди в обработчик "
        "onscreenclick при вызове done()/mainloop() — для заданий с интерактивом мыши.",
    )
    tolerance_px: float = Field(
        default=0.75,
        gt=0,
        description="Допустимое отклонение координат/радиуса в пикселях (накопление float).",
    )
    max_steps: int = Field(
        default=5000,
        gt=0,
        description="Предел числа примитивов движения (forward/circle/goto/...) — защита "
        "от программ с бесконечным рисованием (`while True` без верного условия выхода).",
    )
    timeout_sec: float = Field(
        default=5.0,
        gt=0,
        le=15.0,
        description="Жёсткий лимит времени исполнения в песочнице (wall-clock).",
    )


class SolutionRules(BaseModel):
    """
    Структура JSON-поля tasks.solution_rules.

    Описывает, как задача проверяется и как начисляются баллы.
    """

    max_score: int = Field(
        ...,
        description="Полный балл за задачу (должен совпадать с tasks.max_score).",
        gt=0,
        examples=[5, 10, 15, 20],
    )
    scoring_mode: ScoringMode = Field(
        default="all_or_nothing",
        description="Режим оценивания: all_or_nothing | partial | custom.",
        examples=["all_or_nothing", "partial", "custom"],
    )
    auto_check: bool = Field(
        default=True,
        description="Можно ли выполнить полную проверку автоматически.",
        examples=[True, False],
    )
    manual_review_required: bool = Field(
        default=False,
        description="Требуется ли обязательная ручная дооценка (даже при автопроверке).",
        examples=[False, True],
    )
    partial_auto_check: bool = Field(
        default=False,
        description=(
            "Гибридный режим проверки (tsk-396): часть ответа сверяется автоматически, "
            "финальный зачёт остаётся за преподавателем. Пример — ОГЭ-14: два числовых "
            "ответа формализуемы (эталон в short_answer), построение диаграммы проверяет "
            "только человек. При true авто-сверка ВЫПОЛНЯЕТСЯ и её итог сразу виден "
            "ученику в feedback, но `score` не начисляется (score=0 до оценки "
            "преподавателем) — иначе PASS-гейт движка (score/max_score >= 0.5) зачёл бы "
            "задание и курс до ручной проверки. Числа сошлись → is_correct=None, работа в "
            "обязательной очереди; не сошлись → is_correct=False, в очередь НЕ попадает "
            "(ученик пересдаёт сам, решение оператора). Требует manual_review_required=true "
            "и наличия эталона. Default false — задания без флага ведут себя как раньше."
        ),
        examples=[False, True],
    )
    requires_attachment: bool = Field(
        default=False,
        description=(
            "Требуется ли обязательное вложение (файл-подтверждение) для зачёта (tsk-227). "
            "Если true — ответ по задаче НЕ засчитывается без загруженного файла в попытке, "
            "даже при верном авто-ответе (в т.ч. перекрывает оптимистичный авто-пасс SA_COM). "
            "Сервер — источник истины; клиент показывает обязательную загрузку. Default false — "
            "существующие задания без флага ведут себя как раньше."
        ),
        examples=[False, True],
    )

    # Для задач с выбором (SC/MC)
    correct_options: List[str] = Field(
        default_factory=list,
        description="Список ID правильных вариантов ответа для задач с выбором. Для SC должен быть ровно один элемент.",
        examples=[["A"], ["A", "B"], ["opt1", "opt2", "opt3"], []],
    )
    partial_rules: List[PartialRule] = Field(
        default_factory=list,
        description="Правила частичного оценивания для сложных случаев (обычно MC).",
    )

    # Для короткого ответа
    short_answer: Optional[ShortAnswerRules] = Field(
        default=None,
        description="Правила проверки короткого ответа (SA/SA_COM).",
    )

    # Для «нарисуй фигуру черепахой» (tsk-412): исполнение кода ученика в
    # песочнице и сравнение трассы, а не сравнение самого кода как текста.
    turtle_sim: Optional[TurtleSimRules] = Field(
        default=None,
        description=(
            "Правила проверки через безопасное исполнение кода ученика (tsk-412, "
            "курс 165). Тип задачи остаётся SA (эталон — response.value = полная "
            "программа), но при заполненном turtle_sim CheckingService НЕ делает "
            "code_ast/текстовое сравнение — вместо этого исполняет код в песочнице "
            "и сравнивает получившуюся трассу с эталонной."
        ),
    )

    # Для табличного ответа (TBL_COM); эталон берётся из short_answer
    table: Optional[TableAnswerRules] = Field(
        default=None,
        description=(
            "Настройки сравнения табличного ответа (TBL_COM, tsk-366). Отсутствие "
            "блока = поведение по умолчанию (порядок рядов важен)."
        ),
    )

    # Для развёрнутого ответа
    text_answer: Optional[TextAnswerRules] = Field(
        default=None,
        description="Настройки проверки развёрнутых ответов (TA).",
    )

    # Критерии оценивания там, где эталона нет и быть не может (tsk-605)
    grading_criteria: Optional[GradingCriteria] = Field(
        default=None,
        description=(
            "Критерии оценивания для заданий без формализуемого эталона "
            "(tsk-605). Замена `short_answer.accepted_answers` там, где ответ "
            "нельзя перечислить списком: что обязательно должно быть в ответе, "
            "что считать эквивалентным, что — ошибкой. Вместе с эталоном "
            "образует условие допуска задания к машинной проверке "
            "(`has_grading_criteria`, `app/services/ai_check_policy.py`). "
            "Для TA ту же роль играет заполненная `text_answer.rubric` — "
            "предикат читает оба источника, переносить данные не требуется."
        ),
    )

    # Для квиз-вопросов со шкалами (SC_Qw/MC_Qw)
    quiz: Optional[QuizRules] = Field(
        default=None,
        description="Правила квиз-вопросов со шкалами (SC_Qw/MC_Qw, tsk-122).",
    )

    penalties: PenaltiesRules = Field(
        default_factory=PenaltiesRules,
        description="Настройки штрафов за неверные/отсутствующие ответы.",
    )

    # Для режима custom
    custom_scoring_config: Optional[Any] = Field(
        default=None,
        description=(
            "Конфигурация для режима custom scoring_mode. "
            "Позволяет задать расширенные правила оценивания. "
            "Формат зависит от типа задачи и требований. "
            "Примеры: "
            "{'formula': 'score = correct_count * 2', 'min_score': 0, 'max_score': 20} "
            "или {'rules': [{'condition': 'all_correct', 'score': 10}, {'condition': 'partial', 'score': 5}]} "
            "или {'coefficient': 1.5, 'min_score': 0}"
        ),
        examples=[
            {
                "coefficient": 1.5,
                "min_score": 0,
            },
            {
                "formula": "score = correct_count * 2",
                "min_score": 0,
                "max_score": 20,
            },
            {
                "rules": [
                    {"condition": "all_correct", "score": 10},
                    {"condition": "partial", "score": 5},
                ],
            },
            None,
        ],
    )

    @model_validator(mode="after")
    def validate_max_score(self) -> "SolutionRules":
        """
        Валидация max_score: должен быть положительным числом.
        """
        if self.max_score <= 0:
            raise ValueError("max_score должен быть положительным числом")
        return self

    @model_validator(mode="after")
    def validate_partial_auto_check(self) -> "SolutionRules":
        """Гибридный режим (tsk-396) осмыслен только вместе с двумя предпосылками.

        Без `manual_review_required` флаг обнулял бы сам себя: держать зачёт до
        человека — вся его суть. Без эталона нечего сверять автоматически, и
        ученик получил бы обещание мгновенной обратной связи, которой нет.
        Обе ошибки молча выключили бы режим на импорте — как опечатка в шаге
        нормализации до tsk-262. Лучше 422 при заведении задания.
        """
        if not self.partial_auto_check:
            return self
        if not self.manual_review_required:
            raise ValueError(
                "partial_auto_check=true требует manual_review_required=true: "
                "гибридный режим держит зачёт до проверки преподавателем"
            )
        if not self.has_reference_answer():
            raise ValueError(
                "partial_auto_check=true требует эталон для авто-сверки "
                "(short_answer.accepted_answers либо regex)"
            )
        return self

    def has_reference_answer(self) -> bool:
        """Есть ли эталон для авто-сверки `response.value` (SA/SA_COM/TBL_COM).

        «Эталона нет» — это НЕ только `short_answer is None`: блок правил может
        существовать, но быть пустым (ни одного `accepted_answers`, regex не
        задан или выключен). Пустое правило вообще живёт в трёх формах (SQL NULL,
        JSON-null, объект-но-пустой), и наивная проверка ловит только первую —
        см. плейбук ЕГЭ §6.1 (дважды давал ложное «всё чисто»).

        Единая точка этого предиката: используется и в `_check_table_answer`
        (там он и появился), и при выдаче UX-сигнала клиенту
        (`TaskStateResponse.has_reference_answer`, tsk-547) — чтобы «сверять
        нечем» на проверке и «поле ответа бессмысленно» в форме не разъехались.

        Для типов без короткого ответа (SC/MC/TA/квизы) `short_answer` не
        заполняется, и метод вернёт False — вызывающий обязан учитывать тип
        задания сам (см. `learning.py`).

        :returns: True — эталон задан (списком принимаемых ответов либо regex).
        """
        if self.turtle_sim is not None:
            return True
        rules = self.short_answer
        if rules is None:
            return False
        if rules.accepted_answers:
            return True
        return bool(rules.use_regex and rules.regex)

    def has_grading_criteria(self) -> bool:
        """Заданы ли критерии оценивания — замена эталона там, где его нет (tsk-605).

        Читает ДВА источника, и это не дублирование, а отказ от миграции:
        `grading_criteria` — новое поле для любого типа задания, а
        `text_answer.rubric` — то же самое по смыслу у развёрнутых ответов,
        где 148 заданий прода уже заполнены осмысленно (замер 2026-08-13:
        472 пункта, средняя длина 60 символов). Перенос данных ради единого
        поля обесценил бы готовую работу методиста и сломал редактор рубрики.

        Проверяется НЕПУСТОТА, а не наличие блока: пустой объект — та же
        форма «правило есть, а содержимого нет», из-за которой предикат
        `has_reference_answer` пришлось делать общим (см. его docstring).

        **Черновик критериями не считается (tsk-590).** Заготовка модели
        живёт в том же поле, и если бы предикат смотрел только на непустоту
        `must`, задание становилось бы пригодным для машинной проверки в тот
        момент, когда черновик записан, — то есть до того, как его прочитал
        человек. Замер 12 живых заданий показал, за что именно эта защита:
        для заданий «напишите программу» модель уверенно пишет критерии
        проверки ПРОГРАММЫ, тогда как ученик сдаёт короткий ответ, — текст
        складный, а проверять по нему нельзя.

        Рубрика TA (`text_answer.rubric`) статуса не имеет и считается
        подтверждённой: её 148 заданий писал методист руками, машинного
        происхождения там нет.

        :returns: True — проверяющему есть по чему судить ответ без эталона.
        """
        if self.grading_criteria is not None and self.grading_criteria.is_usable():
            return True
        return bool(self.text_answer is not None and self.text_answer.rubric)

    def criteria_state(self) -> Literal["none", "draft", "approved"]:
        """Что с критериями у задания — для инвентаря и очереди вычитки (tsk-590).

        Три состояния вместо булева «есть/нет»: методисту нужно различать
        «писать с нуля» и «прочитать готовую заготовку», это разная работа и
        разный объём. Предикат один на всех потребителей — по той же причине,
        что и `has_grading_criteria`.

        :returns: `none` — ни критериев, ни черновика; `draft` — заготовка
            ждёт вычитки; `approved` — критерии подтверждены человеком.
        """
        if self.text_answer is not None and self.text_answer.rubric:
            return "approved"
        criteria = self.grading_criteria
        if criteria is None or not criteria.must:
            return "none"
        return "approved" if criteria.status == "approved" else "draft"

    def criteria_for_judge(self) -> Optional[dict]:
        """Критерии в едином виде — независимо от того, где они лежат (tsk-605).

        Единственная точка сборки: и промпт судьи, и экран методиста, и
        инвентарь пробелов обязаны понимать «критерии есть» одинаково. Если
        каждый потребитель разберёт `grading_criteria` и `text_answer.rubric`
        сам, они разъедутся — ровно так уже расходились «сверять нечем» на
        проверке и «поле ответа бессмысленно» в форме до tsk-547.

        :returns: `{"must": [...], "accept": [...], "reject": [...],
            "notes": str | None, "source": "grading_criteria" | "text_rubric"}`
            либо None, если критериев нет.
        """
        if self.grading_criteria is not None and self.grading_criteria.is_usable():
            return {
                "must": list(self.grading_criteria.must),
                "accept": list(self.grading_criteria.accept),
                "reject": list(self.grading_criteria.reject),
                "notes": self.grading_criteria.notes,
                "source": "grading_criteria",
            }
        if self.text_answer is not None and self.text_answer.rubric:
            return {
                "must": [item.title for item in self.text_answer.rubric],
                "accept": [],
                "reject": [],
                "notes": None,
                "source": "text_rubric",
            }
        return None

    def validate_with_task_content(self, task_content: "TaskContent") -> None:
        """
        Валидирует соответствие correct_options и options[].id из task_content.
        
        Вызывается из сервиса при создании/обновлении задачи.
        
        Args:
            task_content: Схема содержимого задачи (TaskContent).
            
        Raises:
            ValueError: Если correct_options не соответствуют options[].id.
        """
        from app.schemas.task_content import TaskContent, QUIZ_TASK_TYPES

        # Для квиз-задач (SC_Qw/MC_Qw) — без correct_options; сверяем объявление шкал.
        if task_content.type in QUIZ_TASK_TYPES:
            if not task_content.options:
                raise ValueError(
                    f"Для квиз-задач типа {task_content.type} необходимо указать варианты "
                    f"ответа в task_content.options"
                )
            if self.quiz is None:
                raise ValueError(
                    f"Для квиз-задач типа {task_content.type} необходима секция 'quiz' "
                    f"в solution_rules (scales, mode)."
                )
            content_scales = set(task_content.scales or [])
            rule_scales = set(self.quiz.scales or [])
            if not rule_scales:
                raise ValueError("quiz.scales не может быть пустым для квиз-задач.")
            if content_scales != rule_scales:
                raise ValueError(
                    f"Шкалы в solution_rules.quiz.scales ({', '.join(sorted(rule_scales))}) "
                    f"не совпадают с task_content.scales ({', '.join(sorted(content_scales))})."
                )
            expected_mode = "single" if task_content.type == "SC_Qw" else "multiple"
            if self.quiz.mode != expected_mode:
                raise ValueError(
                    f"Для типа {task_content.type} ожидается quiz.mode='{expected_mode}', "
                    f"получено '{self.quiz.mode}'."
                )
            return

        # Для задач с выбором (SC/MC) проверяем соответствие
        if task_content.type in ("SC", "MC"):
            if not task_content.options:
                raise ValueError(
                    f"Для задач типа {task_content.type} необходимо указать варианты ответа в task_content.options"
                )
            
            # Получаем все доступные ID вариантов
            available_option_ids = {opt.id for opt in task_content.options}
            
            # Проверяем, что все correct_options существуют в options
            invalid_options = set(self.correct_options) - available_option_ids
            if invalid_options:
                raise ValueError(
                    f"correct_options содержат несуществующие ID вариантов: {', '.join(sorted(invalid_options))}. "
                    f"Доступные ID: {', '.join(sorted(available_option_ids))}"
                )
            
            # Для SC проверяем, что выбран ровно один правильный вариант
            if task_content.type == "SC" and len(self.correct_options) != 1:
                raise ValueError(
                    f"Для задач типа SC должен быть указан ровно один правильный вариант. "
                    f"Указано: {len(self.correct_options)}"
                )
            
            # Проверяем partial_rules
            for rule in self.partial_rules:
                invalid_in_rule = set(rule.selected) - available_option_ids
                if invalid_in_rule:
                    raise ValueError(
                        f"partial_rules содержат несуществующие ID вариантов: {', '.join(sorted(invalid_in_rule))}"
                    )
