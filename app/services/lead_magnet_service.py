"""Общее для лид-магнитов: заявка посетителя и воронка (tsk-053).

Квиз подбора (фаза 1) и ЕГЭ-диагностика (фаза 2) устроены по-разному внутри — там
баллы по шкалам предпочтений, здесь сверка с эталоном, — но снаружи заканчиваются
одинаково: человек оставляет контакт, и маркетолог видит, откуда он пришёл и с чем.
Эта общая часть живёт здесь, чтобы второй лид-магнит не переписывал её заново
(и чтобы правка канала или дедупликации не разъезжалась между ними).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.lead import Lead, LeadSource
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

#: Код канала привлечения для заявок с лид-магнитов (миграция tsk053_guest_quiz_lead).
LEAD_MAGNET_SOURCE_CODE = "quiz"


async def upsert_lead(
    db: AsyncSession,
    *,
    course: Courses,
    guest_session_id: UUID,
    contact: str,
    full_name: Optional[str],
    note: str,
) -> Tuple[int, bool]:
    """Завести или обновить заявку по лид-магниту.

    Повторная отправка из той же сессии обновляет существующую заявку: человек,
    поправивший опечатку в телефоне, не должен превращаться в двух разных людей
    в кабинете маркетолога.

    :return: (id заявки, была ли она уже до этого).
    :raises DomainError 503: канала привлечения нет в справочнике — среда не доедена,
        а молча подставлять «другое» нельзя, иначе канал перестанет считаться.
    """
    source_id = (
        await db.execute(
            select(LeadSource.id).where(LeadSource.code == LEAD_MAGNET_SOURCE_CODE)
        )
    ).scalar_one_or_none()
    if source_id is None:
        logger.error("lead_magnet: в справочнике нет канала '%s'", LEAD_MAGNET_SOURCE_CODE)
        raise DomainError(detail="Приём заявок временно недоступен.", status_code=503)

    existing = await find_lead(db, guest_session_id, course.id)
    if existing is not None:
        existing.contact = contact
        if full_name:
            existing.full_name = full_name
        existing.note = note
        await db.flush()
        return existing.id, True

    lead = Lead(
        source_id=source_id,
        source_detail=course.course_uid,
        full_name=full_name,
        contact=contact,
        note=note,
        guest_session_id=guest_session_id,
        quiz_course_id=course.id,
    )
    db.add(lead)
    await db.flush()
    return lead.id, False


async def find_lead(
    db: AsyncSession, guest_session_id: UUID, course_id: int
) -> Optional[Lead]:
    """Заявка, уже оставленная этой гостевой сессией по этому лид-магниту."""
    return (
        (
            await db.execute(
                select(Lead)
                .where(
                    Lead.guest_session_id == guest_session_id,
                    Lead.quiz_course_id == course_id,
                )
                .order_by(Lead.id.desc())
            )
        )
        .scalars()
        .first()
    )


async def get_funnel(db: AsyncSession) -> List[Dict[str, Any]]:
    """Воронка по каждому лид-магниту: начали, прошли до конца, оставили контакт.

    Лид-магнитом считается публичный демо-курс, у которого есть либо квиз-вопросы
    (`SC_Qw`/`MC_Qw`), либо задания с меткой `lead_magnet` — то есть зонды диагностики.
    Признак не по типу задания: у диагностики тип обычный (`SA`), и по типу её было бы
    не отличить от демо-курса с задачами.

    «Начали» и «прошли» считаются по гостевым сессиям, а не по ответам: человек,
    поменявший ответ, — по-прежнему один человек.
    """
    rows = (
        await db.execute(
            text(
                """
                WITH magnet_task AS (
                    -- `step` — то, что человек проходит ОДИН раз. У квиза это сам
                    -- вопрос, у диагностики — тема: зондов на тему заготовлено
                    -- несколько, а показывается ровно один. Считать шагами по
                    -- задачам значило бы требовать от человека 24 ответа там, где
                    -- он даёт 8, и «прошёл до конца» не наступало бы никогда.
                    SELECT t.id,
                           t.course_id,
                           coalesce(
                               t.task_content->'diagnostic_topic'->>'code',
                               t.id::text
                           ) AS step
                    FROM tasks t
                    JOIN courses c ON c.id = t.course_id AND c.is_public_demo
                    WHERE t.is_active
                      AND (t.task_content->>'type' IN ('SC_Qw', 'MC_Qw')
                           OR coalesce((t.task_content->>'lead_magnet')::bool, false))
                ),
                magnet AS (
                    SELECT c.id, c.course_uid, c.title,
                           count(DISTINCT mt.step) AS total_questions
                    FROM courses c
                    JOIN magnet_task mt ON mt.course_id = c.id
                    GROUP BY c.id, c.course_uid, c.title
                ),
                progress AS (
                    SELECT m.id AS course_id,
                           ga.guest_session_id,
                           count(DISTINCT mt.step) AS answered
                    FROM magnet m
                    JOIN magnet_task mt ON mt.course_id = m.id
                    JOIN guest_attempt ga ON ga.task_id = mt.id
                    GROUP BY m.id, ga.guest_session_id
                )
                SELECT m.course_uid,
                       m.title,
                       m.total_questions,
                       count(p.guest_session_id) AS started,
                       count(p.guest_session_id)
                         FILTER (WHERE p.answered >= m.total_questions) AS completed,
                       (SELECT count(*) FROM leads l WHERE l.quiz_course_id = m.id) AS leads
                FROM magnet m
                LEFT JOIN progress p ON p.course_id = m.id
                GROUP BY m.id, m.course_uid, m.title, m.total_questions
                ORDER BY m.course_uid
                """
            )
        )
    ).fetchall()

    funnel: List[Dict[str, Any]] = []
    for course_uid, title, total_questions, started, completed, leads in rows:
        funnel.append(
            {
                "course_uid": course_uid,
                "title": title,
                "total_questions": int(total_questions),
                "started": int(started),
                "completed": int(completed),
                "leads": int(leads),
                "lead_rate": round(leads / completed, 4) if completed else None,
            }
        )
    return funnel
