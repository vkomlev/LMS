"""Экран куратора: мои ученики и что требует действия сегодня (tsk-742).

Не «список учеников с метриками». Экран отвечает на один вопрос — **к кому идти
первым**, — и всё остальное на нём подчинено этому. Ученик без открытых поводов
занимает одну строку и не отвлекает; ученик с риском ухода стоит первым, даже
если у соседа тридцать непроверенных работ.

Поводы берутся из уже построенных датчиков (зонтик tsk-589): сигналы
`learning_gap_signal`, очередь ручной проверки, заявки помощи, тишина ученика.
Своих метрик здесь не заводится намеренно: ещё одна шкала, посчитанная по-своему,
разойдётся с той, что видит методист, и спорить они будут вечно.

Сроки («просрочено») — из устава, § 3.2–3.3, через
`curator_activity_service`: правило одно и лежит в одном месте.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import curator_activity_service as activity
from app.services.help_requests_service import awaiting_teacher_sql
from app.services.learning_gaps_service import real_student_results_filter
from app.services.teacher_queue_service import mandatory_review_sql

logger = logging.getLogger(__name__)

#: Окно, за которое считаются пропущенные занятия в карточке. Совпадает с окном
#: датчика риска ухода (tsk-647) намеренно: два разных окна на одном экране
#: заставляют человека каждый раз вспоминать, какое из них он сейчас читает.
MISSED_WINDOW_DAYS = 14

#: Окно, за которое ищется последнее касание куратора. Неделя — ровно срок
#: обязанности «не оставить никого без внимания дольше недели» (устав § 3.1).
TOUCH_WINDOW_DAYS = 7

# Порядок важности поводов. Число уходит в сортировку и в поле `priority`,
# чтобы кабинет не пересчитывал его по-своему.
PRIORITY_URGENT = 3     # риск ухода: ученика может не быть уже на следующем занятии
PRIORITY_OVERDUE = 2    # просрочено: сигнал или работа лежат дольше срока
PRIORITY_ATTENTION = 1  # есть открытый повод, срок ещё не вышел
PRIORITY_CALM = 0       # ничего не требует действия

_BOARD_SQL = """
WITH roster AS (
    SELECT sc.student_id, sc.assigned_at, sc.reason, sc.source
    FROM student_curator sc
    JOIN users s ON s.id = sc.student_id
    WHERE sc.curator_id = :curator_id
      AND sc.ended_at IS NULL
      AND s.is_active AND s.merged_into_user_id IS NULL AND s.blocked_at IS NULL
)
SELECT u.id AS student_id,
       u.full_name AS student_name,
       r.assigned_at,
       r.reason AS assignment_reason,
       r.source AS assignment_source,

       -- Открытые сигналы: сколько, самый срочный повод, возраст старшего.
       sig.open_signals,
       sig.has_urgent,
       sig.oldest_signal_at,
       sig.reasons AS signal_reasons,

       -- Очередь обязательной ручной проверки по этому ученику.
       rev.pending_reviews,
       rev.oldest_submitted_at,

       -- Открытые заявки помощи.
       hlp.open_help_requests,
       hlp.oldest_help_at,

       -- Когда ученик в последний раз работал САМ. Ручные отметки
       -- преподавателя сюда не идут — иначе «тишина» показывала бы активность
       -- преподавателя, а не ученика (73 % строк на бою — ручные).
       own.last_own_work,

       -- Занятия, прошедшие мимо, за окно.
       COALESCE(miss.missed, 0) AS missed_lessons,
       COALESCE(miss.total, 0) AS lessons_in_window
