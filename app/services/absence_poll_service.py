"""Опрос «Вы будете на занятии?» во внешних каналах (tsk-1398, этап 4).

**Зачем.** Оператор вручную писал каждому не пришедшему: «Вы опаздываете?»
(решение 21.10). Теперь это делает проход посещаемости.

**Как.**
1. `ask_due` — через `absence_poll_delay_minutes` (5) после начала занятия
   участник всё ещё `scheduled` (не вошёл) → вопрос с кнопками во все его
   внешние каналы (ВК, MAX, Telegram) и строка `absence_poll`.
2. `handle_answer` — ответ кнопкой (или тем же текстом): «Опаздываю» откладывает
   автоотметку неявки до конца занятия; причина пропуска запоминается.
3. `apply_due` — участник стал `no_show` (автоматически или вручную): причина
   из ответа ложится в `lesson_absence_followup` без преподавателя; занятие
   кончилось, а ответа нет — `no_answer`. Отметку преподавателя не трогаем
   (`ON CONFLICT DO NOTHING`): человек главнее автомата.

Каналов у ученика нет — не спрашиваем: спросит преподаватель, как раньше.
Рубильник `absence_poll_enabled` выключен по умолчанию.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.services import inbox_service
from app.services import messenger_channel_service as channels

logger = logging.getLogger(__name__)

SETTING_ENABLED = "absence_poll_enabled"
SETTING_DELAY = "absence_poll_delay_minutes"

#: Ответ → подпись кнопки. Коды причин совпадают с `lesson_absence_followup.reason`.
ANSWERS: dict[str, str] = {
    "late": "Опаздываю, скоро буду",
    "illness": "Не приду: болею",
    "busy": "Не приду: занят(а)",
    "forgot": "Не приду: забыл(а)",
    "other": "Не приду: другая причина",
}
_BY_LABEL = {label.lower(): code for code, label in ANSWERS.items()}
#: Сколько ждать ответа на вопрос, если занятие затянулось или ответ запоздал.
ANSWER_WINDOW = timedelta(hours=3)

QUESTION = "Занятие уже началось, а вас пока нет. Вы будете?"
REPLIES = {
    "late": "Спасибо, предупредим преподавателя. Подключайтесь, как сможете.",
    "reason": "Спасибо, причину передали преподавателю. Задания можно сделать в кабинете.",
}

_DUE_SQL = """
SELECT lop.id, lo.id, lop.student_id, lo.scheduled_at
FROM lesson_occurrence_participant lop
JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
WHERE lop.status = 'scheduled'
  AND lo.scheduled_at + make_interval(mins => :delay) < :now
  AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) > :now
  AND NOT EXISTS (SELECT 1 FROM absence_poll ap WHERE ap.participant_id = lop.id)
