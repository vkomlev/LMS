"""Сигналы «нужно повторение»: эндпоинты (tsk-572, фаза 7).

Два разных списка для двух разных людей — см. `learning_gap_signals_service`.
Преподаватель видит своих учеников и может ответить: разберусь сам / передать
методисту / это ложное срабатывание. Методист видит темы.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.services import learning_gap_signals_service as signals

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning-gaps", tags=["learning_gaps"])

# Ученические сигналы — преподавателю (и методисту с админом: им полезно
# видеть картину целиком). Темы — методисту: заводить мини-курс всё равно ему.
_TEACHER_GATE = require_role("teacher", "methodist", "admin")
_METHODIST_GATE = require_role("methodist", "admin")


class GapSignalRead(BaseModel):
    id: int
    course_id: int
    course_title: str
    student_id: Optional[int] = None
    student_name: Optional[str] = None
    submissions: int
    students: int
    wrong_rate: float
    status: str
    teacher_comment: Optional[str] = None
    reason: str = Field(
        "error_rate",
        description=(
            "Повод сигнала: `error_rate` — ученик много ошибается; "
            "`ai_authorship` — у работ признак ИИ-авторства (tsk-646); "
            "`dropout_risk` — ученик затих: занятия идут мимо него и сам он в "
            "кабинете не работает (tsk-647). У двух последних поводов "
            "`wrong_rate` честно нулевая и показывать её нельзя — числа лежат "
            "в `meta`"
        ),
        examples=["error_rate", "ai_authorship", "dropout_risk"],
    )
    meta: Optional[dict] = Field(
        None,
        description="Числа повода. Состав зависит от `reason`",
        examples=[
            None,
            {"reason": "ai_authorship", "reviewed": 12, "flagged": 11},
            {"reason": "dropout_risk", "window_days": 14, "lessons_missed": 2,
             "silence_days": 37, "last_attended": None},
        ],
    )

    @property
    def wrong_percent(self) -> int:
        return round(self.wrong_rate * 100)


class SignalDecision(BaseModel):
    """Ответ преподавателя на сигнал."""

    comment: Optional[str] = Field(
        None, max_length=2000,
        description=(
            "Что преподаватель знает про ученика сверх цифр — он видел его "
            "вживую. Уезжает вместе с эскалацией методисту."
        ),
    )
    escalate: bool = Field(
        False,
        description=(
            "False — «разберусь сам на занятии» (нормальный исход: живой канал "
            "есть только у преподавателя). True — передать методисту."
        ),
    )


def _view(rows: list[dict]) -> list[dict]:
    """Добавить процент: доля в интерфейсе читается хуже, чем «78%»."""
    out = []
    for r in rows:
        item = dict(r)
        item["wrong_percent"] = round(float(r["wrong_rate"]) * 100)
        out.append(item)
    return out


@router.get("/students", summary="Кому нужно повторение (преподавателю)")
async def list_student_signals(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> list[dict]:
    """Ученики, которым датчик предлагает повторение.

    Показываются только открытые сигналы: разобранные уходят из списка, иначе
    он копится и его перестают читать.
    """
    return _view(await signals.list_signals(db, for_student=True))


@router.get("/topics", summary="Темы под мини-курс (методисту)")
async def list_topic_signals(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> list[dict]:
    """Что лежит на столе у методиста.

    Не только темы: сюда же попадают ученические сигналы, которые ПЕРЕДАЛ
    преподаватель. Иначе эскалация уходит в никуда — он нажал «передать», а у
    методиста пусто, и оба считают, что дело сделано.
    """
    return _view(await signals.list_signals(
        db, for_student=False, statuses=("new", "acknowledged", "escalated"),
    ))


@router.post("/{signal_id}/acknowledge", summary="Принять к сведению")
async def acknowledge(
    signal_id: int,
    body: SignalDecision,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> dict:
    """Преподаватель разобрался с сигналом.

    Без эскалации — «беру на себя, разберём на занятии». С эскалацией сигнал
    уходит методисту ВМЕСТЕ с комментарием: цифру он и так видит, ценность в
    том, что преподаватель знает про ученика живьём.
    """
    ok = await signals.acknowledge_signal(
        db, signal_id=signal_id, teacher_id=current_user.id,
        comment=body.comment, escalate=body.escalate,
    )
    if not ok:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Сигнал не найден или уже закрыт",
        )
    return {"status": "escalated" if body.escalate else "acknowledged"}


@router.post("/{signal_id}/dismiss", summary="Повторение не нужно")
async def dismiss(
    signal_id: int,
    body: SignalDecision,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> dict:
    """Отклонить сигнал.

    Причина сохраняется намеренно: по отклонениям видно, что датчик шумит
    (ученик болел, сломан эталон), и это основание пересмотреть пороги, а не
    молча терпеть ложные срабатывания.
    """
    ok = await signals.dismiss_signal(
        db, signal_id=signal_id, teacher_id=current_user.id, comment=body.comment
    )
    if not ok:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Сигнал не найден или уже закрыт"
        )
    return {"status": "dismissed"}


class SignalResolution(BaseModel):
    """Итог разбора методистом."""

    comment: Optional[str] = Field(
        None, max_length=2000, description="Чем кончился разбор",
    )
    mini_course_id: Optional[int] = Field(
        None,
        description=(
            "Курс повторения, если он собран. Не обязателен: разбор не всегда "
            "кончается курсом. Но если курс есть — это единственное место, где "
            "видно, ЧЕМ кончилась эскалация"
        ),
        examples=[None, 1460],
    )


@router.post("/{signal_id}/resolve", summary="Методист разобрал сигнал")
async def resolve(
    signal_id: int,
    body: SignalResolution,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> dict:
    """Закрыть переданный методисту сигнал.

    tsk-653: до этой ручки у эскалации не было выхода вовсе. `dismiss` работает
    только из `new`/`acknowledged`, а из `escalated` закрыть сигнал было нечем —
    и 5 сигналов висели в проде с 06.08. Это читалось как «методист их не
    разбирает», хотя кнопки, которая фиксирует разбор, просто не существовало.

    Гейт методистский, а не преподавательский: закрывает тот, кому передали.
    """
    ok = await signals.resolve_signal(
        db, signal_id=signal_id,
        # У сервисного ключа человека нет: `get_current_user` отдаёт таким
        # вызовам `CurrentUser(id=0, is_service=True)`. Записать сюда ноль
        # значит соврать в журнале — «закрыл пользователь 0». Пустое значение
        # честнее и выпадает из `meta` само (`jsonb_strip_nulls`).
        methodist_id=None if current_user.is_service else current_user.id,
        comment=body.comment, mini_course_id=body.mini_course_id,
    )
    if not ok:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Сигнал не найден, не был передан методисту или уже закрыт",
        )
    return {"status": "resolved"}
