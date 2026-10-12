"""tsk-1322: действующие замки зависимостей курсов — ответы API."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DependencyLockRead(BaseModel):
    """Один замок: курс ученика закрыт, пока не пройден другой курс."""

    student_id: int
    course_id: int = Field(..., description="Закрытый корневой курс ученика")
    course_title: str
    required_course_id: int = Field(..., description="Курс, который надо пройти раньше")
    required_course_title: str
    since: Optional[datetime] = Field(
        None,
        description=(
            "С какой даты замок мог действовать: позднейшая из записей ученика на "
            "закрытый курс и на закрывающий. Своей даты у зависимости нет — "
            "оценка сверху"
        ),
    )
    days: Optional[int] = Field(None, description="Сколько полных дней висит")


class AdminDependencyLockRead(DependencyLockRead):
    """Замок в общем списке администратора — с именем ученика."""

    student_name: str
