"""Поиск задания в кабинете преподавателя по номеру или тексту (tsk-353).

На живом уроке ученик называет номер задания или пересказывает условие —
преподавателю нужно быстро найти его, не листая дерево прогресса. Поиск ведётся
**в контексте открытого ученика** (переиспользует ACL из tsk-297/tsk-349), а
результат — краткая карточка, ведущая в уже существующую детальную карточку
(``task_history_service.build_task_history``, эндпоинт tsk-349).

Два режима одного параметра ``q``:

* число или ``id-<N>`` (см. видимый номер задания, tsk-309/311) — точный поиск
  по ``tasks.id``;
* иначе — полнотекстовый ``ILIKE`` по условию/заголовку задания
  (``task_content->>'stem'``/``'title'``), кандидатный пул + ACL-фильтр в Python,
  без ``pg_trgm`` (по объёму задачи достаточно).

ACL — ``manual_progress_service.can_edit_progress`` (тот же гейт, что у правки
прогресса и у истории задания): teacher видит только заданиями учеников,
закреплённых за ним, либо курсов под его ACL, при условии что ученик реально
достиг этого узла (``list_active_roots_of_node`` внутри). **Задания с
``course_id IS NULL`` (легаси, tsk-349 follow-up) исключены всегда** — по ним
эндпоинт истории отдаёт 404, и попадание такого задания в выдачу вело бы клик
в тупик.

Read-only: ни одной записи в БД.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.services import manual_progress_service
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

#: Обрезка снипета в результате поиска — длиннее обычного заголовка (80), чтобы
#: преподаватель успел опознать задание по началу условия, но не весь текст.
_SNIPPET_MAX_LEN = 200

#: Минимальная длина текстового запроса — короче двух символов ILIKE по всему
#: массиву заданий даёт бесполезный полный скан без пользы для поиска.
_MIN_TEXT_QUERY_LEN = 2

#: Кандидатный пул полнотекстового поиска ДО ACL-фильтра. С запасом сверх
#: итогового лимита — часть кандидатов ACL отсеет.
_CANDIDATE_POOL = 200

#: "110" или "id-110" / "ID-110" — видимый номер задания (tsk-309/311).
_NUMBER_RE = re.compile(r"^id-(\d+)$", re.IGNORECASE)

_TASK_ROW_SQL = (
    "SELECT t.id, t.course_id, t.external_uid, "
    "       t.task_content->>'type' AS task_type, "
    "       t.task_content->>'title' AS tc_title, "
    "       t.task_content->>'stem' AS tc_stem, "
    "       c.title AS course_title, d.name_ru AS difficulty_name "
    "FROM tasks t "
    "LEFT JOIN courses c ON c.id = t.course_id "
    "LEFT JOIN difficulties d ON d.id = t.difficulty_id "
)


def _parse_task_number(query: str) -> Optional[int]:
    """"110" / "id-110" / "ID-110" -> 110. Иначе None (текстовый режим)."""
    stripped = query.strip()
    if stripped.isdigit():
        return int(stripped)
    match = _NUMBER_RE.match(stripped)
    return int(match.group(1)) if match else None


def _escape_ilike(raw: str) -> str:
    """Экранировать `\\`, `%`, `_` — иначе буквальные % / _ в запросе учителя
    сработали бы как wildcard ILIKE, а не как искомый текст."""
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_result(row: Dict[str, Any]) -> Dict[str, Any]:
    task_id = int(row["id"])
    return {
        "task_id": task_id,
        "visible_id": f"id-{task_id}",
        "title": humanize_task_title(
            task_id, row.get("tc_title"), row.get("tc_stem"), row.get("external_uid"),
            max_len=_SNIPPET_MAX_LEN,
        ),
        "task_type": row.get("task_type"),
        "course_id": int(row["course_id"]),
        "course_title": row.get("course_title"),
        "difficulty": row.get("difficulty_name"),
    }


async def _load_task_by_id(db: AsyncSession, task_id: int) -> Optional[Dict[str, Any]]:
    row = (
        await db.execute(
            text(_TASK_ROW_SQL + "WHERE t.id = :task_id AND t.is_active = true"),
            {"task_id": task_id},
        )
    ).mappings().fetchone()
    return dict(row) if row is not None else None


async def _search_by_text(
    db: AsyncSession, query_text: str, *, candidate_limit: int
) -> List[Dict[str, Any]]:
    pattern = f"%{_escape_ilike(query_text)}%"
    rows = (
        await db.execute(
            text(
                _TASK_ROW_SQL
                + "WHERE t.is_active = true AND t.course_id IS NOT NULL "
                "  AND (t.task_content->>'stem' ILIKE :pattern ESCAPE '\\' "
                "       OR t.task_content->>'title' ILIKE :pattern ESCAPE '\\') "
                "ORDER BY t.id "
                "LIMIT :candidate_limit"
            ),
            {"pattern": pattern, "candidate_limit": candidate_limit},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


async def search_tasks_for_teacher(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    student_id: int,
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Найти задания, доступные преподавателю в контексте ученика.

    :param query: номер (``110``/``id-110``) или текст условия.
    :param limit: максимум результатов после ACL-фильтра.
    :returns: список словарей, готовых для ``TaskSearchResult(**row)``.
    """
    task_id = _parse_task_number(query)

    if task_id is not None:
        row = await _load_task_by_id(db, task_id)
        # course_id=NULL (легаси, tsk-349 follow-up) — эндпоинт истории даст 404,
        # находить такое задание поиском незачем.
        if row is None or row["course_id"] is None:
            return []
        if not await manual_progress_service.can_edit_progress(
            db, current_user, student_id, int(row["course_id"])
        ):
            return []
        return [_to_result(row)]

    stripped = query.strip()
    if len(stripped) < _MIN_TEXT_QUERY_LEN:
        return []

    candidates = await _search_by_text(db, stripped, candidate_limit=_CANDIDATE_POOL)
    if len(candidates) >= _CANDIDATE_POOL:
        logger.debug(
            "tsk-353: полнотекстовый поиск задания достиг кандидатного пула (%s) "
            "до ACL-фильтра — часть совпадений могла быть не показана",
            _CANDIDATE_POOL,
        )

    # ACL проверяется на уровне course_id, а не задания — кэш на запрос, чтобы
    # не дёргать can_edit_progress повторно для заданий одного курса.
    acl_cache: Dict[int, bool] = {}
    results: List[Dict[str, Any]] = []
    for row in candidates:
        course_id = row.get("course_id")
        if course_id is None:
            continue
        course_id = int(course_id)
        if course_id not in acl_cache:
            acl_cache[course_id] = await manual_progress_service.can_edit_progress(
                db, current_user, student_id, course_id
            )
        if acl_cache[course_id]:
            results.append(_to_result(row))
            if len(results) >= limit:
                break
    return results


__all__ = ["search_tasks_for_teacher"]
