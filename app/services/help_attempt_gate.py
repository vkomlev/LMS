"""Гейт «сначала попробуй сам» для помощи по заданию (tsk-993, П4).

Решение оператора Р1: помощь преподавателя и новый разговор с ИИ-наставником
открываются, если у ученика есть хотя бы одна своя сдача по заданию ИЛИ он
описал, где застрял (не короче 20 символов после обрезки пробелов).

Гейт глобальный, под рубильником `HELP_REQUIRES_ATTEMPT` (по умолчанию
выключен): выкат кода не меняет поведение, пока оператор не включит. Не
касается заявок по материалу и автозаявок `blocked_limit` — те и так
возникают после попыток.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.learning_engine_service import LearningEngineService
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

settings = Settings()
_engine = LearningEngineService()

# Порог «описал, где застрял» (Р1). Меньше — это «помогите» без содержания.
STUCK_NOTE_MIN_LEN = 20
# Потолок заметки на уровне схем запросов.
STUCK_NOTE_MAX_LEN = 1000

ATTEMPT_REQUIRED_CODE = "attempt_required"
ATTEMPT_REQUIRED_TEXT = (
    "Сначала попробуй решить сам — отправь ответ или опиши, где застрял"
)


def is_enabled() -> bool:
    """Включён ли гейт (рубильник `HELP_REQUIRES_ATTEMPT`)."""
    return bool(getattr(settings, "help_requires_attempt", False))


def normalize_stuck_note(stuck_note: Optional[str]) -> Optional[str]:
    """Заметка без крайних пробелов; пустая строка — None."""
    if stuck_note is None:
        return None
    cleaned = stuck_note.strip()
    return cleaned or None


def stuck_note_sufficient(stuck_note: Optional[str]) -> bool:
    """Достаточно ли заметки, чтобы открыть помощь без сдачи (≥20 символов)."""
    cleaned = normalize_stuck_note(stuck_note)
    return cleaned is not None and len(cleaned) >= STUCK_NOTE_MIN_LEN


async def is_required(db: AsyncSession, *, student_id: int, task_id: int) -> bool:
    """Закрыта ли помощь до своей сдачи — сигнал клиенту (состояние задания).

    True, только если рубильник включён и своей сдачи нет. Заметка здесь не
    учитывается: клиент по этому признаку показывает поле «Где застрял?».
    """
    if not is_enabled():
        return False
    return not await _engine.has_own_attempt(db, student_id, task_id)


async def enforce(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    stuck_note: Optional[str],
    surface: str,
) -> Optional[int]:
    """Проверить гейт и вернуть ID попытки последней своей сдачи.

    При выключенном рубильнике ничего не проверяет и возвращает None —
    поведение как до tsk-993.

    Корень курса не передаётся: заявка и наставник приходят без контекста
    пути, а прогресс по заданию общий для всех корней (tsk-264) — сдача в
    любом курсе означает, что ученик уже пробовал.

    Args:
        db: сессия БД.
        student_id: ID ученика.
        task_id: ID задания.
        stuck_note: описание «где застрял» (может быть None).
        surface: откуда вызов — для журнала (`request_help`/`ai_tutor`).

    Returns:
        ID попытки последней своей сдачи или None (сдачи нет либо гейт выключен).

    Raises:
        DomainError: 409 `attempt_required` — нет своей сдачи и заметка короче
            порога.
    """
    if not is_enabled():
        return None
    attempt_id = await _engine.last_own_attempt_id(db, student_id, task_id)
    if attempt_id is not None:
        return attempt_id
    if stuck_note_sufficient(stuck_note):
        return None
    logger.info(
        "help gate: отказ attempt_required surface=%s student_id=%s task_id=%s",
        surface, student_id, task_id,
    )
    raise DomainError(
        detail=ATTEMPT_REQUIRED_TEXT,
        status_code=409,
        payload={"code": ATTEMPT_REQUIRED_CODE, "min_note_length": STUCK_NOTE_MIN_LEN},
    )
