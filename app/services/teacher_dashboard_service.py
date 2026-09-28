"""Дашборд преподавателей у методиста (tsk-1147).

Три показателя по каждому преподавателю за период, раздельно «на занятии» и
«вне занятия»:

* **Не с первого раза** — доля заявок, которые ученик вернул хотя бы раз.
  Та же когорта и тот же хозяин заявки, что у `get_reopen_kpi` (tsk-599), —
  выражения берутся оттуда, а не переписываются.
* **Время реакции** — от создания заявки до первого ответа, а если ответа не
  было — до закрытия преподавателем. Момент «взял заявку» в базе не хранится;
  первый ответ — то, что ученик реально увидел; закрытие без текста — устный
  разбор на занятии, тоже реакция. Медиана и доля уложившихся в порог; порог
  свой для занятия и для времени вне его.
* **Обработано** — заявки, закрытые преподавателем, по способу помощи: тип
  последнего ответа (`help_request_replies.reply_kind`), закрыта без ответа —
  «голосом». Исключение — индивидуальный разбор (`individual_review`, tsk-303):
  это встреча по ссылке Телемоста, строки ответа она не оставляет, а
  закрывается системно после оценки ученика (`closed_by` пуст). Закрытый
  разбор без текстового ответа считается обработанным консультацией в Телемосте.

«На занятии» — момент создания заявки попал в окно занятия ученика: общий
предикат `lesson_window_sql.in_lesson_sql` (tsk-1111).

Определения и их обоснование — `docs/specs/2026-09-28-spec-tsk1147-teacher-dashboard.md`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.help_reply_kind import REPLY_KINDS
from app.services.help_requests_service import (
    ACTIVE_TEACHERS_SQL,
    HELP_REQUEST_OWNER_SQL,
    LADDER_TYPES_SQL,
    MIN_REQUESTS_FOR_RATE,
)
from app.services.lesson_window_sql import in_lesson_sql

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

#: Порог реакции на занятии, минут. Решение 28.09 по факту прода: сейчас
#: укладываются 78 % заявок, медиана 3,8 мин — ученик сидит и ждёт.
REACTION_LIMIT_IN_LESSON_MIN = 10
#: Порог реакции вне занятия, минут: сейчас укладываются ~75 %, медиана 10,8 мин.
REACTION_LIMIT_OFF_LESSON_MIN = 120


def _period_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Границы периода по Москве: `[date_from 00:00, date_to + 1 день 00:00)`."""
    start = datetime.combine(date_from, time.min, tzinfo=_MSK)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=_MSK)
    return start, end


def _empty_side() -> dict[str, Any]:
    """Накопитель одной половины («на занятии» или «вне»)."""
    return {
        "requests": 0,
        "reopened_requests": 0,
        "reactions_min": [],
        "processed": 0,
        "by_kind": {k: 0 for k in REPLY_KINDS},
    }


def _finish_side(acc: dict[str, Any], limit_min: int) -> dict[str, Any]:
    """Посчитать доли и медиану; ниже порога объёма — `None` («мало данных»)."""
    reactions: list[float] = acc["reactions_min"]
    enough_requests = acc["requests"] >= MIN_REQUESTS_FOR_RATE
    enough_reactions = len(reactions) >= MIN_REQUESTS_FOR_RATE
    return {
        "requests": acc["requests"],
        "reopened_requests": acc["reopened_requests"],
        "reopen_rate": (
            round(acc["reopened_requests"] / acc["requests"], 4) if enough_requests else None
        ),
        "reacted": len(reactions),
        "reaction_median_min": round(median(reactions), 1) if enough_reactions else None,
        "within_limit": sum(1 for m in reactions if m <= limit_min),
        "within_limit_rate": (
            round(sum(1 for m in reactions if m <= limit_min) / len(reactions), 4)
            if enough_reactions
            else None
        ),
        "reaction_limit_min": limit_min,
        "processed": acc["processed"],
        "by_kind": dict(acc["by_kind"]),
    }


