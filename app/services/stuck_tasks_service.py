"""Задания, на которых ученик стоит: несколько неверных попыток подряд или
исчерпанный лимит попыток — и до сих пор не решено.

Отдельный модуль, потому что расчёт нужен двум потребителям с разными
вопросами: плану занятия (tsk-743, «кто буксует прямо сейчас») и очерёдности
внимания в сводке (tsk-648, «к кому подойти сегодня»). Держать его в одном из
них значило бы тянуть в другой всю его цепочку зависимостей — на этом сразу
получился круговой импорт через ``retention_service``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.task_title import humanize_task_title

#: Сколько неверных попыток по ОДНОМУ заданию считаем «застрял».
_STUCK_WRONG_ATTEMPTS = 3

#: Ручные (проставленные преподавателем) результаты не считаются работой
#: ученика — та же константа, что в сводке занятия и в плане.
_MANUAL_SOURCE = "manual_teacher"


async def load_stuck_tasks(
    db: AsyncSession,
    *,
    student_ids: list[int],
    since: datetime,
    until: datetime,
    include_limit_blocked: bool = False,
) -> dict[int, list[dict[str, Any]]]:
    """Задания, где ученик за окно ошибся ``_STUCK_WRONG_ATTEMPTS`` раз и так и
    не решил.

    Это «трудности по заданиям» из постановки, и они не теоретические: замер
    боевой базы за 30 дней — 47 таких пар «ученик + задание» на 26 занятиях из
    56, то есть почти на каждом втором уроке кто-то буксует молча.

    Ручные зачёты преподавателя (``source_system = manual_teacher``) не считаем:
    это не попытка ученика.

    tsk-648: ``include_limit_blocked`` добавляет второй способ упереться —
    исчерпанный лимит попыток (``attempt_limit_reached``). До лимита ученик
    может дойти и с двумя ошибками, если лимит понижен, — тогда порога «три
    неверных» он не переступит, а сдвинуться сам всё равно не может. Условие
    «и до сих пор не решил» здесь несёт основную нагрузку: за 7 дней боевой
    базы в лимит упёрлись 50 пар «ученик + задание» у 24 человек, а остались
    нерешёнными 8 у 8 — остальным преподаватель продлил попытки, и повод
    подходить отпал. Порядок очерёдности (сводка занятия) зовёт с ним, план
    занятия (tsk-743) — без него: там шаг «буксует» про ошибки на уроке.
    """
    if not student_ids:
        return {}
    # Второй источник подключается отдельной веткой UNION, а не отдельным
    # запросом: одно и то же задание может прийти обоими путями, и решать это
    # склейкой в Python значило бы считать порядок из двух разных снимков.
    limit_branch = (
        "    UNION ALL "
        "    SELECT le.student_id AS user_id, (le.payload->>'task_id')::int AS task_id, "
        "           0::bigint AS wrong_cnt, max(le.created_at) AS last_at, true AS by_limit "
        "    FROM learning_events le "
        "    WHERE le.student_id = ANY(:ids) AND le.event_type = 'attempt_limit_reached' "
        "      AND le.created_at >= :since AND le.created_at <= :until "
        "      AND le.payload->>'task_id' IS NOT NULL "
        "    GROUP BY 1, 2 "
    ) if include_limit_blocked else ""

    rows = (
        await db.execute(
            text(
                "WITH candidate AS ( "
                "    SELECT tr.user_id, tr.task_id, count(*) AS wrong_cnt, "
                "           max(tr.submitted_at) AS last_at, false AS by_limit "
                "    FROM task_results tr "
                "    WHERE tr.user_id = ANY(:ids) AND tr.is_correct = false "
                "      AND tr.source_system IS DISTINCT FROM :manual_source "
                "      AND tr.submitted_at >= :since AND tr.submitted_at <= :until "
                "    GROUP BY 1, 2 "
                "    HAVING count(*) >= :min_wrong "
                f"{limit_branch}"
                "), "
                "stuck AS ( "
                "    SELECT user_id, task_id, max(wrong_cnt) AS wrong_cnt, "
                "           max(last_at) AS last_at, bool_or(by_limit) AS by_limit "
                "    FROM candidate GROUP BY 1, 2 "
                ") "
                "SELECT w.user_id, w.task_id, w.wrong_cnt, w.last_at, w.by_limit, "
                "       tk.external_uid, tk.task_content->>'title' AS title_raw, "
                "       tk.task_content->>'stem' AS stem "
                "FROM stuck w "
                "JOIN tasks tk ON tk.id = w.task_id "
                "WHERE NOT EXISTS ( "
                "    SELECT 1 FROM task_results ok "
                "    WHERE ok.user_id = w.user_id AND ok.task_id = w.task_id "
                "      AND ok.is_correct = true "
                ") "
                "ORDER BY w.last_at DESC"
            ),
            {
                "ids": student_ids,
                "since": since,
                "until": until,
                "min_wrong": _STUCK_WRONG_ATTEMPTS,
                "manual_source": _MANUAL_SOURCE,
            },
        )
    ).mappings().fetchall()

    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["user_id"]), []).append(
            {
                "task_id": int(row["task_id"]),
                "task_title": humanize_task_title(
                    int(row["task_id"]), row["title_raw"], row["stem"], row["external_uid"],
                ),
                "wrong_attempts": int(row["wrong_cnt"]),
                "by_limit": bool(row["by_limit"]),
                "last_at": row["last_at"],
            }
        )
    return result


__all__ = ["load_stuck_tasks"]
