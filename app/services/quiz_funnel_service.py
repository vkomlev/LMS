"""Воронка сайта (tsk-1139): метки источника, развилка входного квиза, токен бота.

Квиз-ветка — обычный курс-квиз (итог, рекомендация и замер работают «на курс»),
поэтому здесь только то, чего у одиночного квиза нет: откуда пришёл человек,
какая ветка куда ведёт и как связать итог квиза с ботом.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.courses import Courses
from app.models.guest_session import GuestSession
from app.models.quiz_funnel import QuizFunnelBotLead, QuizFunnelBranch

logger = logging.getLogger(__name__)
_settings = Settings()

#: Какие метки принимаем. Одна схема на сайт — согласуется с tsk-1072.
ATTRIBUTION_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "page",
    "referrer",
    "for",
    "entry_uid",
)
_MAX_VALUE_LEN = 300

#: Префикс параметра стартовой ссылки бота — отличает воронку от прочих /start.
BOT_START_PREFIX = "q_"


def is_enabled() -> bool:
    """Включена ли воронка (рубильник QUIZ_FUNNEL_ENABLED)."""
    return _settings.quiz_funnel_enabled


def reminders_enabled() -> bool:
    """Слать ли напоминания гостям в боте (нужны оба рубильника)."""
    return _settings.quiz_funnel_enabled and _settings.quiz_funnel_reminders_enabled


def sanitize_attribution(raw: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Оставить только известные метки-строки, обрезать длину.

    Метки приходят из адресной строки — то есть от кого угодно. Произвольные
    ключи и вложенные объекты в jsonb не пускаем.
    """
    if not raw:
        return {}
    clean: Dict[str, str] = {}
    for key in ATTRIBUTION_KEYS:
        value = raw.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            clean[key] = str(value).strip()[:_MAX_VALUE_LEN]
    return clean


async def record_attribution(
    db: AsyncSession, guest_session_id: UUID, raw: Optional[Mapping[str, Any]]
) -> Dict[str, str]:
    """Записать метки первого касания в гостевую сессию.

    Пишется один раз: переход из входного квиза в ветку — это наш же переход,
    и он не должен подменить исходный источник.

    :return: метки, которые действуют для сессии после вызова.
    """
    session = await db.get(GuestSession, guest_session_id)
    if session is None:
        return {}
    if session.attribution:
        return dict(session.attribution)
    clean = sanitize_attribution(raw)
    if clean:
        session.attribution = clean
        await db.flush()
    return clean


async def list_entry_branches(
    db: AsyncSession, entry_course_id: int
) -> List[tuple[QuizFunnelBranch, Courses]]:
    """Ветки входного квиза с курсами-ветками, по коду ветки."""
    rows = await db.execute(
        select(QuizFunnelBranch, Courses)
        .join(Courses, Courses.id == QuizFunnelBranch.quiz_course_id)
        .where(QuizFunnelBranch.entry_course_id == entry_course_id)
        .order_by(QuizFunnelBranch.branch_code)
    )
    return [(b, c) for b, c in rows.all()]


async def get_branch(db: AsyncSession, quiz_course_id: int) -> Optional[QuizFunnelBranch]:
    """Настройки ветки, если курс-квиз — ветка воронки."""
    return await db.get(QuizFunnelBranch, quiz_course_id)


