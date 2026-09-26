"""Регистрация из итога квиза-воронки → полный разбор, заявка, курс (tsk-1139).

Человек прошёл квиз гостем, нажал «Получить полный разбор и демо», вошёл по
ссылке из письма. SPW зовёт claim — и здесь в одной транзакции:

1. гостевая сессия привязывается к ученику (ответы квиза уходят в кабинет);
2. заявка канала «Квиз на сайте» создаётся или дополняется: ученик, ветка, итог, метки;
3. если курс итога бесплатный — ученик записывается на него сам; иначе SPW
   открывает его демо-темы (tsk-1108);
4. возвращается полный разбор: видимая часть, уточнения, разборы мини-проверок,
   абзацы шаблона полного разбора.

Повторный вызов безопасен: привязка и самозапись идемпотентны, заявка одна на
(сессия, квиз).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.guest_session import GuestSession
from app.services import lead_magnet_service, quiz_funnel_service
from app.services.auth import guest_attribution_service
from app.services.free_enrollment_service import enroll_self_free

logger = logging.getLogger(__name__)


@dataclass
class ClaimResult:
    """Что получил ученик после регистрации из квиза."""

    quiz_uid: str
    outcome_code: str
    branch: str
    role: Optional[str]
    breakdown: Dict[str, Any]
    target_course_uid: Optional[str]
    course_id: Optional[int]
    enrolled: bool
    lead_id: int
    bot_start_url: Optional[str]
    buttons: List[Dict[str, Any]] = field(default_factory=list)


async def claim(
    db: AsyncSession,
    *,
    user_id: int,
    contact: str,
    guest_session_id: UUID,
    quiz_uid: str,
) -> ClaimResult:
    """Перенести прохождение в кабинет, завести заявку, открыть курс.

    :param contact: чем связаться — почта или tg ученика из сессии входа.
    :raises HTTPException: 404 — воронка выключена или квиза нет;
        409 — регистрация из этой ветки закрыта, квиз не пройден или сессия
        принадлежит другому ученику.
    """
    funnel = await quiz_funnel_service.load_funnel(db, quiz_uid)
    if funnel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Квиз-воронка не найдена.")

    evaluation = await quiz_funnel_service.evaluate_session(db, funnel, guest_session_id)
    outcome = evaluation.outcome
    if outcome is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Квиз не пройден до конца.")
    branch, role = evaluation.walk.branch, evaluation.walk.role
    if not quiz_funnel_service.registration_open(funnel.spec, branch, evaluation.ctx):
        raise HTTPException(status.HTTP_409_CONFLICT, "Регистрация из этой ветки пока закрыта.")

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
    attribution.update(
        {"branch": branch, "role": role, "outcome": outcome.get("code"), "registered": True}
    )
    note = (
        f"Квиз «{funnel.course.title}», ветка {branch}, итог {outcome.get('code')}: "
        f"зарегистрировался. Курс итога: {outcome.get('target_course_uid') or '—'}."
    )
    lead = await lead_magnet_service.find_lead(db, guest_session_id, funnel.course.id)
    if lead is None:
        await lead_magnet_service.upsert_lead(
            db,
            course=funnel.course,
            guest_session_id=guest_session_id,
            contact=contact,
            full_name=None,
            note=note,
        )
        lead = await lead_magnet_service.find_lead(db, guest_session_id, funnel.course.id)
    else:
        # Контакт, оставленный раньше (телефон, ник в боте), почтой не затираем.
        lead.note = f"{lead.note or ''}\n{note}".strip()
    lead.linked_student_id = user_id
    lead.attribution = {**(lead.attribution or {}), **attribution}
    await db.flush()

    target_uid = outcome.get("target_course_uid")
    course_id: Optional[int] = None
    enrolled = False
    if target_uid:
        course_id = (
            await db.execute(select(Courses.id).where(Courses.course_uid == target_uid))
        ).scalar_one_or_none()
        if course_id is not None:
            try:
                # Отказы (платный курс, тема, выключен) случаются до записи —
                # заявка и привязка сессии в транзакции остаются.
                await enroll_self_free(db, user_id=user_id, course_id=course_id)
                enrolled = True
            except HTTPException as exc:
                logger.info(
                    "tsk-1139: самозапись не применима user_id=%s course_id=%s: %s",
                    user_id, course_id, exc.detail,
                )

    bot_url: Optional[str] = None
    if quiz_funnel_service.pdf_url(funnel.spec, branch):
        token = await quiz_funnel_service.ensure_bot_token(db, guest_session_id, funnel.course.id)
        bot_url = quiz_funnel_service.bot_start_url(token)

    await quiz_funnel_service.save_progress(db, funnel, guest_session_id)
    logger.info(
        "tsk-1139: claim user_id=%s quiz=%s branch=%s outcome=%s lead_id=%s enrolled=%s",
        user_id, quiz_uid, branch, outcome.get("code"), lead.id, enrolled,
    )
    # Кнопки полного разбора — те же, что на итоге, кроме ведущих в регистрацию:
    # человек уже вошёл.
    # Запись на пробное из кабинета — переписка с заполненным сообщением: форма
    # контакта не нужна, человек уже вошёл, а заявка уже есть.
    result = await quiz_funnel_service.get_result(db, funnel, guest_session_id)
    buttons = []
    for button in result.get("buttons", []):
        if button["kind"] in quiz_funnel_service.REGISTRATION_BUTTON_KINDS:
            continue
        if button["kind"] == "lead_trial":
            button = {**button, "url": result.get("contact_url")}
        buttons.append(button)
    return ClaimResult(
        quiz_uid=funnel.course.course_uid or quiz_uid,
        outcome_code=outcome.get("code") or "",
        branch=branch,
        role=role,
        breakdown=quiz_funnel_service.full_breakdown(funnel, evaluation),
        target_course_uid=target_uid,
        course_id=course_id,
        enrolled=enrolled,
        lead_id=lead.id,
        bot_start_url=bot_url,
        buttons=buttons,
    )