"""


def enabled() -> bool:
    """Включён ли опрос в кабинете администратора."""
    return settings_store.get_bool(SETTING_ENABLED)


async def ask_due(db: AsyncSession, *, now: Optional[datetime] = None) -> int:
    """Спросить не пришедших. Не коммитит: проход посещаемости коммитит сам.

    :returns: скольким ученикам вопрос ушёл хотя бы в один канал.
    """
    if not enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(text(_DUE_SQL), {
        "delay": settings_store.get_int(SETTING_DELAY), "now": now,
    })).all()
    asked = 0
    for participant_id, occurrence_id, student_id, _ in rows:
        targets = await channels.student_channels(db, int(student_id))
        if not targets:
            continue
        await db.execute(text(
            "INSERT INTO absence_poll (participant_id, occurrence_id, student_id) "
            "VALUES (:p, :o, :s) ON CONFLICT DO NOTHING"
        ), {"p": participant_id, "o": occurrence_id, "s": student_id})
        delivered = 0
        for channel, external_id in targets:
            try:
                if channel == "tg":
                    delivered += await channels.send_tg_with_buttons(
                        int(external_id), QUESTION, list(ANSWERS.values()),
                    )
                else:
                    await channels.send_text(channel, external_id, QUESTION, buttons=list(ANSWERS.values()))
                    delivered += 1
            except channels.SendRefusedError:
                await channels.disable_by_external(db, channel, external_id, "refused")
            except Exception as e:
                logger.error("tsk-1398: вопрос о занятии в %s не ушёл: %s", channel, type(e).__name__)
        if delivered:
            asked += 1
        else:
            # Ни один канал не принял — строку снимаем, иначе вопрос не
            # повторится никогда (выборка исключает уже спрошенных).
            await db.execute(text("DELETE FROM absence_poll WHERE participant_id = :p"), {"p": participant_id})
    return asked


def parse_answer(body: str) -> Optional[str]:
    """Код ответа по тексту кнопки (или его началу, если клиент обрезал)."""
    t = body.strip().lower()
    if t in _BY_LABEL:
        return _BY_LABEL[t]
    for label, code in _BY_LABEL.items():
        if len(t) >= 8 and label.startswith(t):
            return code
    return None


async def handle_answer(db: AsyncSession, *, student_id: int, channel: str, body: str) -> Optional[str]:
    """Принять ответ ученика на открытый вопрос.

    :returns: текст ответа ученику, если сообщение — ответ на вопрос; иначе None
      (тогда это обычное сообщение, его сохраняет переписка).
    """
    code = parse_answer(body)
    if code is None:
        return None
    poll = (await db.execute(text(
        "SELECT ap.id, ap.occurrence_id FROM absence_poll ap "
        "WHERE ap.student_id = :s AND ap.applied_at IS NULL AND ap.asked_at > :since "
        "ORDER BY ap.asked_at DESC LIMIT 1"
    ), {"s": student_id, "since": datetime.now(timezone.utc) - ANSWER_WINDOW})).first()
    if poll is None:
        return None
    changed = (await db.execute(text(
        "UPDATE absence_poll SET answer = :a, answer_channel = :c, answered_at = now() "
        "WHERE id = :id AND answer IS DISTINCT FROM :a RETURNING id"
    ), {"a": code, "c": channel, "id": poll[0]})).scalar()
    if changed is not None and code == "late":
        await _tell_teachers_late(db, occurrence_id=int(poll[1]), student_id=student_id)
    await db.commit()
    logger.info("tsk-1398: ученик %s ответил на вопрос о занятии: %s (%s)", student_id, code, channel)
    return REPLIES["late"] if code == "late" else REPLIES["reason"]


async def _tell_teachers_late(db: AsyncSession, *, occurrence_id: int, student_id: int) -> None:
    """Преподавателям занятия — в кабинет: ученик опаздывает."""
    from app.services import notification_names

    name = await notification_names.user_display_name(db, student_id)
    teacher_ids = {r[0] for r in (await db.execute(text(
        "SELECT teacher_id FROM lesson_occurrence_teacher WHERE occurrence_id = :o AND is_active"
    ), {"o": occurrence_id})).all()}
    if not teacher_ids:
        teacher_ids = {r[0] for r in (await db.execute(text(
            "SELECT teacher_id FROM lesson_occurrence WHERE id = :o"
        ), {"o": occurrence_id})).all()}
    for teacher_id in teacher_ids:
        await inbox_service.create_for_user(
            db, user_id=int(teacher_id), kind="student_running_late",
            title="Ученик опаздывает", content=f"{name} предупредил(а), что опаздывает.",
            payload={"occurrence_id": occurrence_id, "student_id": student_id},
            created_by=None,
        )


def late_participant_ids_sql() -> str:
    """Условие для автоотметки неявки: «опаздываю» откладывает её до конца занятия."""
    return (
        "NOT EXISTS (SELECT 1 FROM absence_poll ap WHERE ap.participant_id = lop.id "
        "AND ap.answer = 'late' AND lo.scheduled_at + make_interval(mins => lo.duration_minutes) > :now)"
    )


_APPLY_SQL = """
SELECT ap.id, ap.student_id, ap.occurrence_id, ap.answer, ap.answer_channel,
       lop.status, lo.scheduled_at + make_interval(mins => lo.duration_minutes) <= :now AS ended
FROM absence_poll ap
JOIN lesson_occurrence_participant lop ON lop.id = ap.participant_id
JOIN lesson_occurrence lo ON lo.id = ap.occurrence_id
WHERE ap.applied_at IS NULL
"""

_CHANNEL_NAMES = {"vk": "ВКонтакте", "max": "MAX", "tg": "Telegram"}


async def apply_due(db: AsyncSession, *, now: Optional[datetime] = None) -> int:
    """Перенести ответы в отметки о пропуске. Не коммитит.

    :returns: сколько отметок создано.
    """
    now = now or datetime.now(timezone.utc)
    applied = 0
    for poll_id, student_id, occurrence_id, answer, channel, status, ended in (
        await db.execute(text(_APPLY_SQL), {"now": now})
    ).all():
        if status == "scheduled":
            # Неявку ещё не отметили (порог автоотметки бывает длиннее занятия) — ждём.
            continue
        if status != "no_show":
            # Пришёл, отказался заранее или занятие перенесли — переносить нечего.
            await db.execute(text("UPDATE absence_poll SET applied_at = now() WHERE id = :id"), {"id": poll_id})
            continue
        if answer in (None, "late"):
            if not ended:
                continue
            reason, note = "no_answer", "Не ответил(а) на вопрос о занятии в мессенджере"
            if answer == "late":
                reason, note = "other", "Написал(а), что опаздывает, но не пришёл(а)"
        else:
            reason = answer
            note = f"Ответ ученика в {_CHANNEL_NAMES.get(channel or '', 'мессенджере')}"
        res = await db.execute(text(
            "INSERT INTO lesson_absence_followup (student_id, occurrence_id, asked_by, reason, note) "
            "VALUES (:s, :o, NULL, :r, :n) ON CONFLICT DO NOTHING"
        ), {"s": student_id, "o": occurrence_id, "r": reason, "n": note})
        applied += res.rowcount or 0
        await db.execute(text("UPDATE absence_poll SET applied_at = now() WHERE id = :id"), {"id": poll_id})
    return applied
