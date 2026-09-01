"""Pydantic схемы для /me эндпоинтов (Phase Y-1 + Y-3 + Y-6.2 + tsk-427)."""
from datetime import date, datetime
from typing import Literal, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

# tsk-427: категория ученика. Значения — латинские snake_case-коды (та же
# конвенция, что sale_status/match_kind в pricing, tsk-505); тексты для UI
# переводятся на стороне клиента (SPW).
ProfileCategory = Literal[
    "school_student",
    "university_student",
    "college_student",
    "applicant",
    "adult",
]

# tsk-588: откуда взялся `users.timezone`. 'manual' — вписал человек (профиль
# ученика, карточка методиста), 'auto' — снят с браузера при входе. Автозахват
# перезаписывает только 'auto' и пустое значение.
TimezoneSource = Literal["auto", "manual"]


def normalize_city(v: str | None) -> str | None:
    """Обрезать пробелы; строка из одних пробелов = не заполнено (None)."""
    if v is None:
        return v
    stripped = v.strip()
    return stripped or None


#: tsk-753: технические имена пояса вместо города. У `Etc/GMT±N` в POSIX
#: ОБРАТНЫЙ знак (`Etc/GMT-3` — это UTC+3), поэтому при любой ручной сверке их
#: читают наоборот; `UTC`/`GMT`/`Factory` приезжают с машин без настроенного
#: пояса. В профиле человека такое значение означает не место, а недонастройку —
#: у одного ученика школы так и было записано `Etc/GMT-3`.
_TECHNICAL_TIMEZONE_PREFIXES = ("etc/", "gmt")
_TECHNICAL_TIMEZONE_NAMES = {"utc", "universal", "zulu", "factory", "localtime"}


def is_technical_timezone(v: str) -> bool:
    """Техническое имя пояса вместо города — `Etc/GMT-3`, `UTC`, `Factory` (tsk-753)."""
    normalized = v.strip().lower()
    return normalized in _TECHNICAL_TIMEZONE_NAMES or normalized.startswith(
        _TECHNICAL_TIMEZONE_PREFIXES
    )


def validate_timezone(v: str | None) -> str | None:
    """Проверить, что значение — валидный IANA-идентификатор часового пояса.

    tsk-753: технические имена (`Etc/GMT-3`, `UTC`) отклоняются, хотя `ZoneInfo`
    их и знает. Считать по ним время можно, а хранить как «пояс человека» —
    нет: знак в них обратный, и первый же, кто сверит значение глазами, прочтёт
    его наоборот.
    """
    if v is None:
        return v
    try:
        ZoneInfo(v)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Некорректный часовой пояс: «{v}». Ожидается IANA-идентификатор, "
            "например Europe/Moscow."
        ) from exc
    if is_technical_timezone(v):
        raise ValueError(
            f"Часовой пояс «{v}» — техническое имя, а не место. Ожидается "
            "идентификатор с городом, например Asia/Krasnoyarsk."
        )
    return v


class PaymentDueNotice(BaseModel):
    """Напоминание об оплате для плашки в кабинете (tsk-744).

    Сумма и дата отдаются вместе с признаком, а не собираются клиентом: иначе
    SPW пришлось бы вторым запросом идти за начислениями на каждом экране —
    и держать свою копию правила «что считать долгом».
    """

    #: Сколько осталось заплатить, копейки. Формат тот же, что у начислений.
    due_minor: int
    #: Крайний день оплаты ближайшего долга.
    due_date: date
    #: Месяцы, за которые не заплачено. Обычно один.
    periods: list[date]
    #: Срок уже прошёл — плашка говорит «месяц закончился», а не «заканчивается».
    is_overdue: bool


