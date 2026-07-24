"""История выполнения задания по паре (ученик, задание) — сборка для карточки (tsk-349).

Единый агрегат «всё по одному заданию у одного ученика», собранный из готовых
кирпичей учебного движка одним набором батч-запросов (без N+1 по попыткам):

* попытки — строки ``task_results`` неотменённых попыток (score/статус/ответ
  ученика/комментарий преподавателя/время проверки);
* заявки помощи по этому заданию + диалог ответов (``help_requests`` +
  ``help_request_replies``);
* подсказки — счётчики из ``learning_events`` (``get_hint_open_counts``);
* правило проверки / эталон (``solution_rules``) — ТОЛЬКО когда
  ``include_solution=True`` (преподавательский путь). Ученический путь блок не
  собирает вовсе: разграничение видимости структурное, а не фильтрацией на выходе.

ACL здесь НЕ проверяется — это делает вызывающий эндпоинт (учитель — ACL портала
``ensure_can_edit_progress``; ученик — только своя история, ``current_user.id``).

Замечание по именованию колонок: в ``task_results``/``attempts`` ученик — это
``user_id``; в ``learning_events`` — ``student_id`` (учтено в
``get_hint_open_counts``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.checking_service import CheckingService
from app.services.learning_events_service import get_hint_open_counts

logger = logging.getLogger(__name__)

#: Провенанс ручного зачёта преподавателем (синтетическая попытка, tsk-297).
_MANUAL_SOURCE = "manual_teacher"

_checking = CheckingService()


async def course_of_task(db: AsyncSession, task_id: int) -> Optional[int]:
    """Курс задания (для scoped-ACL до сборки данных). None — задания нет."""
    row = (
        await db.execute(
            text("SELECT course_id FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        )
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else None


def _attempt_status(is_correct: Optional[bool]) -> str:
    """Статус строки результата: passed | failed | pending_review."""
    if is_correct is None:
        return "pending_review"
    return "passed" if is_correct else "failed"


def _table_columns(task_content: Any) -> Optional[int]:
    """Число столбцов табличного ответа (TBL_COM) из task_content.table.columns."""
    if not isinstance(task_content, dict):
        return None
    table = task_content.get("table")
    if not isinstance(table, dict):
        return None
    cols = table.get("columns")
    return int(cols) if isinstance(cols, int) else None


async def _load_task_meta(db: AsyncSession, task_id: int) -> Optional[Dict[str, Any]]:
    """Мета задания + курс + сырые solution_rules (для ветки эталона). None — нет задания."""
    row = (
        await db.execute(
            text(
                "SELECT t.id, t.external_uid, t.course_id, t.max_score, "
                "       t.task_content, t.solution_rules, c.title AS course_title "
                "FROM tasks t "
                "LEFT JOIN courses c ON c.id = t.course_id "
                "WHERE t.id = :task_id"
            ),
            {"task_id": task_id},
        )
    ).mappings().fetchone()
    return dict(row) if row is not None else None


async def _load_attempts(
    db: AsyncSession, *, user_id: int, task_id: int
) -> List[Dict[str, Any]]:
    """Попытки ученика по заданию (неотменённые), в хронологическом порядке.

    Один запрос независимо от числа попыток. ``attempt_no`` — 1-based порядковый
    номер по времени сдачи. ``manual`` — синтетическая попытка ручного зачёта.
    """
    rows = (
        await db.execute(
            text(
                "SELECT tr.id AS task_result_id, tr.attempt_id, tr.submitted_at, "
                "       tr.score, COALESCE(tr.max_score, 0) AS max_score, "
                "       tr.is_correct, tr.answer_json, tr.metrics->>'comment' AS comment, "
                "       tr.checked_at, tr.source_system "
                "FROM task_results tr "
                "JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                "WHERE tr.user_id = :user_id AND tr.task_id = :task_id "
                "ORDER BY tr.submitted_at ASC, tr.id ASC"
            ),
            {"user_id": user_id, "task_id": task_id},
        )
    ).mappings().fetchall()

    attempts: List[Dict[str, Any]] = []
    for idx, r in enumerate(rows, start=1):
        attempts.append(
            {
                "task_result_id": int(r["task_result_id"]),
                "attempt_id": int(r["attempt_id"]) if r["attempt_id"] is not None else None,
                "attempt_no": idx,
                "submitted_at": r["submitted_at"],
                "score": int(r["score"] or 0),
                "max_score": int(r["max_score"] or 0),
                "is_correct": r["is_correct"],
                "status": _attempt_status(r["is_correct"]),
                "answer_json": r["answer_json"],
                "comment": r["comment"],
                "checked_at": r["checked_at"],
                "manual": (r["source_system"] == _MANUAL_SOURCE),
            }
        )
    return attempts


async def _load_help_requests(
    db: AsyncSession, *, user_id: int, task_id: int
) -> List[Dict[str, Any]]:
    """Заявки помощи ученика по заданию + диалог ответов.

    Два запроса на всё (заявки + все реплаи по IN), без N+1 на заявку.
    """
    hr_rows = (
        await db.execute(
            text(
                "SELECT id AS request_id, status, request_type, message, "
                "       created_at, closed_at, resolution_comment "
                "FROM help_requests "
                "WHERE student_id = :user_id AND task_id = :task_id "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"user_id": user_id, "task_id": task_id},
        )
    ).mappings().fetchall()

    requests: List[Dict[str, Any]] = [dict(r) for r in hr_rows]
    if not requests:
        return []

    request_ids = [int(r["request_id"]) for r in requests]
    reply_rows = (
        await db.execute(
            text(
                "SELECT request_id, id AS reply_id, teacher_id, body, "
                "       close_after_reply, created_at "
                "FROM help_request_replies "
                "WHERE request_id = ANY(:request_ids) "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"request_ids": request_ids},
        )
    ).mappings().fetchall()

    replies_by_request: Dict[int, List[Dict[str, Any]]] = {}
    for rr in reply_rows:
        replies_by_request.setdefault(int(rr["request_id"]), []).append(
            {
                "reply_id": int(rr["reply_id"]),
                "teacher_id": int(rr["teacher_id"]),
                "body": rr["body"],
                "close_after_reply": bool(rr["close_after_reply"]),
                "created_at": rr["created_at"],
            }
        )

    for req in requests:
        req["replies"] = replies_by_request.get(int(req["request_id"]), [])
    return requests


def _build_solution(task_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Собрать блок правила проверки/эталона (ТОЛЬКО для преподавателя).

    Использует безопасный парс ``build_solution_rules`` (JSON null → деградированный
    объект без эталона), поэтому не падает на заданиях без заведённого правила.
    """
    rules = _checking.build_solution_rules(
        task_meta.get("solution_rules"), task_meta.get("max_score")
    )
    short = rules.short_answer
    accepted = (
        [{"value": a.value, "score": a.score} for a in short.accepted_answers]
        if short is not None
        else []
    )
    task_content = task_meta.get("task_content")
    task_type = (
        task_content.get("type") if isinstance(task_content, dict) else None
    )
    return {
        "type": task_type,
        "max_score": rules.max_score,
        "scoring_mode": rules.scoring_mode,
        "auto_check": rules.auto_check,
        "manual_review_required": rules.manual_review_required,
        "requires_attachment": rules.requires_attachment,
        "accepted_answers": accepted,
        "correct_option_ids": list(rules.correct_options),
        "row_order_matters": rules.table.row_order_matters if rules.table is not None else None,
        "normalization": list(short.normalization) if short is not None else [],
        "use_regex": short.use_regex if short is not None else False,
        "regex": short.regex if short is not None else None,
    }


