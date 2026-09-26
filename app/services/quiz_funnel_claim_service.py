"""Регистрация из итога квиза-ветки → заявка + демо (tsk-1139, Ф2).

Человек прошёл ветку гостем, нажал «Получить полный разбор и демо», вошёл по
ссылке из письма. SPW зовёт claim — и здесь в одной транзакции:

1. гостевая сессия привязывается к ученику (ответы квиза уходят в кабинет);
2. заявка канала «Квиз на сайте» создаётся или дополняется: ученик, ветка, метки;
3. если рекомендованный курс бесплатный — ученик записывается на него сам;
   иначе SPW открывает его демо-темы (tsk-1108), записывать не на что.

Повторный вызов безопасен: привязка и самозапись идемпотентны, заявка одна на
(сессия, квиз).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.guest_session import GuestSession
from app.schemas.guest_quiz import QuizRecommendation
from app.services import guest_quiz_service, lead_magnet_service, quiz_funnel_service
from app.services.auth import guest_attribution_service
from app.services.free_enrollment_service import enroll_self_free

logger = logging.getLogger(__name__)


@dataclass
class ClaimResult:
    """Что получил ученик после регистрации из квиза."""

    quiz_uid: str
    branch_code: str
    scales: Dict[str, int]
    recommendation: Optional[QuizRecommendation]
    course_id: Optional[int]
    enrolled: bool
    lead_id: int
    bot_start_url: Optional[str]


async def claim(
    db: AsyncSession,
    *,
    user_id: int,
    contact: str,
    guest_session_id: UUID,
    quiz_uid: str,
) -> ClaimResult:
    """Перенести прохождение ветки в кабинет, завести заявку, открыть курс.

    :param contact: чем связаться — почта или tg ученика из сессии входа.
    :raises HTTPException: 404 — воронка выключена или это не квиз-ветка;
        409 — регистрация из этой ветки закрыта, квиз не пройден или сессия
        принадлежит другому ученику.
    """
    if not quiz_funnel_service.is_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Воронка квиза выключена.")

    evaluated = await guest_quiz_service.evaluate_quiz(db, quiz_uid, guest_session_id)
    if evaluated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Квиз не найден.")
    course, is_complete, totals, recommendation = evaluated

    branch = await quiz_funnel_service.get_branch(db, course.id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Это не квиз воронки.")
    if not branch.registration_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Регистрация из этой ветки пока закрыта.")
    if not is_complete:
        raise HTTPException(status.HTTP_409_CONFLICT, "Квиз не пройден до конца.")

    try:
        await guest_attribution_service.attribute_guest_post_login(
            db=db, user_id=user_id, guest_session_id=guest_session_id
        )
    except guest_attribution_service.GuestAttributionConflictError as exc:
        logger.warning("tsk-1139: claim чужой сессии user_id=%s: %s", user_id, exc)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Это прохождение уже привязано к другой учётной записи."
        ) from exc

    session = await db.get(GuestSession, guest_session_id)
    attribution = dict(session.attribution or {}) if session else {}
    attribution["branch"] = branch.branch_code
    attribution["registered"] = True

    rec_title = recommendation.title if recommendation else "не определилась"
    note = (
        f"Квиз «{course.title}», ветка {branch.branch_code}: зарегистрировался. "
        f"Рекомендация: {rec_title}. Шкалы: {totals or '—'}."
    )
    existing = await lead_magnet_service.find_lead(db, guest_session_id, course.id)
    if existing is not None:
        # Контакт, оставленный гостем раньше (телефон), не затираем почтой.
        existing.linked_student_id = user_id
        existing.attribution = {**(existing.attribution or {}), **attribution}
        existing.note = f"{existing.note or ''}\n{note}".strip()
        lead_id = existing.id
    else:
        lead_id, _ = await lead_magnet_service.upsert_lead(
            db,
            course=course,
            guest_session_id=guest_session_id,
            contact=contact,
            full_name=None,
            note=note,
        )
        lead = await lead_magnet_service.find_lead(db, guest_session_id, course.id)
        if lead is not None:
            lead.linked_student_id = user_id
            lead.attribution = attribution
    await db.flush()

    course_id: Optional[int] = None
    enrolled = False
    if recommendation is not None:
        course_id = (
            await db.execute(
                select(Courses.id).where(Courses.course_uid == recommendation.course_uid)
            )
        ).scalar_one_or_none()
        if course_id is not None:
            try:
                # Отказы (платный курс, тема, выключен) случаются до записи —
                # заявка и привязка сессии в транзакции остаются.
                await enroll_self_free(db, user_id=user_id, course_id=course_id)
                enrolled = True
            except HTTPException as exc:
                # Платный или не корневой курс — не ошибка: откроются демо-темы.
                logger.info(
                    "tsk-1139: самозапись не применима user_id=%s course_id=%s: %s",
                    user_id, course_id, exc.detail,
                )

    bot_url: Optional[str] = None
    if branch.pdf_url:
        token = await quiz_funnel_service.ensure_bot_token(db, guest_session_id, course.id)
        bot_url = quiz_funnel_service.bot_start_url(token)

    logger.info(
        "tsk-1139: claim user_id=%s quiz=%s branch=%s lead_id=%s enrolled=%s",
        user_id, quiz_uid, branch.branch_code, lead_id, enrolled,
    )
    return ClaimResult(
        quiz_uid=course.course_uid or quiz_uid,
        branch_code=branch.branch_code,
        scales=totals,
        recommendation=recommendation,
        course_id=course_id,
        enrolled=enrolled,
        lead_id=lead_id,
        bot_start_url=bot_url,
    )
