"""tsk-1516: дашборд методиста по ученикам — агрегаты за период.

Период задаётся календарными датами по МСК (`to` включительно); отклонения
считаются к прошлому периоду той же длительности и к среднему по группе.
Формулы — в задаче tsk-1516 и docstring сервиса.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.services import methodist_students_dashboard_service as svc

router = APIRouter(
    prefix="/methodist", tags=["methodist_students_dashboard"],
)

_METHODIST_GATE = require_role("methodist", "admin")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StudentsDashboardRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    prev_from: str
    prev_to: str
    students: list[dict]
    group_average: dict


@router.get("/students-dashboard", response_model=StudentsDashboardRead)
async def get_students_dashboard(
    from_: str = Query(..., alias="from", description="Начало периода, YYYY-MM-DD (МСК)"),
    to: str = Query(..., description="Конец периода включительно, YYYY-MM-DD (МСК)"),
    student_id: Optional[int] = Query(default=None, description="Один ученик (опционально)"),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(_METHODIST_GATE),
) -> StudentsDashboardRead:
    """Посещаемость, доля трудностей, скорость и дисциплина ДЗ по ученикам."""
    for value, name in ((from_, "from"), (to, "to")):
        if not _DATE_RE.match(value):
            raise HTTPException(400, f"{name} must be YYYY-MM-DD")
    try:
        data = await svc.get_students_dashboard(
            db, from_date=from_, to_date=to, student_id=student_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return StudentsDashboardRead(**data)