async def build_task_history(
    db: AsyncSession,
    *,
    user_id: int,
    task_id: int,
    include_solution: bool,
) -> Optional[Dict[str, Any]]:
    """Собрать историю по паре (ученик, задание). None — задания нет (404 у вызова).

    :param include_solution: True (преподаватель) — добавить блок ``solution``
        (правило проверки/эталон). False (ученик) — блок не собирается вовсе.
    """
    task_meta = await _load_task_meta(db, task_id)
    if task_meta is None:
        return None

    attempts = await _load_attempts(db, user_id=user_id, task_id=task_id)
    help_requests = await _load_help_requests(db, user_id=user_id, task_id=task_id)
    hints_total, hints_text, hints_video = await get_hint_open_counts(
        db, user_id=user_id, task_id=task_id
    )

    task_content = task_meta.get("task_content")
    task_type = task_content.get("type") if isinstance(task_content, dict) else None
    title = task_content.get("title") if isinstance(task_content, dict) else None
    stem = task_content.get("stem") if isinstance(task_content, dict) else None

    result: Dict[str, Any] = {
        "user_id": user_id,
        "task": {
            "task_id": int(task_meta["id"]),
            "external_uid": task_meta.get("external_uid"),
            "type": task_type,
            "title": title,
            "stem": stem,
            "max_score": task_meta.get("max_score"),
            "course_id": task_meta.get("course_id"),
            "course_title": task_meta.get("course_title"),
            "table_columns": _table_columns(task_content),
        },
        "attempts": attempts,
        "help_requests": help_requests,
        "hints": {"total": hints_total, "text": hints_text, "video": hints_video},
        "solution": _build_solution(task_meta) if include_solution else None,
    }
    return result