async def get_teacher_dashboard(
    db: AsyncSession, *, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """Строки дашборда по всем действующим преподавателям за период.

    Период режется по `created_at` заявки (по Москве, обе даты включительно) —
    как в сводке возвратов, чтобы числитель и знаменатель считали одно множество.
    """
    start, end = _period_bounds(date_from, date_to)
    in_lesson = in_lesson_sql("h.created_at", "h.student_id")
    rows = (
        await db.execute(
            text(f"""
                WITH cohort AS (
                    SELECT h.id, h.status, h.closed_by, h.created_at, h.closed_at, h.request_type,
                           {HELP_REQUEST_OWNER_SQL} AS owner_id,
                           {in_lesson} AS in_lesson
                      FROM help_requests h
                     WHERE h.request_type IN {LADDER_TYPES_SQL}
                       AND h.created_at >= :start AND h.created_at < :end
                )
                SELECT c.owner_id,
                       c.in_lesson,
                       EXISTS (SELECT 1 FROM help_request_reopens x
                                WHERE x.request_id = c.id) AS reopened,
                       EXTRACT(EPOCH FROM (
                           COALESCE(fr.first_at,
                                    CASE WHEN c.closed_by IS NOT NULL THEN c.closed_at END)
                           - c.created_at)) / 60.0 AS reaction_min,
                       (c.status = 'closed'
                        AND (c.closed_by IS NOT NULL
                             OR c.request_type = 'individual_review')) AS processed,
                       CASE
                           -- Закрыта преподавателем заметно позже последнего
                           -- ответа (после возврата) — разобрали устно.
                           WHEN lr.reply_kind IS NOT NULL
                                AND c.closed_by IS NOT NULL
                                AND c.closed_at > lr.created_at + INTERVAL '1 minute'
                               THEN 'voice'
                           WHEN lr.reply_kind IS NOT NULL THEN lr.reply_kind
                           WHEN c.request_type = 'individual_review' THEN 'telemost'
                           ELSE 'voice'
                       END AS last_kind
                  FROM cohort c
                  LEFT JOIN LATERAL (
                      SELECT MIN(r.created_at) AS first_at
                        FROM help_request_replies r WHERE r.request_id = c.id
                  ) fr ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT r.reply_kind, r.created_at
                        FROM help_request_replies r WHERE r.request_id = c.id
                       ORDER BY r.created_at DESC, r.id DESC
                       LIMIT 1
                  ) lr ON TRUE
                 WHERE c.owner_id IS NOT NULL
            """),  # nosec B608 — фрагменты из литералов модулей, значения через bind
            {"start": start, "end": end},
        )
    ).fetchall()

    blocked = (
        await db.execute(
            text("""
                SELECT closed_by, COUNT(*)
                  FROM help_requests
                 WHERE request_type = 'blocked_limit'
                   AND closed_by IS NOT NULL
                   AND created_at >= :start AND created_at < :end
                 GROUP BY closed_by
            """),
            {"start": start, "end": end},
        )
    ).fetchall()
    blocked_by_teacher = {int(r[0]): int(r[1]) for r in blocked}

    roster = (
        await db.execute(
            text(f"""
                SELECT t.teacher_id, u.full_name
                  FROM ({ACTIVE_TEACHERS_SQL}) t
                  JOIN users u ON u.id = t.teacher_id
            """)  # nosec B608 — литерал модуля
        )
    ).fetchall()
    names: dict[int, Optional[str]] = {int(r[0]): r[1] for r in roster}

    acc: dict[int, dict[str, dict[str, Any]]] = {}
    for owner_id, is_in_lesson, reopened, reaction_min, processed, last_kind in rows:
        tid = int(owner_id)
        side = acc.setdefault(tid, {"in": _empty_side(), "off": _empty_side()})[
            "in" if is_in_lesson else "off"
        ]
        side["requests"] += 1
        if reopened:
            side["reopened_requests"] += 1
        if reaction_min is not None:
            side["reactions_min"].append(max(float(reaction_min), 0.0))
        if processed:
            side["processed"] += 1
            side["by_kind"][last_kind] += 1

    # Ушедший преподаватель остаётся в сводке, если заявки у него были.
    missing = [tid for tid in list(acc) + list(blocked_by_teacher) if tid not in names]
    if missing:
        extra = (
            await db.execute(
                text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
                {"ids": missing},
            )
        ).fetchall()
        names.update({int(r[0]): r[1] for r in extra})

    items: list[dict[str, Any]] = []
    for tid, name in names.items():
        sides = acc.get(tid, {"in": _empty_side(), "off": _empty_side()})
        in_side = _finish_side(sides["in"], REACTION_LIMIT_IN_LESSON_MIN)
        off_side = _finish_side(sides["off"], REACTION_LIMIT_OFF_LESSON_MIN)
        total = in_side["requests"] + off_side["requests"]
        reopened_total = in_side["reopened_requests"] + off_side["reopened_requests"]
        items.append(
            {
                "teacher_id": tid,
                "teacher_name": name,
                "requests": total,
                "reopened_requests": reopened_total,
                "reopen_rate": (
                    round(reopened_total / total, 4) if total >= MIN_REQUESTS_FOR_RATE else None
                ),
                "in_lesson": in_side,
                "off_lesson": off_side,
                "blocked_limit_closed": blocked_by_teacher.get(tid, 0),
            }
        )
    items.sort(key=lambda it: (-it["requests"], it["teacher_name"] or "", it["teacher_id"]))
    logger.info(
        "teacher_dashboard %s..%s: заявок %d, преподавателей %d",
        date_from, date_to, len(rows), len(items),
    )
    return items
