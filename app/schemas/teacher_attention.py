"""Схема ответа GET /teacher/attention/summary (tsk-652).

Единая сводка «требует внимания» для преподавателя: свежие сигналы
`learning_gap_signal` (tsk-646 ai_authorship, tsk-647 dropout_risk, error_rate)
плюс непрочитанные уведомления о простое/пропуске занятия (tsk-591). Не
подменяет существующие экраны (`/learning-gaps/students`, `/me/notifications`) —
только счётчики для доставки-хука (TG_LMS teacher-бот).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TeacherAttentionSummaryResponse(BaseModel):
    """Сводка отклонений, требующих внимания преподавателя.

    `gap_signals_new` — счётчик ОБЩИЙ для всех преподавателей (сигналы
    `learning_gap_signal` не привязаны к конкретному преподавателю, пока его
    не разберут — см. `GET /learning-gaps/students`, тот же принцип «кто
    первый увидел»). `student_idle_unread`/`lesson_missed_unread` — только
    свои: уведомления уже адресованы конкретному `user_id`.
    """

    gap_signals_new: int = Field(
        ..., description="Сигналы learning_gap_signal в статусе new (общий счётчик на всех)"
    )
    student_idle_unread: int = Field(
        ..., description="Непрочитанные notifications.kind='student_idle', адресованные этому преподавателю"
    )
    lesson_missed_unread: int = Field(
        ..., description="Непрочитанные notifications.kind='lesson_missed', адресованные этому преподавателю"
    )
    oldest_created_at: Optional[datetime] = Field(
        None, description="Самый старый непрочитанный/неразобранный элемент из трёх счётчиков выше"
    )