FROM roster r
JOIN users u ON u.id = r.student_id
LEFT JOIN LATERAL (
    SELECT count(*) AS open_signals,
           bool_or(s.reason = ANY(:urgent_reasons)) AS has_urgent,
           min(s.created_at) AS oldest_signal_at,
           string_agg(DISTINCT s.reason, ',') AS reasons
    FROM learning_gap_signal s
    WHERE s.student_id = u.id
      AND s.status IN ('new', 'acknowledged')
      AND s.acknowledged_at IS NULL
) sig ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS pending_reviews, min(tr.submitted_at) AS oldest_submitted_at
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.user_id = u.id
      AND tr.checked_at IS NULL
      AND {mandatory}
) rev ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS open_help_requests, min(hr.created_at) AS oldest_help_at
    FROM help_requests hr
    WHERE hr.student_id = u.id
      AND {awaiting_teacher}
) hlp ON TRUE
LEFT JOIN LATERAL (
    SELECT max(tr.submitted_at) AS last_own_work
    FROM task_results tr
    WHERE tr.user_id = u.id AND {real_student}
) own ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE p.status = 'no_show') AS missed
    FROM lesson_occurrence_participant p
    JOIN lesson_occurrence o ON o.id = p.occurrence_id
    WHERE p.student_id = u.id
      AND p.status IN ('confirmed', 'no_show')
      AND o.scheduled_at >= now() - make_interval(days => :missed_window)
      AND o.scheduled_at < now()
) miss ON TRUE
ORDER BY u.full_name
"""


def _days_since(moment: datetime | None, now: datetime) -> int | None:
    """Сколько полных дней прошло; None, если события не было вовсе.

    None и 0 — разные вещи, и склеивать их нельзя: «ни разу не работал» и
    «работал сегодня» на экране означают противоположное.
    """
    if moment is None:
        return None
    return max(0, (now - moment).days)


async def get_board(db: AsyncSession, *, curator_id: int) -> Dict[str, Any]:
    """Экран куратора: его ученики, поводы и порядок «к кому идти первым».

    Ничего не пишет. Просмотр конкретной карточки фиксируется отдельно
    (`curator_activity_service.record_view`) — открытие общего списка касанием
    не считается: пролистать список не значит посмотреть человека.
    """
    now = datetime.now(timezone.utc)
    sql = _BOARD_SQL.format(  # nosec B608 — подставляются литералы из закрытого набора
        mandatory=mandatory_review_sql("t", "tr"),
        real_student=real_student_results_filter("tr"),
        awaiting_teacher=awaiting_teacher_sql("hr"),
    )
    rows = (await db.execute(text(sql), {
        "curator_id": curator_id,
        "urgent_reasons": list(activity.URGENT_SIGNAL_REASONS),
        "missed_window": MISSED_WINDOW_DAYS,
    })).mappings().all()

    touch_since = now - timedelta(days=TOUCH_WINDOW_DAYS)
    touches = await activity.last_touches(db, curator_id=curator_id, since=touch_since)

    signal_days = activity.signal_response_days()
    urgent_hours = activity.urgent_response_hours()
    review_days = activity.review_response_days()

    students: List[dict] = []
    for r in rows:
        item = dict(r)
        sid = int(item["student_id"])
        reasons_to_act: List[str] = []
        priority = PRIORITY_CALM

        open_signals = int(item["open_signals"] or 0)
        if open_signals:
            age = now - item["oldest_signal_at"]
            if item["has_urgent"]:
                priority = PRIORITY_URGENT
                reasons_to_act.append("риск ухода — разобрать сегодня")
            elif age > timedelta(days=signal_days):
                priority = max(priority, PRIORITY_OVERDUE)
                reasons_to_act.append(f"сигнал висит {age.days} дн.")
            else:
                priority = max(priority, PRIORITY_ATTENTION)
                reasons_to_act.append(f"сигналов открыто: {open_signals}")
            if item["has_urgent"] and age > timedelta(hours=urgent_hours):
                reasons_to_act.append("срок реакции на риск ухода вышел")

        pending = int(item["pending_reviews"] or 0)
        if pending:
            age = now - item["oldest_submitted_at"]
            if age > timedelta(days=review_days):
                priority = max(priority, PRIORITY_OVERDUE)
                reasons_to_act.append(f"работа ждёт проверки {age.days} дн.")
            else:
                priority = max(priority, PRIORITY_ATTENTION)
                reasons_to_act.append(f"на проверке: {pending}")

        help_open = int(item["open_help_requests"] or 0)
        if help_open:
            age = now - item["oldest_help_at"]
            if age > timedelta(days=1):
                priority = max(priority, PRIORITY_OVERDUE)
                reasons_to_act.append(f"заявка помощи ждёт {age.days} дн.")
            else:
                priority = max(priority, PRIORITY_ATTENTION)
                reasons_to_act.append(f"заявок помощи: {help_open}")

        item["silence_days"] = _days_since(item["last_own_work"], now)
        item["priority"] = priority
        item["reasons_to_act"] = reasons_to_act
        # Отдельным полем, а не внутри поводов: «я его давно не смотрел» — это
        # про работу куратора, а не про состояние ученика, и смешивать их
        # на одном экране значит потерять и то и другое.
        last_touch = touches.get(sid)
        item["last_touch_at"] = last_touch
        item["untouched_this_week"] = last_touch is None
        students.append(item)

    students.sort(
        key=lambda s: (
            -s["priority"],
            -(s["open_signals"] or 0),
            s["student_name"] or "",
        )
    )

    return {
        "curator_id": curator_id,
        "students": students,
        "summary": {
            "total": len(students),
            "need_action": sum(1 for s in students if s["priority"] > PRIORITY_CALM),
            "urgent": sum(1 for s in students if s["priority"] == PRIORITY_URGENT),
            "untouched_this_week": sum(1 for s in students if s["untouched_this_week"]),
        },
        "thresholds": {
            "signal_response_days": signal_days,
            "urgent_response_hours": urgent_hours,
            "review_response_days": review_days,
            "touch_window_days": TOUCH_WINDOW_DAYS,
        },
    }
