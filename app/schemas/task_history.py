"""Схемы API «история выполнения задания по паре (ученик, задание)» (tsk-349).

Единый ответ на два эндпоинта:

* ученический ``GET /me/tasks/{task_id}/history`` — свои попытки, комментарии
  учителя, свои обращения за помощью, подсказки. Поле ``solution`` всегда
  ``null`` — правило проверки и эталон ученику не отдаются (инвариант
  «эталон только преподавателю», класс answer-in-stem, tsk-254);
* преподавательский ``GET /teacher/students/{student_id}/tasks/{task_id}/history``
  — то же плюс полное правило проверки/эталон в ``solution`` и диалог заявок помощи.

Разграничение видимости сделано СТРУКТУРНО, а не фильтрацией на выходе: блок
``solution`` собирается только в преподавательской ветке сервиса, ученический путь
его не строит вовсе (вырезание уже собранного — источник утечек).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TaskHistoryAttempt(BaseModel):
    """Одна попытка ученика по заданию (строка ``task_results`` неотменённой попытки)."""

    task_result_id: int
    attempt_id: Optional[int] = None
    attempt_no: int = Field(
        ..., description="Порядковый номер попытки (1-based, по времени сдачи)"
    )
    submitted_at: datetime
    score: int
    max_score: int
    is_correct: Optional[bool] = Field(
        default=None, description="None — сдано и ждёт ручной проверки преподавателя"
    )
    status: Literal["passed", "failed", "pending_review"] = Field(
        ..., description="passed | failed | pending_review (по is_correct)"
    )
    answer_json: Optional[Dict[str, Any]] = Field(
        default=None, description="Ответ самого ученика в этой попытке"
    )
    comment: Optional[str] = Field(
        default=None,
        description="Комментарий преподавателя (ручная проверка / regrade), из metrics.comment",
    )
    checked_at: Optional[datetime] = Field(
        default=None, description="Когда преподаватель проверил (у авто-типов null)"
    )
    manual: bool = Field(
        default=False,
        description="True — зачтено вручную преподавателем (source_system=manual_teacher)",
    )


class TaskHistoryHelpReply(BaseModel):
    """Ответ преподавателя в диалоге заявки помощи."""

    reply_id: int
    teacher_id: int
    body: str
    close_after_reply: bool = False
    created_at: datetime


class TaskHistoryHelpRequest(BaseModel):
    """Обращение ученика за помощью по заданию + диалог ответов преподавателя."""

    request_id: int
    status: Literal["open", "closed"]
    request_type: Literal["manual_help", "blocked_limit"]
    message: Optional[str] = Field(
        default=None, description="Текст обращения ученика (у авто-заявок blocked_limit — null)"
    )
    created_at: datetime
    closed_at: Optional[datetime] = None
    resolution_comment: Optional[str] = None
    # Обязательное (сервис всегда заполняет) — чтобы клиентский тип был не-optional.
    replies: List[TaskHistoryHelpReply] = Field(...)


class TaskHistoryHints(BaseModel):
    """Сколько подсказок ученик открыл по этому заданию (все поля всегда заполнены)."""

    total: int
    text: int
    video: int


class TaskHistoryTaskInfo(BaseModel):
    """Мета-информация о задании (не содержит правила проверки/эталона)."""

    task_id: int
    external_uid: Optional[str] = None
    type: Optional[str] = Field(default=None, description="Тип задания (SC/MC/SA/SA_COM/TBL_COM/TA/…)")
    title: Optional[str] = None
    stem: Optional[str] = Field(default=None, description="Полное условие задания")
    max_score: Optional[int] = None
    course_id: Optional[int] = None
    course_title: Optional[str] = None
    table_columns: Optional[int] = Field(
        default=None,
        description=(
            "TBL_COM: число столбцов таблицы ответа (из task_content.table.columns). "
            "Нужно для рендера ответа/эталона таблицей, а не строкой (tsk-366)."
        ),
    )


class TaskHistoryAcceptedAnswer(BaseModel):
    """Один допустимый эталонный ответ и баллы за него."""

    value: str
    score: int


class TaskHistorySolution(BaseModel):
    """Правило проверки и эталонный ответ задания.

    ТОЛЬКО для преподавателя. В ученическом ответе весь блок отсутствует
    (``TaskHistoryResponse.solution = null``) — он там даже не собирается.
    """

    type: Optional[str] = None
    max_score: int
    scoring_mode: str = Field(..., description="all_or_nothing | partial | custom")
    auto_check: bool
    manual_review_required: bool
    requires_attachment: bool = False
    accepted_answers: List[TaskHistoryAcceptedAnswer] = Field(
        ...,
        description="SA/SA_COM/TBL_COM: допустимые эталонные ответы (для TBL_COM — строкой, ячейки через пробел, ряды через перевод строки)",
    )
    correct_option_ids: List[str] = Field(
        ..., description="SC/MC: id правильных вариантов ответа"
    )
    row_order_matters: Optional[bool] = Field(
        default=None, description="TBL_COM: важен ли порядок рядов при сверке"
    )
    normalization: List[str] = Field(
        ...,
        description="Шаги нормализации перед сравнением (SA/SA_COM/TBL_COM)",
    )
    use_regex: bool = False
    regex: Optional[str] = None


class TaskHistoryResponse(BaseModel):
    """История выполнения задания по паре (ученик, задание)."""

    user_id: int
    task: TaskHistoryTaskInfo
    # Всегда присутствуют (сервис заполняет всегда) → в клиентском типе не-optional.
    attempts: List[TaskHistoryAttempt] = Field(...)
    help_requests: List[TaskHistoryHelpRequest] = Field(...)
    hints: TaskHistoryHints = Field(...)
    solution: Optional[TaskHistorySolution] = Field(
        default=None,
        description=(
            "Правило проверки / эталонный ответ — ТОЛЬКО в преподавательском "
            "ответе. У ученика всегда null (эталон ученику не показываем)."
        ),
    )
