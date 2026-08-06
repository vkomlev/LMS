"""Сервис обращений о проблемах и идеях (tsk-303, Поток B).

Второй поток единого инбокса. Минимальный набор операций: создать, показать
список, закрыть. Ничего сверх этого задача не просит, а лишние состояния
(«в работе», приоритеты, назначение исполнителя) без реального процесса вокруг
них превратились бы в поля, которые никто не заполняет.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

REPORT_TYPES: tuple[str, ...] = ("bug", "content", "feature_idea")
BODY_MAX_LEN = 4000


async def create_report(
    db: AsyncSession,
    *,
    author_id: int,
    report_type: str,
    body: str,
    course_id: Optional[int] = None,
    material_id: Optional[int] = None,
    task_id: Optional[int] = None,
) -> dict[str, Any]:
    """Создать обращение. Валидацию типа и непустого текста делает слой API."""
    row = (
        await db.execute(
            text("""
                INSERT INTO feedback_reports
                    (report_type, status, author_id, body, course_id, material_id, task_id,
                     created_at, updated_at)
                VALUES (:rt, 'open', :author, :body, :course_id, :material_id, :task_id,
                        now(), now())
                RETURNING id, created_at
            """),
            {
                "rt": report_type,
                "author": author_id,
                "body": body[:BODY_MAX_LEN],
                "course_id": course_id,
                "material_id": material_id,
                "task_id": task_id,
            },
        )
    ).fetchone()
    logger.info(
        "tsk-303 feedback: создано обращение %s (тип %s, автор %s)", row[0], report_type, author_id
    )
    return {"report_id": int(row[0]), "status": "open", "created_at": row[1]}


async def list_reports(
    db: AsyncSession,
    *,
    author_id: Optional[int] = None,
    status_filter: str = "open",
    report_type: str = "all",
    limit: int = 20,
    offset: int = 0,
) -> Tuple[list[dict[str, Any]], int]:
    """Список обращений.

    `author_id` сужает выборку до собственных обращений — этим же параметром
    ограничивается преподаватель, которому чужие обращения не показываются.
    Методист и админ зовут без него и видят всё.
    """
    conds: list[str] = ["1=1"]
    params: dict[str, Any] = {}
    if author_id is not None:
        conds.append("fr.author_id = :author_id")
        params["author_id"] = author_id
    if status_filter in ("open", "closed"):
        conds.append("fr.status = :status")
        params["status"] = status_filter
    if report_type in REPORT_TYPES:
        conds.append("fr.report_type = :rt")
        params["rt"] = report_type
    where_sql = " AND ".join(conds)

    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM feedback_reports fr WHERE {where_sql}"),  # nosec B608 — where_sql из литералов, значения через bind
                params,
            )
        ).scalar()
        or 0
    )

    rows = (
        await db.execute(
            text(f"""
                SELECT fr.id, fr.report_type, fr.status, fr.author_id, fr.body,
                       fr.course_id, fr.material_id, fr.task_id,
                       fr.created_at, fr.updated_at, fr.closed_at, fr.closed_by,
                       fr.resolution_comment,
                       u.full_name AS author_name,
                       c.title AS course_title
                FROM feedback_reports fr
                LEFT JOIN users u ON u.id = fr.author_id
                LEFT JOIN courses c ON c.id = fr.course_id
                WHERE {where_sql}
                ORDER BY fr.created_at DESC, fr.id DESC
                LIMIT :limit OFFSET :offset
            """),  # nosec B608 — where_sql из литералов, значения через bind
            {**params, "limit": limit, "offset": offset},
        )
    ).fetchall()

    items = [
        {
            "report_id": int(r[0]),
            "report_type": r[1],
            "status": r[2],
            "author_id": r[3],
            "body": r[4],
            "course_id": r[5],
            "material_id": r[6],
            "task_id": r[7],
            "created_at": r[8],
            "updated_at": r[9],
            "closed_at": r[10],
            "closed_by": r[11],
            "resolution_comment": r[12],
            "author_name": r[13],
            "course_title": r[14],
        }
        for r in rows
    ]
    return (items, total)


async def close_report(
    db: AsyncSession,
    report_id: int,
    closed_by: int,
    resolution_comment: Optional[str] = None,
    *,
    is_privileged: bool,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """Закрыть обращение.

    Закрывает автор (передумал, сам разобрался) либо методист/админ (разобрал).
    Повторное закрытие идемпотентно: кнопку жмут дважды, и вторая попытка не
    повод для ошибки.

    Возвращает (data, error). error: None | "not_found" | "forbidden".
    """
    row = (
        await db.execute(
            text(
                "SELECT author_id, status, closed_at FROM feedback_reports WHERE id = :id"
            ),
            {"id": report_id},
        )
    ).fetchone()
    if row is None:
        return (None, "not_found")
    author_id, status_val, closed_at = row
    if not is_privileged and author_id != closed_by:
        return (None, "forbidden")
    if status_val == "closed":
        return (
            {"report_id": report_id, "status": "closed", "closed_at": closed_at,
             "already_closed": True},
            None,
        )

    now = datetime.now(timezone.utc)
    await db.execute(
        text("""
            UPDATE feedback_reports
            SET status = 'closed', closed_at = :now, closed_by = :by,
                resolution_comment = :comment, updated_at = :now
            WHERE id = :id
        """),
        {
            "id": report_id,
            "now": now,
            "by": closed_by,
            "comment": (resolution_comment or "")[:2000] or None,
        },
    )
    return (
        {"report_id": report_id, "status": "closed", "closed_at": now, "already_closed": False},
        None,
    )