class MeResponse(BaseModel):
    id: int
    email: str | None
    tg_id: str | None
    is_service: bool
    # tsk-223: реальное ФИО из users.full_name. Может быть null (email/legacy
    # пользователи без заполненного ФИО) — обратная совместимость.
    full_name: str | None = None
    # tsk-427: доп. поля профиля — все опциональны, заполняются позже в
    # кабинете, не при регистрации.
    category: ProfileCategory | None = None
    school_grade: int | None = None
    city: str | None = None
    timezone: str | None = None
    # tsk-588: откуда взят пояс. Клиенту нужно, чтобы честно подписать
    # автоматическое значение в профиле и не спорить с ручным выбором.
    timezone_source: TimezoneSource | None = None
    # tsk-298 (Фаза 0): имена ролей пользователя из user_roles (M2M),
    # отсортированы по алфавиту. Аддитивно — по умолчанию пустой список для
    # обратной совместимости. SPW гейтит teacher-зону по наличию 'teacher'.
    roles: list[str] = Field(default_factory=list)
    # tsk-674: ученик из аудитории опроса, который ещё не оставил пожеланий по
    # осеннему расписанию. Флаг едет в каждом ответе профиля, потому что
    # напоминание должно быть видно на ЛЮБОМ экране кабинета, а не только там,
    # куда человек однажды зашёл сам (урок tsk-591/tsk-652: уведомление, которое
    # не открывают, равно отсутствию уведомления). Гасится сохранением пожеланий.
    schedule_preference_pending: bool = False
    # tsk-744: чем закончится месяц, если не заплатить. Едет тем же способом и
    # по той же причине, что флаг выше: напоминание об оплате должно быть видно
    # на ЛЮБОМ экране кабинета, а не только на странице оплаты, куда должник как
    # раз не заходит. `None` — показывать нечего: либо человек ничего не должен,
    # либо до конца месяца ещё далеко.
    payment_due: PaymentDueNotice | None = None

    model_config = {"from_attributes": True}


class MeUpdateRequest(BaseModel):
    """Тело PATCH /me — self-service обновление профиля.

    Partial update: каждое поле независимо и необязательно — передано
    (не None) → обновляется, не передано → не трогается. ФИО (tsk-223)
    по-прежнему проверяется `validate_full_name` в самом эндпоинте (единое
    серверное правило формата, чистое русское 422-сообщение) — этот класс
    только требует непустую строку, если она вообще передана.

    tsk-427: category/school_grade/city/timezone — доп. поля профиля,
    заполняются позже в кабинете ученика, не при регистрации. Кросс-
    валидация «class только у школьника» — в сервисе (зависит от текущего
    значения category в БД, если оно не передано этим же запросом).
    """

    full_name: str | None = Field(
        default=None,
        min_length=1,
        description="Реальное ФИО «Фамилия Имя [Отчество]» русскими буквами.",
    )
    category: ProfileCategory | None = Field(
        default=None,
        description=(
            "Категория: school_student (школьник), university_student "
            "(студент вуза), college_student (студент суза), applicant "
            "(абитуриент), adult (взрослый)."
        ),
    )
    school_grade: int | None = Field(
        default=None,
        ge=1,
        le=11,
        description="Класс (1-11) — только для category=school_student.",
    )
    city: str | None = Field(
        default=None, max_length=255, description="Город, свободный текст."
    )
    timezone: str | None = Field(
        default=None,
        description="Часовой пояс, IANA-идентификатор (напр. Europe/Moscow). Вводится вручную.",
    )

    @field_validator("city")
    @classmethod
    def _strip_city(cls, v: str | None) -> str | None:
        return normalize_city(v)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str | None) -> str | None:
        return validate_timezone(v)


# ── tsk-588: автозахват пояса из браузера ────────────────────────────────────

class BrowserTimezoneRequest(BaseModel):
    """Тело PUT /me/timezone/auto — системный пояс устройства, снятый клиентом.

    Отдельный эндпоинт, а не поле в PATCH /me: у этих двух действий разные
    права на значение. PATCH — «человек выбрал», он перебивает всё; этот —
    «так думает устройство», и он уступает ручному выбору.
    """

    timezone: str = Field(
        min_length=1,
        description=(
            "Системный пояс браузера, IANA-идентификатор "
            "(Intl.DateTimeFormat().resolvedOptions().timeZone)."
        ),
    )

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        validated = validate_timezone(v)
        assert validated is not None  # v не None по сигнатуре — для типизации
        return validated


class BrowserTimezoneResponse(BaseModel):
    """Ответ PUT /me/timezone/auto — что в итоге записано в профиле."""

    timezone: str | None = Field(
        description="Пояс пользователя после запроса (может отличаться от присланного)"
    )
    source: TimezoneSource | None = Field(
        description="Источник значения: manual (выбор человека) | auto (снят с браузера)"
    )
    applied: bool = Field(
        description=(
            "True — присланный пояс записан; False — оставлен прежний "
            "(человек выбрал пояс сам, либо значение не изменилось)"
        )
    )


# ── Phase Y-3: /me/identities ────────────────────────────────────────────────

class IdentityRead(BaseModel):
    """Identity link для public read (с masked value)."""

    kind: Literal["email", "tg", "vk"]
    value_masked: str
    created_at: datetime
    last_used_at: datetime | None


# ── Phase Y-3: /me/courses ───────────────────────────────────────────────────

class CourseProgress(BaseModel):
    tasks_total: int
    tasks_done: int
    materials_total: int
    materials_done: int
    percent: int


