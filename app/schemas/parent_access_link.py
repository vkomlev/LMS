"""
Схемы ссылок доступа родителя (tsk-498).

Сырой токен присутствует ТОЛЬКО в ответе на создание
(`ParentAccessLinkCreatedRead`) — в схеме чтения (`ParentAccessLinkRead`) его
нет: в базе лежит хеш, показать ссылку повторно нельзя, можно только выпустить
новую. Это осознанно: так утечка списка ссылок не равна утечке доступа.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.student_dashboard import StudentDashboardRead


class ParentAccessLinkCreateRequest(BaseModel):
    label: Optional[str] = None


class ParentAccessLinkRead(BaseModel):
    id: int
    student_id: int
    label: Optional[str] = None
    created_at: datetime
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    is_active: bool


class ParentAccessLinkCreatedRead(ParentAccessLinkRead):
    """Ответ на создание — единственное место, где виден сам токен и ссылка."""

    token: str
    url: str


class PublicParentDashboardRead(StudentDashboardRead):
    """Дашборд, открытый по ссылке. Тот же состав, что у привилегированных
    зрителей (контракт tsk-494 — без `solution_rules` и текста заявок помощи),
    плюс имя ученика: родитель по ссылке не имеет профиля и иначе не увидел
    бы, чей это дашборд."""

    student_full_name: Optional[str] = None