async def ensure_bot_token(
    db: AsyncSession, guest_session_id: UUID, quiz_course_id: int
) -> str:
    """Токен стартовой ссылки бота для пары (сессия, ветка) — один на пару.

    В токене нет ни id сессии, ни контактов: ссылку пересылают, и по ней не
    должно читаться ничего, кроме «это гость такой-то ветки».
    """
    existing = (
        await db.execute(
            select(QuizFunnelBotLead).where(
                QuizFunnelBotLead.guest_session_id == guest_session_id,
                QuizFunnelBotLead.quiz_course_id == quiz_course_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.start_token
    row = QuizFunnelBotLead(
        start_token=secrets.token_urlsafe(18),  # 24 символа [A-Za-z0-9_-]
        guest_session_id=guest_session_id,
        quiz_course_id=quiz_course_id,
    )
    db.add(row)
    await db.flush()
    return row.start_token


def bot_start_url(token: str) -> Optional[str]:
    """Стартовая ссылка бота или None, если ник бота не настроен."""
    username = _settings.quiz_funnel_bot_username.strip().lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}?start={BOT_START_PREFIX}{token}"


#: Шаги воронки сайта по ветке (tsk-1139, Ф5). Каждый шаг — подмножество
#: гостевых сессий ветки, дошедших до него; оплата — подтверждённый платёж
#: ученика, созданный после его регистрации из квиза.
_SITE_FUNNEL_SQL = """
WITH br AS (
    SELECT b.quiz_course_id, b.branch_code, c.course_uid,
           (SELECT count(*) FROM tasks t
             WHERE t.course_id = b.quiz_course_id
               AND t.task_content->>'type' IN ('SC_Qw','MC_Qw')) AS total_q
      FROM quiz_funnel_branch b
      JOIN courses c ON c.id = b.quiz_course_id
     WHERE b.entry_course_id = :entry_id
),
sess AS (
    SELECT br.quiz_course_id, ga.guest_session_id,
           count(DISTINCT ga.task_id) AS answered
      FROM br
      JOIN tasks t ON t.course_id = br.quiz_course_id
      JOIN guest_attempt ga ON ga.task_id = t.id
      JOIN guest_session gs ON gs.id = ga.guest_session_id
     WHERE (CAST(:utm_source AS text) IS NULL OR gs.attribution->>'utm_source' = :utm_source)
       AND (CAST(:utm_campaign AS text) IS NULL OR gs.attribution->>'utm_campaign' = :utm_campaign)
     GROUP BY br.quiz_course_id, ga.guest_session_id
),
reg AS (
    SELECT s.quiz_course_id, s.guest_session_id, l.linked_student_id AS user_id, l.updated_at
      FROM sess s
      JOIN leads l ON l.guest_session_id = s.guest_session_id
                  AND l.quiz_course_id = s.quiz_course_id
     WHERE l.linked_student_id IS NOT NULL
)
SELECT br.branch_code, br.course_uid, br.total_q,
       (SELECT count(*) FROM sess s WHERE s.quiz_course_id = br.quiz_course_id) AS started,
       (SELECT count(*) FROM sess s WHERE s.quiz_course_id = br.quiz_course_id
          AND s.answered >= br.total_q AND br.total_q > 0) AS completed,
       (SELECT count(*) FROM reg r WHERE r.quiz_course_id = br.quiz_course_id) AS registered,
       (SELECT count(*) FROM reg r WHERE r.quiz_course_id = br.quiz_course_id
          AND EXISTS (SELECT 1 FROM task_results tr
                       WHERE tr.user_id = r.user_id AND tr.is_correct IS TRUE)) AS first_solved,
       (SELECT count(*) FROM quiz_funnel_bot_lead bl
          JOIN sess s ON s.guest_session_id = bl.guest_session_id
                     AND s.quiz_course_id = bl.quiz_course_id
         WHERE bl.quiz_course_id = br.quiz_course_id AND bl.started_at IS NOT NULL) AS bot_started,
       (SELECT count(*) FROM quiz_funnel_bot_lead bl
          JOIN sess s ON s.guest_session_id = bl.guest_session_id
                     AND s.quiz_course_id = bl.quiz_course_id
         WHERE bl.quiz_course_id = br.quiz_course_id
           AND bl.trial_requested_at IS NOT NULL) AS trial_requested,
       (SELECT count(DISTINCT r.user_id) FROM reg r
          JOIN student_payment p ON p.student_id = r.user_id
         WHERE r.quiz_course_id = br.quiz_course_id
           AND p.status = 'confirmed' AND p.created_at >= r.updated_at) AS paid
  FROM br
 ORDER BY br.branch_code
"""


async def get_site_funnel(
    db: AsyncSession,
    entry_uid: str,
    utm_source: Optional[str] = None,
    utm_campaign: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Шаги воронки по веткам входного квиза: старт → итог → регистрация →
    первое решённое задание → бот → пробное → оплата.

    :return: None — входного квиза нет.
    """
    from sqlalchemy import text as sa_text

    entry_id = (
        await db.execute(select(Courses.id).where(Courses.course_uid == entry_uid))
    ).scalar_one_or_none()
    if entry_id is None:
        return None
    rows = await db.execute(
        sa_text(_SITE_FUNNEL_SQL),
        {"entry_id": entry_id, "utm_source": utm_source, "utm_campaign": utm_campaign},
    )
    return [dict(r) for r in rows.mappings()]
