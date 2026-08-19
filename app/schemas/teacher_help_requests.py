"""Схемы API заявок на помощь преподавателя (Learning Engine V1, этап 3.8 / 3.8.1)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator


HelpRequestStatus = Literal["open", "closed"]
HelpRequestStatusFilter = Literal["open", "closed", "all"]
# tsk-303: третий класс заявки — индивидуальный разбор. Без него карточка и
# список у преподавателя падали бы на сериализации, как только ученик поднялся
# на уровень 2: литерал закрытый, а тип в БД уже допустим.
HelpRequestType = Literal["manual_help", "blocked_limit", "individual_review"]
HelpRequestTypeFilter = Literal["manual_help", "blocked_limit", "individual_review", "all"]


# ----- GET list -----

class HelpRequestListItem(BaseModel):
    """Элемент списка заявок."""
    request_id: int = Field(..., description="ID заявки")
    status: HelpRequestStatus
    request_type: HelpRequestType = Field("manual_help", description="Тип заявки (этап 3.8.1)")
    auto_created: bool = Field(False, description="Создана автоматически при BLOCKED_LIMIT")
    context: Dict[str, Any] = Field(default_factory=dict, description="Контекст (attempts_used, attempts_limit_effective и др.)")
    student_id: int
    student_name: Optional[str] = None
    task_id: int
    task_title: Optional[str] = None
    course_id: Optional[int] = None
    course_title: Optional[str] = None
    attempt_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    thread_id: Optional[int] = None
    event_id: Optional[int] = None
    # Этап 3.9: SLA/приоритет
    priority: int = Field(100, description="Приоритет (меньше — выше)")
    due_at: Optional[datetime] = Field(None, description="Желательный срок обработки")
    is_overdue: bool = Field(False, description="Просрочена ли по due_at")
    # tsk-592: состояние захвата. Колонки в БД жили с этапа 3.9, но наружу не
    # отдавались — интерфейс физически не мог показать «уже в работе», и два
    # преподавателя брались за одну заявку (на проде так вышло с 4 заявками
    # из 49 отвеченных). Поля необязательные и с безопасными значениями по
    # умолчанию — старые клиенты не ломаются.
    is_claimed: bool = Field(
        False,
        description=(
            "Заявка сейчас в работе: захват стоит и не истёк. Истёкший захват "
            "считается свободной заявкой"
        ),
    )
    claimed_by: Optional[int] = Field(
        None, description="ID преподавателя, взявшего заявку; null — свободна"
    )
    claimed_by_name: Optional[str] = Field(
        None, description="Имя преподавателя, взявшего заявку"
    )
    claim_expires_at: Optional[datetime] = Field(
        None, description="До какого момента держится захват"
    )
    claimed_by_me: bool = Field(
        False, description="Захват принадлежит запрашивающему преподавателю"
    )


class HelpRequestListResponse(BaseModel):
    """Ответ списка заявок."""
    items: list[HelpRequestListItem] = Field(default_factory=list)
    total: int = 0


# ----- GET detail -----

class HelpRequestReplyItem(BaseModel):
    """Элемент истории ответов."""
    reply_id: int
    teacher_id: int
    message_id: int
    body: str
    close_after_reply: bool = False
    created_at: datetime


class HelpRequestDetailResponse(HelpRequestListItem):
    """Карточка заявки (список + доп. поля и история)."""
    task_full_title: Optional[str] = Field(
        default=None,
        description=(
            "Полное условие задания (не обрезка в 80 симв., как `task_title`, "
            "а под разумный предел карточки) — учителю нужен весь контекст, "
            "чтобы ответить на заявку помощи."
        ),
    )
    message: Optional[str] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[int] = None
    resolution_comment: Optional[str] = None
    history: list[HelpRequestReplyItem] = Field(default_factory=list, description="Ответы преподавателей")
    # tsk-303: состояние лестницы помощи — преподавателю нужно видеть, на каком
    # уровне заявка и не вернул ли её ученик, иначе он отвечает вслепую.
    reopen_count: int = Field(0, description="Сколько раз ученик возвращал заявку")
    webinar_link: Optional[str] = Field(None, description="Ссылка на разбор (пока заявка открыта)")
    review_understood: Optional[bool] = Field(
        None, description="Оценка ученика после разбора; false — заявка ушла методисту"
    )
    escalated_to_methodist_at: Optional[datetime] = Field(
        None, description="Когда заявка эскалирована методисту (уровень 3)"
    )


# ----- POST close -----

class ReopenKpiItem(BaseModel):
    """Возвраты заявок на одного преподавателя (tsk-303, доля — tsk-599)."""
    teacher_id: int
    teacher_name: Optional[str] = None
    requests: int = Field(
        ...,
        description=(
            "Знаменатель: сколько заявок лестницы за период числится за этим "
            "преподавателем. 0 — за период к нему не обращались"
        ),
    )
    reopened_requests: int = Field(
        ..., description="Числитель: сколько из этих заявок ученики вернули хотя бы раз"
    )
    reopens: int = Field(
        ..., description="Сколько всего было возвратов (одну заявку могли вернуть не раз)"
    )
    reopen_rate: Optional[float] = Field(
        None,
        description=(
            "Доля возвращённых заявок, 0..1. null — заявок меньше порога "
            "min_requests_for_rate, сравнивать нельзя («мало данных»)"
        ),
    )
    last_reopened_at: Optional[datetime] = None


class ReopenKpiResponse(BaseModel):
    """Сводка возвратов. Одна и та же для кабинета преподавателя и методиста."""
    items: list[ReopenKpiItem] = Field(default_factory=list)
    total_reopens: int = Field(0, description="Сумма возвратов по всем строкам выборки")
    since: Optional[datetime] = Field(None, description="Начало окна, если задано")
    min_requests_for_rate: int = Field(
        0,
        description=(
            "Порог показа доли: ниже этого числа заявок процент не считается. "
            "Отдаётся сервером, чтобы панель не держала своё значение порога"
        ),
    )


class WebinarLinkRequest(BaseModel):
    """Тело запроса ссылки на индивидуальный разбор (tsk-303, уровень 2)."""
    teacher_id: int = Field(..., description="ID преподавателя")
    webinar_link: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Ссылка на комнату разбора (http/https), вводится вручную",
    )
    lock_token: Optional[str] = Field(
        None, description="Токен блокировки; при невалидном/просроченном — 409"
    )

    @field_validator("webinar_link")
    @classmethod
    def _must_be_http_url(cls, v: str) -> str:
        """Отбить пустую строку и не-ссылку.

        Ученику эта строка приезжает кнопкой «Перейти к разбору». Текст вместо
        ссылки даст кнопку в никуда, а заявка при этом будет выглядеть
        отвеченной — тот же класс дефекта, что уже закрыт на уровне БД
        (`ck_help_requests_webinar_link_type`), просто с понятной ошибкой
        вместо 500 от нарушенного ограничения.
        """
        link = v.strip()
        if not link:
            raise ValueError("Ссылка не может быть пустой")
        if not (link.startswith("http://") or link.startswith("https://")):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        return link


class WebinarLinkResponse(BaseModel):
    """Ответ на отправку ссылки на разбор."""
    request_id: int
    webinar_link: str
    status: str = Field(..., description="Заявка остаётся открытой — разбор впереди")


class HelpRequestCloseRequest(BaseModel):
    """Тело запроса закрытия заявки."""
    closed_by: int = Field(..., description="ID пользователя, закрывающего заявку")
    resolution_comment: Optional[str] = Field(None, max_length=2000)
    lock_token: Optional[str] = Field(None, description="Токен блокировки (этап 3.9); при невалидном/просроченном — 409")


class HelpRequestCloseResponse(BaseModel):
    """Ответ закрытия заявки."""
    request_id: int
    status: Literal["closed"] = "closed"
    closed_at: Optional[datetime] = None
    updated_at: datetime
    already_closed: bool = False


# ----- POST reply -----

class HelpRequestReplyRequest(BaseModel):
    """Тело запроса ответа на заявку."""
    teacher_id: int = Field(..., description="ID преподавателя")
    message: str = Field(..., min_length=1, max_length=4000, description="Текст ответа студенту")
    close_after_reply: bool = Field(False, description="Закрыть заявку после отправки ответа")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Ключ идемпотентности")
    lock_token: Optional[str] = Field(None, description="Токен блокировки (этап 3.9); при невалидном/просроченном — 409")


class HelpRequestReplyResponse(BaseModel):
    """Ответ на заявку (reply)."""
    request_id: int
    message_id: int
    thread_id: Optional[int] = None
    request_status: HelpRequestStatus = "open"
    deduplicated: bool = False


# ----- GET pending-count (tsk-348) -----

class HelpRequestPendingCountResponse(BaseModel):
    """Количество открытых заявок помощи (manual_help + blocked_limit), назначенных на преподавателя.

    Источник TG_LMS bot-поллера (tsk-348 — до этого поллер отслеживал только
    очередь ручной проверки заданий, help_requests не видел вообще) и
    веб-бейджа учителя в SPW.
    """
    count: int = Field(..., description="Количество открытых заявок, назначенных на преподавателя")
    oldest_created_at: Optional[datetime] = Field(
        None, description="MIN(created_at) среди открытых заявок; null при count=0"
    )
