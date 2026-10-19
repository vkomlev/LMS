"""Гейт «сначала попробуй сам» для помощи по заданию (tsk-993, П4).

Решение оператора Р1: помощь преподавателя и новый разговор с ИИ-наставником
открываются, если у ученика есть хотя бы одна своя сдача по заданию ИЛИ он
описал, где застрял (не короче 20 символов после обрезки пробелов).

tsk-1384: третье основание — кнопка «Не понимаю, с чего начать»
(`stuck_reason='no_start'`): ученик без единой идеи не догадывался описать
это словами и просил преподавателя устно, а факт обращения терялся.

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

# tsk-1384: основания помощи без своей сдачи (`help_requests.stuck_reason`).
# `no_start` — кнопка «Не понимаю, с чего начать»: у ученика нет ни одной идеи,
# и описать, где застрял, ему нечем. Решение оператора 19.10 — допустимое
# основание наравне с заметкой.
STUCK_REASON_NOTE = "note"
STUCK_REASON_NO_START = "no_start"
STUCK_REASONS = (STUCK_REASON_NO_START, STUCK_REASON_NOTE)

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


def resolve_stuck_reason(
    stuck_note: Optional[str], stuck_reason: Optional[str]
) -> Optional[str]:
    """Что записать в `help_requests.stuck_reason` (tsk-1384).

    `no_start` — если ученик нажал «Не понимаю, с чего начать»; иначе `note`,
    если есть непустая заметка; иначе None. Пишется независимо от гейта: это
    факт о самом обращении, преподаватель видит его в карточке.
    """
    if stuck_reason == STUCK_REASON_NO_START:
        return STUCK_REASON_NO_START
    if normalize_stuck_note(stuck_note) is not None:
        return STUCK_REASON_NOTE
    return None


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
    stuck_reason: Optional[str] = None,
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
        stuck_reason: `no_start` — ученик нажал «Не понимаю, с чего начать»
            (tsk-1384): основание наравне с заметкой ≥20 символов. Иные
            значения гейт не снимают.
        surface: откуда вызов — для журнала (`request_help`/`ai_tutor`).

    Returns:
        ID попытки последней своей сдачи или None (сдачи нет либо гейт выключен).

    Raises:
        DomainError: 409 `attempt_required` — нет своей сдачи, заметка короче
            порога и не нажато «Не понимаю, с чего начать».
    """
    if not is_enabled():
        return None
    attempt_id = await _engine.last_own_attempt_id(db, student_id, task_id)
    if attempt_id is not None:
        return attempt_id
    if stuck_note_sufficient(stuck_note):
        return None
    if stuck_reason == STUCK_REASON_NO_START:
        logger.info(
            "help gate: no_start surface=%s student_id=%s task_id=%s",
            surface, student_id, task_id,
        )
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
