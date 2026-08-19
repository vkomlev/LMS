"""Схемы API Teacher Next Modes (Learning Engine V1, этап 3.9): claim-next, release, workload."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.code_review import CodeReviewReport


# ----- Help Request Claim Next -----

class HelpRequestClaimNextRequest(BaseModel):
    """Тело запроса «взять следующий help-request»."""
    teacher_id: int = Field(..., description="ID преподавателя")
    request_type: Literal["manual_help", "blocked_limit", "individual_review", "all"] = Field(
        "all",
        description="Тип заявки: manual_help | blocked_limit | all",
    )
    status: Literal["open"] = Field("open", description="На этом этапе только open")
    ttl_sec: int = Field(120, ge=30, le=600, description="Время жизни блокировки в секундах")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Ключ идемпотентности")
    course_id: Optional[int] = Field(None, description="Фильтр по курсу")


class HelpRequestClaimItem(BaseModel):
    """Элемент заявки в ответе claim-next (минимальный контекст)."""
    request_id: int
    status: str
    request_type: str
    student_id: int
    task_id: int
    course_id: Optional[int] = None
    created_at: datetime
    priority: int = 100
    due_at: Optional[datetime] = None
    is_overdue: bool = False


class HelpRequestClaimNextResponse(BaseModel):
    """Ответ claim-next для help-requests."""
    item: Optional[HelpRequestClaimItem] = None
    lock_token: Optional[str] = None
    lock_expires_at: Optional[datetime] = None
    empty: bool = Field(..., description="True, если нет доступного кейса")


# ----- Help Request Claim by id (tsk-592) -----

class HelpRequestClaimRequest(BaseModel):
    """Тело запроса «взять в работу конкретную заявку» (tsk-592).

    Клиент зовёт при открытии карточки: до tsk-592 отметка ставилась только на
    пути «взять следующую» (claim-next), поэтому заявка, открытая из списка,
    для остальных выглядела свободной.
    """
    teacher_id: int = Field(..., description="ID преподавателя")
    ttl_sec: int = Field(
        300,
        ge=30,
        le=1800,
        description=(
            "Срок захвата в секундах. По умолчанию 5 минут — карточку читают "
            "дольше, чем берут кейс из очереди. Повторный вызов продлевает"
        ),
    )
    takeover: bool = Field(
        False,
        description=(
            "Перехватить действующий чужой захват («всё равно взять»). "
            "Перехват пишется в журнал событий"
        ),
    )


class HelpRequestClaimResponse(BaseModel):
    """Ответ захвата конкретной заявки: блокировка выдана (иначе 403/404/409)."""
    item: HelpRequestClaimItem
    lock_token: str
    lock_expires_at: datetime
    took_over_from: Optional[int] = Field(
        None,
        description=(
            "ID преподавателя, у которого заявка перехвачена; "
            "null — заявка была свободна"
        ),
    )
    took_over_from_name: Optional[str] = Field(
        None, description="Имя преподавателя, у которого заявка перехвачена"
    )


# ----- Help Request Release -----

class HelpRequestReleaseRequest(BaseModel):
    """Тело запроса освобождения блокировки заявки."""
    teacher_id: int = Field(..., description="ID преподавателя")
    lock_token: str = Field(..., min_length=1, description="Токен блокировки")


class HelpRequestReleaseResponse(BaseModel):
    """Ответ release для help-request."""
    released: bool = Field(..., description="True, если блокировка снята; False при идемпотентном вызове (уже свободен)")


# ----- Review Claim Next -----

class ReviewClaimNextRequest(BaseModel):
    """Тело запроса «взять следующую проверку»."""
    teacher_id: int = Field(..., description="ID преподавателя")
    ttl_sec: int = Field(120, ge=30, le=600, description="Время жизни блокировки в секундах")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Ключ идемпотентности")
    course_id: Optional[int] = Field(None, description="Фильтр по курсу")
    user_id: Optional[int] = Field(None, description="Фильтр по ученику")


class ReviewClaimItem(BaseModel):
    """Элемент результата в ответе claim-next для проверок (TaskResult + минимальный контекст)."""
    id: int
    task_id: int
    user_id: int
    score: int
    submitted_at: datetime
    max_score: Optional[int] = None
    is_correct: Optional[bool] = None
    answer_json: Optional[Dict[str, Any]] = None
    task_title: Optional[str] = Field(
        None,
        description="Человекочитаемый заголовок задания (title → очищенный stem → external_uid)",
    )
    user_name: Optional[str] = None
    course_id: Optional[int] = None
    # tsk-298 Фаза 2: attempt_id нужен веб-порталу для построения URL скачивания
    # вложений ответа (`/attempts/{attempt_id}/attachments/{id}`). Аддитивно.
    attempt_id: Optional[int] = None
    # tsk-302 этап 0: машинная оценка работы (чистота кода; далее — признак
    # ИИ-авторства и время решения). Аддитивно и опционально: у работ, сданных до
    # включения оценки, и у не-кодовых заданий поле остаётся null, клиент просто
    # не рисует блок. Ученику это поле не уходит — оно только здесь и в
    # `TaskResultRead`, обе поверхности доступны лишь персоналу.
    code_review: Optional[CodeReviewReport] = Field(
        None,
        description=(
            "Машинная оценка работы: секции `code_quality`, далее `ai_authorship` и "
            "`timing`. Null — оценки нет (не код, старая работа, сбой анализа)."
        ),
    )


# ----- Review pending list (tsk-298 Фаза 2, teacher-scoped очередь для веб-портала) -----

class PendingReviewItem(BaseModel):
    """Лёгкий элемент очереди ожидающих проверки (без answer_json).

    Полный ответ ученика приходит при claim конкретной работы
    (`/teacher/reviews/{id}/claim`) — здесь только контекст для списка-очереди.
    """
    id: int = Field(..., description="ID результата (task_result)")
    attempt_id: Optional[int] = None
    task_id: int
    user_id: int
    user_name: Optional[str] = None
    user_timezone: Optional[str] = Field(
        None,
        description=(
            "tsk-588: часовой пояс ученика (IANA id) или None, если не заполнен. "
            "Нужен там, где преподаватель договаривается с учеником о времени."
        ),
    )
    task_title: Optional[str] = Field(
        None,
        description="Человекочитаемый заголовок задания (title → очищенный stem → external_uid)",
    )
    course_id: Optional[int] = None
    score: int
    max_score: Optional[int] = None
    is_correct: Optional[bool] = None
    submitted_at: datetime
    is_claimed: bool = Field(False, description="Работа уже взята кем-то на проверку (действующий lock)")
    has_evidence: bool = Field(
        False,
        description=(
            "tsk-372: есть комментарий (response.comment — там же код для "
            "программистских заданий) или вложение (response.meta.attachments)"
        ),
    )
    auto_checked_part_matched: bool = Field(
        False,
        description=(
            "tsk-396: задание в гибридном режиме (partial_auto_check), и формализуемая "
            "часть ответа УЖЕ сверена авто-чеком и совпала с эталоном — проверять надо "
            "только ручную часть (например, построение диаграммы в ОГЭ-14). Работы с "
            "несошедшейся числовой частью в обязательную очередь не попадают, поэтому "
            "флаг здесь всегда означает «числа верны»."
        ),
    )


class PendingReviewListResponse(BaseModel):
    """Ответ списка очереди проверки преподавателя."""
    items: list[PendingReviewItem]
    total: int = Field(..., description="Всего работ в очереди (без учёта limit/offset)")


class ReviewClaimNextResponse(BaseModel):
    """Ответ claim-next для manual review."""
    item: Optional[ReviewClaimItem] = None
    lock_token: Optional[str] = None
    lock_expires_at: Optional[datetime] = None
    empty: bool = Field(..., description="True, если нет доступного кейса")


# ----- Review Claim by id (tsk-247) -----

class ReviewClaimRequest(BaseModel):
    """Тело запроса «взять на оценку конкретную работу» (tsk-247).

    Нужен для оценки опциональных работ (авто-проверенные SA_COM), которые
    преподаватель открывает из списка, а не получает из обязательной очереди:
    grade требует валидный lock_token, а взять его иначе было негде.
    """
    teacher_id: int = Field(..., description="ID преподавателя")
    ttl_sec: int = Field(120, ge=30, le=600, description="Время жизни блокировки в секундах")


class ReviewClaimResponse(BaseModel):
    """Ответ claim по result_id: блокировка выдана (иначе 403/404/409)."""
    item: ReviewClaimItem
    lock_token: str
    lock_expires_at: datetime


# ----- Review Release -----

class ReviewReleaseRequest(BaseModel):
    """Тело запроса освобождения блокировки проверки."""
    teacher_id: int = Field(..., description="ID преподавателя")
    lock_token: str = Field(..., min_length=1, description="Токен блокировки")


class ReviewReleaseResponse(BaseModel):
    """Ответ release для review."""
    released: bool = Field(..., description="True, если блокировка снята; False при идемпотентном вызове")


# ----- Workload -----

class TeacherWorkloadResponse(BaseModel):
    """Сводка нагрузки преподавателя для главного экрана."""
    open_help_requests_total: int = Field(0, description="Всего открытых заявок на помощь")
    open_blocked_limit_total: int = Field(0, description="Открытых заявок типа blocked_limit")
    open_manual_help_total: int = Field(0, description="Открытых заявок типа manual_help")
    open_individual_review_total: int = Field(
        0, description="Открытых заявок на индивидуальный разбор (tsk-303, уровень 2)"
    )
    pending_manual_reviews_total: int = Field(0, description="Результатов в ожидании ручной проверки")
    overdue_total: int = Field(0, description="Просроченных (due_at < now) открытых заявок")


# ----- Review Grade (Phase Y-4) -----

class ReviewGradeRequest(BaseModel):
    """Тело запроса grade для SA_COM/TA-проверки.

    Y-6 (2026-05-04): убрано поле `is_correct` — оно теперь выводится
    server-side через `REVIEW_PASS_THRESHOLD_RATIO`. Teacher передаёт
    только `score` и опц. `comment`. На клиенте «Принять» = `score=max_score`,
    «Отклонить» = `score=0`, «Указать балл» = произвольное значение.
    """
    teacher_id: int = Field(..., description="ID преподавателя, выставляющего оценку")
    lock_token: str = Field(..., min_length=1, description="Токен блокировки из claim-next")
    score: int = Field(..., ge=0, description="Балл (0..max_score), max_score проверяется в сервисе")
    comment: Optional[str] = Field(
        None, max_length=4096, description="Комментарий преподавателя (опционально)"
    )


class ReviewGradeResponse(BaseModel):
    """Ответ grade endpoint."""
    result_id: int
    task_id: int
    score: int
    max_score: int
    is_correct: bool
    comment: Optional[str] = None
    notification_id: int = Field(..., description="ID созданной inbox-записи ученика")


# ----- Review Regrade (Phase Y-6) -----

class ReviewRegradeRequest(BaseModel):
    """Тело запроса regrade для уже оценённой проверки.

    Y-6 Stage 3. Используется teacher'ом / методистом, чтобы изменить
    оценку (score/comment) после grade. Не idempotent — каждый regrade
    event записывается в `metrics.regrade_history` array.
    """
    score: int = Field(..., ge=0, description="Новый балл (0..max_score)")
    comment: Optional[str] = Field(
        None, max_length=4096, description="Комментарий преподавателя (опционально)"
    )


class ReviewRegradePartScore(BaseModel):
    """Снимок score/is_correct до или после regrade."""
    score: int
    is_correct: bool


class ReviewRegradeResponse(BaseModel):
    """Ответ regrade endpoint."""
    result_id: int
    task_id: int
    old: ReviewRegradePartScore
    new: ReviewRegradePartScore
    comment: Optional[str] = None
    checked_at: datetime
    notification_id: int = Field(..., description="ID inbox-записи ученика")


# ----- Pending Count (Phase Y-4, для TG_LMS поллера) -----

class PendingCountResponse(BaseModel):
    """Количество pending-заявок на проверку для преподавателя (без захвата)."""
    count: int = Field(..., description="Количество pending-заявок (без захвата)")
    oldest_received_at: Optional[datetime] = Field(
        None, description="MIN(submitted_at) среди pending-заявок; null при count=0"
    )