class CourseWithProgressRead(BaseModel):
    course_id: int
    course_uid: str | None
    title: str
    order_number: int | None
    progress: CourseProgress
    last_active_at: datetime | None
    is_completed: bool


# ── Phase Y-3: /me/last-position ─────────────────────────────────────────────

class LastPositionRead(BaseModel):
    course_id: int
    course_uid: str | None
    course_title: str
    # Корневой курс дерева (root) для построения навигации в SPW. Отличается от
    # course_id, когда элемент в листовом подкурсе. Если корень не определён —
    # совпадает с листовым course_id/course_uid (tsk-127).
    root_course_id: int | None = None
    root_course_uid: str | None = None
    type: Literal["task", "material", "course_completed", "none"]
    task_id: int | None = None
    external_uid: str | None = None
    material_id: int | None = None
    last_active_at: datetime


# ── Phase Y-3: /me/streak ────────────────────────────────────────────────────

class StreakRead(BaseModel):
    streak_days: int
    last_active_date: date | None
    today_active: bool


# ── Phase Y-4: /me/history ───────────────────────────────────────────────────

class HistoryItem(BaseModel):
    """Запись истории попыток ученика."""

    task_result_id: int
    task_id: int
    task_external_uid: str | None
    course_id: int | None
    course_uid: str | None
    course_title: str | None
    task_title: str | None
    type: str | None
    status: Literal["pending_review", "passed", "failed"]
    score: int | None
    max_score: int | None
    comment: str | None
    received_at: datetime
    submitted_at: datetime
    checked_at: datetime | None


# ── Phase Y-6.2: /me/courses/{course_id}/syllabus-states ─────────────────────

SyllabusTaskStatus = Literal[
    "passed",
    "pending_review",
    "failed",
    "blocked_limit",
    "in_progress",
    "not_started",
    "skipped",
]

SyllabusMaterialStatus = Literal["completed", "not_started", "skipped"]
RequirementLevel = Literal["skippable", "recommended", "required"]


class SyllabusTaskItem(BaseModel):
    """Состояние задания в syllabus-дереве курса."""

    kind: Literal["task"] = "task"
    task_id: int
    course_id: int = Field(..., description="ID owner-курса (subcourse, не root)")
    status: SyllabusTaskStatus
    requirement_level: RequirementLevel = Field(
        ...,
        description=(
            "Уровень обязательности ДЛЯ ЭТОГО УЧЕНИКА, а не свойство задания "
            "(tsk-692): обязательное задание, добавленное в курс после того, как "
            "ученик закрыл тему, отдаётся ему как recommended"
        ),
    )
    is_active: bool = True
    attempts_used: int
    attempts_limit_effective: int
    last_score: int | None
    last_max_score: int | None
    last_submitted_at: datetime | None
    # tsk-684: поля для отрисовки пункта оглавления. До них кабинет добирал
    # название и ссылку отдельным `GET /tasks/by-course/{id}` НА КАЖДЫЙ
    # подкурс — 110 запросов и 7,5 МБ за одно открытие флагмана (замер 26.08,
    # docs/qa/2026-08-26-tsk684-zamer-do.md). Аддитивно: прежние поля на месте,
    # клиент со старым контрактом их просто не читает.
    external_uid: str | None = Field(
        None, description="Технический слаг задания — по нему клиент строит ссылку на страницу задания"
    )
    title: str | None = Field(
        None, description="Curated-название из task_content.title; пусто у 45 из 6409 активных заданий"
    )
    task_type: str | None = Field(
        None, description="task_content.type (SC/MC/SA/SA_COM/TA/квизы) — клиент рисует по нему значок"
    )
    stem_preview: str | None = Field(
        None,
        description=(
            "Начало условия без разметки, до 200 символов — запасная подпись для задания "
            "без своего названия. Заполняется ТОЛЬКО когда title пуст: иначе NULL, чтобы не "
            "возить условие задачи впустую. Готовую подпись из этих двух полей собирает клиент"
        ),
    )


class SyllabusMaterialItem(BaseModel):
    """Состояние материала в syllabus-дереве курса."""

    kind: Literal["material"] = "material"
    material_id: int
    course_id: int = Field(..., description="ID owner-курса (subcourse, не root)")
    status: SyllabusMaterialStatus
    requirement_level: RequirementLevel = Field(
        ...,
        description=(
            "Уровень обязательности ДЛЯ ЭТОГО УЧЕНИКА, а не свойство материала "
            "(tsk-692): обязательный материал, добавленный в курс после того, как "
            "ученик закрыл тему, отдаётся ему как recommended"
        ),
    )
    is_active: bool = True
    completed_at: datetime | None
    # tsk-684, та же причина, что у SyllabusTaskItem: без этих двух полей
    # кабинет звал `GET /courses/{id}/materials` на каждый подкурс.
    title: str | None = Field(None, description="Название материала (materials.title)")
    material_type: str | None = Field(None, description="Тип материала: text | video")


