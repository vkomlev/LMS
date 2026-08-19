"""Лента активности учеников для преподавателя (tsk-408).

Единый поток четырёх типов событий, читаемых из разных таблиц:

* ``task_solved`` — реальные (не ручной зачёт) результаты ``task_results``;
* ``help_requested`` — заявки помощи ``help_requests`` (момент создания);
* ``material_studied`` — завершённые (не ручной зачёт) материалы
  ``student_material_progress``;
* ``student_idle`` — простой ученика во время занятия ``lesson_idle_episode``
  (tsk-591): ушёл из кабинета или сидит без действий дольше порога.

Каждый источник читается ОТДЕЛЬНЫМ read-only запросом (top-``limit`` по своему
времени, с ACL и опциональным курсором ``before``), после чего результаты
сливаются и обрезаются до общего топ-``limit`` в Python. Для текущего объёма
данных (см. tsk-297/349) это проще и надёжнее одного гигантского UNION ALL по
разнородным таблицам, а стоимость — три недорогих индексных запроса вместо одного.

ACL — тот же принцип, что у ``manual_progress_service.can_edit_progress``
(tsk-297) и ``teacher_queue_service`` (``HELP_REQUESTS_ACL_SQL``/``REVIEW_ACL_SQL``):
методист/админ — bypass; преподаватель — только свои ученики
(``student_teacher_links``) или ученики на закреплённых за ним курсах
(``teacher_course_acl``, рекурсия по ``course_parents``).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.schemas.code_review import build_code_review_badge
from app.services import roles_service
from app.services.teacher_queue_service import teacher_course_acl
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

#: Провенанс синтетических (не реальных) записей — не студенческая активность.
_MANUAL_SOURCE = "manual_teacher"
_ELEVATED_ROLES = frozenset({"admin", "methodist"})


def _scope_predicate(student_col: str, course_col: str) -> str:
    """SQL-фрагмент «ученик/курс в зоне ответственности :teacher_id».

    Тот же принцип, что у ``can_edit_progress``: ученик закреплён напрямую
    (``student_teacher_links``) ИЛИ курс попадает под иерархический ACL
    (``teacher_course_acl``). Вызывается только когда bypass (admin/methodist)
    уже исключён на уровне Python — сюда user-input не попадает, только
    литералы столбцов из закрытого набора call-sites этого модуля.
    """
    return f"""
        (
            EXISTS (
                SELECT 1 FROM student_teacher_links stl
                WHERE stl.student_id = {student_col} AND stl.teacher_id = :teacher_id
            )
            OR ({course_col} IS NOT NULL AND {teacher_course_acl(course_col)})
        )
    """  # nosec B608 — student_col/course_col из закрытого набора литералов модуля


async def _is_elevated(db: AsyncSession, current_user: CurrentUser) -> bool:
    """Сервисный токен / роль admin|methodist — полный доступ ко всем ученикам."""
    if current_user.is_service:
        return True
    roles = {
        r.lower().strip() for r in await roles_service.get_user_role_names(db, current_user.id)
    }
    return bool(roles & _ELEVATED_ROLES)


def _outcome_of(is_correct: Optional[bool]) -> str:
    if is_correct is None:
        return "pending_review"
    return "correct" if is_correct else "incorrect"


async def _fetch_task_solved(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime]
) -> List[Dict[str, Any]]:
    """Реальные результаты заданий (без синтетических зачётов tsk-297)."""
    conds = ["tr.source_system IS DISTINCT FROM :manual_source"]
    params: Dict[str, Any] = {
        "manual_source": _MANUAL_SOURCE, "teacher_id": teacher_id, "limit": limit,
    }
    if before is not None:
        conds.append("tr.submitted_at < :before")
        params["before"] = before
    if not elevated:
        conds.append(_scope_predicate("tr.user_id", "t.course_id"))
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT tr.user_id AS student_id, u.full_name AS student_name,
                       tr.task_id, t.course_id, t.external_uid,
                       t.task_content->>'title' AS title_raw, t.task_content->>'stem' AS stem,
                       tr.is_correct, tr.submitted_at AS event_at,
                       tr.code_review
                FROM task_results tr
                JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                JOIN tasks t ON t.id = tr.task_id
                JOIN users u ON u.id = tr.user_id
                WHERE {where_sql}
                ORDER BY tr.submitted_at DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        title = humanize_task_title(r["task_id"], r["title_raw"], r["stem"], r["external_uid"])
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        outcome = _outcome_of(r["is_correct"])
        verdict = {
            "correct": "верно", "incorrect": "неверно", "pending_review": "на проверке",
        }[outcome]
        verb = "сдал" if outcome == "pending_review" else "решил"
        events.append({
            "type": "task_solved",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": int(r["task_id"]),
            "material_id": None,
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            "timestamp": r["event_at"],
            "summary": f"{student} — {verb} задание «{title}» ({verdict})",
            "outcome": outcome,
            # tsk-302: компактный значок машинной оценки. Полный отчёт в ленту не
            # тащим — на сотне событий это лишний вес ради данных, которые в
            # строку списка всё равно не поместятся; подробности преподаватель
            # смотрит в карточке задания.
            "code_review": build_code_review_badge(r["code_review"]),
        })
    return events


async def _fetch_help_requested(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime]
) -> List[Dict[str, Any]]:
    """Заявки помощи (момент создания — ``created_at``)."""
    conds = ["1 = 1"]
    params: Dict[str, Any] = {"teacher_id": teacher_id, "limit": limit}
    if before is not None:
        conds.append("hr.created_at < :before")
        params["before"] = before
    if not elevated:
        conds.append(_scope_predicate("hr.student_id", "COALESCE(hr.course_id, t.course_id)"))
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT hr.student_id, u.full_name AS student_name, hr.task_id,
                       COALESCE(hr.course_id, t.course_id) AS course_id,
                       t.external_uid, t.task_content->>'title' AS title_raw,
                       t.task_content->>'stem' AS stem, hr.status, hr.created_at AS event_at
                FROM help_requests hr
                JOIN users u ON u.id = hr.student_id
                LEFT JOIN tasks t ON t.id = hr.task_id
                WHERE {where_sql}
                ORDER BY hr.created_at DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        title = humanize_task_title(r["task_id"], r["title_raw"], r["stem"], r["external_uid"])
        events.append({
            "type": "help_requested",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": int(r["task_id"]),
            "material_id": None,
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            "timestamp": r["event_at"],
            "summary": f"{student} — запросил помощь по заданию «{title}»",
            "outcome": r["status"],
        })
    return events


async def _fetch_material_studied(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime]
) -> List[Dict[str, Any]]:
    """Завершённые материалы (без синтетических зачётов tsk-297)."""
    conds = [
        "smp.status = 'completed'",
        "smp.completed_at IS NOT NULL",
        "smp.source IS DISTINCT FROM :manual_source",
    ]
    params: Dict[str, Any] = {
        "manual_source": _MANUAL_SOURCE, "teacher_id": teacher_id, "limit": limit,
    }
    if before is not None:
        conds.append("smp.completed_at < :before")
        params["before"] = before
    if not elevated:
        conds.append(_scope_predicate("smp.student_id", "m.course_id"))
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT smp.student_id, u.full_name AS student_name, smp.material_id,
                       m.course_id, m.title AS material_title, smp.completed_at AS event_at
                FROM student_material_progress smp
                JOIN materials m ON m.id = smp.material_id
                JOIN users u ON u.id = smp.student_id
                WHERE {where_sql}
                ORDER BY smp.completed_at DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        events.append({
            "type": "material_studied",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": None,
            "material_id": int(r["material_id"]),
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            "timestamp": r["event_at"],
            "summary": f"{student} — изучил материал «{r['material_title']}»",
            "outcome": None,
        })
    return events


async def _fetch_student_idle(
    db: AsyncSession, *, teacher_id: int, elevated: bool, limit: int, before: Optional[datetime]
) -> List[Dict[str, Any]]:
    """Простои учеников на занятиях (tsk-591) — эпизоды «затих → вернулся».

    ACL шире, чем у остальных источников, и это намеренно: кроме обычной зоны
    ответственности (свои ученики / свои курсы) событие видит тот, кто ВЕДЁТ
    это занятие. Иначе преподаватель на подмене не увидел бы простой ученика,
    сидящего прямо у него на уроке, — то есть ровно того, ради кого сигнал и
    заводился.
    """
    conds = ["1 = 1"]
    params: Dict[str, Any] = {"teacher_id": teacher_id, "limit": limit}
    if before is not None:
        conds.append("e.detected_at < :before")
        params["before"] = before
    if not elevated:
        conds.append(
            f"""(
                {_scope_predicate("e.student_id", "e.course_id")}
                OR EXISTS (
                    SELECT 1 FROM lesson_occurrence_teacher lot
                    WHERE lot.occurrence_id = e.occurrence_id
                      AND lot.teacher_id = :teacher_id AND lot.is_active
                )
                OR EXISTS (
                    SELECT 1 FROM lesson_occurrence lo
                    WHERE lo.id = e.occurrence_id AND lo.teacher_id = :teacher_id
                )
            )"""
        )
    where_sql = " AND ".join(conds)

    rows = (
        await db.execute(
            text(f"""
                SELECT e.student_id, u.full_name AS student_name, e.kind,
                       e.task_id, e.material_id, e.course_id, e.context,
                       e.silent_since, e.detected_at, e.resolved_at,
                       t.external_uid, t.task_content->>'title' AS title_raw,
                       t.task_content->>'stem' AS stem,
                       m.title AS material_title
                FROM lesson_idle_episode e
                JOIN users u ON u.id = e.student_id
                LEFT JOIN tasks t ON t.id = e.task_id
                LEFT JOIN materials m ON m.id = e.material_id
                WHERE {where_sql}
                ORDER BY e.detected_at DESC
                LIMIT :limit
            """),  # nosec B608 — where_sql из закрытого набора литералов модуля
            params,
        )
    ).mappings().fetchall()

    events: List[Dict[str, Any]] = []
    for r in rows:
        student = r["student_name"] or f"Ученик #{r['student_id']}"
        resolved = r["resolved_at"] is not None
        end = r["resolved_at"] or r["detected_at"]
        minutes = max(1, int((end - r["silent_since"]).total_seconds() // 60))

        if r["context"] == "task" and r["task_id"] is not None:
            title = humanize_task_title(
                r["task_id"], r["title_raw"], r["stem"], r["external_uid"]
            )
            where = f", открыто задание «{title}»"
        elif r["context"] == "material" and r["material_title"]:
            where = f", открыт материал «{r['material_title']}»"
        else:
            where = ""

        if r["kind"] == "away":
            body = f"вышел из кабинета на {minutes} мин во время занятия"
        else:
            body = f"{minutes} мин без действий на занятии{where}"
        tail = " — уже вернулся" if resolved else ""

        events.append({
            "type": "student_idle",
            "student_id": int(r["student_id"]),
            "student_name": r["student_name"],
            "task_id": int(r["task_id"]) if r["task_id"] is not None else None,
            "material_id": int(r["material_id"]) if r["material_id"] is not None else None,
            "course_id": int(r["course_id"]) if r["course_id"] is not None else None,
            # Время события — момент обнаружения: лента про свежесть, а простой
            # становится фактом именно тогда, когда его заметили.
            "timestamp": r["detected_at"],
            "summary": f"{student} — {body}{tail}",
            "outcome": "resolved" if resolved else "ongoing",
        })
    return events


async def get_activity_feed(
    db: AsyncSession,
    current_user: CurrentUser,
    *,
    limit: int = 100,
    before: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], bool, Optional[datetime]]:
    """Собрать топ-``limit`` событий по всем ученикам преподавателя, по убыванию времени.

    :param before: курсор пагинации — вернуть только события строго раньше этого
        момента (клиент передаёт ``next_before`` предыдущей страницы).
    :returns: (события, has_more, next_before). ``has_more`` — True в двух
        случаях: (1) слитый набор источников больше ``limit`` — значит,
        страница обрезала уже известные (полученные) события; (2) хотя бы
        один источник вернул ровно ``limit`` строк — у него могут быть ещё
        более старые записи, которые не запрашивались вовсе (``LIMIT`` мог
        обрезать раньше выборки). Только проверки (2) недостаточно: три
        источника по 60 строк каждый (180 > ``limit``=100) не капнутся ни по
        одному, но обрежутся при слиянии — без проверки (1) страница молча
        потеряла бы 80 реальных событий.
        ``next_before`` — время последнего (самого старого) события в ответе.
    """
    elevated = await _is_elevated(db, current_user)
    teacher_id = current_user.id

    task_events, help_events, material_events, idle_events = [
        await fn(db, teacher_id=teacher_id, elevated=elevated, limit=limit, before=before)
        for fn in (
            _fetch_task_solved,
            _fetch_help_requested,
            _fetch_material_studied,
            _fetch_student_idle,
        )
    ]

    sources = (task_events, help_events, material_events, idle_events)
    merged = sorted(
        [event for source in sources for event in source],
        key=lambda e: e["timestamp"],
        reverse=True,
    )
    has_more = len(merged) > limit or any(len(source) == limit for source in sources)
    page = merged[:limit]
    next_before = page[-1]["timestamp"] if page else None
    return page, has_more, next_before