SyllabusItem = Union[SyllabusTaskItem, SyllabusMaterialItem]


class SyllabusSectionMeta(BaseModel):
    """Метаданные подкурса в syllabus — для рендера sticky-headers и иерархии (Phase Y-6.2)."""

    course_id: int
    title: str
    depth: int = Field(..., description="0 для root, 1+ для подкурсов")
    parent_course_id: int | None = Field(
        None, description="None для root; для подкурса — ID непосредственного родителя в обходе"
    )
    order_number: int | None = Field(
        None, description="course_parents.order_number (для отладки/UI sort внутри одного уровня)"
    )


class BlockedDependency(BaseModel):
    """Обогащённая проекция одной непройденной зависимости (tsk-231).

    `blocked_courses` (ниже) отдаёт только голый ID заблокированного узла —
    этого недостаточно клиенту, чтобы показать ученику ПОЧЕМУ заблокировано
    и куда идти: нужен ID+название именно required-курса, а не заблокированного.
    """

    course_id: int = Field(..., description="Заблокированный узел (совпадает с элементом blocked_courses)")
    required_course_id: int = Field(..., description="Курс, который нужно завершить, чтобы снять блокировку")
    required_course_title: str
    required_course_uid: str | None = None


class SyllabusStatesResponse(BaseModel):
    """Снимок состояний всех задач+материалов поддерева курса для рендера syllabus.

    Phase Y-6.2: SPW использует для рендера дерева курса с per-item статусами
    (passed / pending_review / failed / blocked / in_progress / not_started)
    и для блокировки subcourse-узлов через `blocked_courses` (course_dependencies
    не выполнены).

    `sections` (Y-6.2 ext): depth-first walk дерева с titles+depth — нужен SPW
    для рендера sticky-headers подкурсов (`/courses/{id}/tree` legacy
    service-key only, недоступен под cookie auth).
    """

    course_id: int
    items: list[SyllabusItem]
    blocked_courses: list[int]
    blocked_dependencies: list[BlockedDependency] = Field(
        default_factory=list,
        description=(
            "tsk-231: обогащённая версия blocked_courses — для каждого "
            "заблокированного узла указывает ID+название required-курса. "
            "blocked_courses НЕ убирается (обратная совместимость)."
        ),
    )
    sections: list[SyllabusSectionMeta] = Field(
        default_factory=list,
        description=(
            "Depth-first walk дерева курса с metadata подкурсов "
            "(course_id, title, depth, parent_course_id, order_number). "
            "Order — тот же, по которому emit'ятся items. Phase Y-6.2 SPW."
        ),
    )


# ──────────────── tsk-301 Фаза 8: витрина прав подписки ─────────────────────


class CapabilityState(BaseModel):
    """Состояние одной возможности для интерфейса ученика.

    `remaining` и `limit` заполнены только у счётной возможности. `warn` —
    признак «осталось мало»: порог считает сервер, а не каждый клиент по-своему,
    иначе веб и бот однажды предупредят на разных числах.
    """

    allowed: bool = Field(..., description="Можно пользоваться прямо сейчас")
    reason: str = Field(..., description="allowed | denied_no_plan | denied_not_in_plan | denied_limit")
    limit: Optional[int] = Field(None, description="Лимит в месяц; null — безлимит или неприменимо")
    remaining: Optional[int] = Field(None, description="Остаток с учётом купленных пакетов")
    warn: bool = Field(False, description="Остаток мал — предупредить ДО исчерпания")
    upgrade_hint: Optional[str] = Field(
        None, description="Что даёт апгрейд. Показывать вместо «недоступно»"
    )


class MyEntitlements(BaseModel):
    """Права ученика целиком — один запрос на все кнопки."""

    plan_code: Optional[str] = Field(None, description="Код тарифа; null — тарифа нет")
    plan_name: Optional[str] = Field(None, description="Имя тарифа для человека")
    content: str = Field("full", description="full | demo — уровень доступа к материалам")
    capabilities: dict[str, CapabilityState] = Field(
        default_factory=dict,
        description="Ключ — возможность (ai_tutor, code_review, teacher_escalation)",
    )
    package_offer: Optional[dict] = Field(
        None,
        description=(
            "Предложение докупить пакет обращений: {units, price_minor}. "
            "null — на этом тарифе пакет не продаётся"
        ),
    )
