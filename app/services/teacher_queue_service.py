"""
Сервис очередей преподавателя (Learning Engine V1, этап 3.9).

Claim-next и release для help-requests и manual review с атомарным lock/TTL.
Workload-агрегат для главного экрана.
Идемпотентность claim по idempotency_key: in-memory кэш (teacher_id, key, endpoint_type) -> тот же item/token.

Ограничение (P1): кэш локальнен процессу. При нескольких воркерах/репликах повтор с тем же
idempotency_key на другой ноде может выполнить новый claim. Для распределённой идемпотентности
нужен общий store (Redis/БД). См. docs/tz-learning-engine-stage3-9-teacher-next-modes.md.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Кэш идемпотентности claim: ключ (teacher_id, idempotency_key, "help"|"review") -> (item, token, expires_at, cache_until)
_idempotency_cache: dict[tuple, tuple] = {}
_idempotency_lock = asyncio.Lock()
_idempotency_cache_hits = 0
_idempotency_cache_misses = 0
# Negative-cache: при empty кэшируем на короткий TTL; новые кейсы могут не появиться в ответе до истечения TTL
_IDEM_EMPTY_TTL_SEC = 30
_IDEM_SUCCESS_BUFFER_SEC = 60


def _prune_idempotency_cache(now: datetime) -> None:
    """Удалить из кэша записи с cache_until < now. Вызывать только под _idempotency_lock."""
    expired = [k for k, v in _idempotency_cache.items() if v[3] < now]
    for k in expired:
        del _idempotency_cache[k]
    if expired:
        logger.debug("idempotency_cache pruned %d entries, size=%d", len(expired), len(_idempotency_cache))


def get_idempotency_cache_stats() -> dict[str, int]:
    """Наблюдаемость: размер кэша и счётчики hit/miss (для метрик/логов)."""
    return {
        "idempotency_cache_size": len(_idempotency_cache),
        "idempotency_cache_hits": _idempotency_cache_hits,
        "idempotency_cache_misses": _idempotency_cache_misses,
    }

# ACL для заявок (совпадает с help_requests_service)
HELP_REQUESTS_ACL_SQL = """
    (hr.assigned_teacher_id = :teacher_id
     OR EXISTS (SELECT 1 FROM student_teacher_links stl WHERE stl.student_id = hr.student_id AND stl.teacher_id = :teacher_id)
     OR (hr.course_id IS NOT NULL AND EXISTS (SELECT 1 FROM teacher_courses tc WHERE tc.course_id = hr.course_id AND tc.teacher_id = :teacher_id))
     OR EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :teacher_id AND r.name = 'methodist'))
"""

# ACL для pending review: только курсы, где преподаватель в teacher_courses (или methodist)
REVIEW_ACL_SQL = """
    (EXISTS (SELECT 1 FROM teacher_courses tc WHERE tc.course_id = t.course_id AND tc.teacher_id = :teacher_id)
     OR EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :teacher_id AND r.name = 'methodist'))
"""


def _token() -> str:
    return secrets.token_hex(32)


async def claim_next_help_request(
    db: AsyncSession,
    *,
    teacher_id: int,
    request_type: str = "all",
    ttl_sec: int = 120,
    course_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str], Optional[datetime]]:
    """
    Атомарно взять следующий открытый help-request по приоритету/SLA.
    При переданном idempotency_key повторный вызов возвращает тот же (item, token, expires_at).
    Возвращает (item_dict, lock_token, lock_expires_at) или (None, None, None) если пусто.
    """
    global _idempotency_cache_hits, _idempotency_cache_misses
    now = datetime.now(timezone.utc)
    if idempotency_key:
        cache_key = (teacher_id, idempotency_key.strip()[:128], "help")
        async with _idempotency_lock:
            _prune_idempotency_cache(now)
            if cache_key in _idempotency_cache:
                item, token, expires_at, cache_until = _idempotency_cache[cache_key]
                if now < cache_until:
                    _idempotency_cache_hits += 1
                    logger.info(
                        "claim_idempotent_hit queue=help key_prefix=%s cache_size=%d",
                        idempotency_key[:16], len(_idempotency_cache),
                    )
                    return (item, token, expires_at)
                del _idempotency_cache[cache_key]
            _idempotency_cache_misses += 1

    type_cond = ""
    if request_type == "manual_help":
        type_cond = "AND hr.request_type = 'manual_help'"
    elif request_type == "blocked_limit":
        type_cond = "AND hr.request_type = 'blocked_limit'"
    course_cond = "AND hr.course_id = :course_id" if course_id is not None else ""
    params: dict[str, Any] = {"teacher_id": teacher_id}
    if course_id is not None:
        params["course_id"] = course_id

    # Выбрать одну строку с FOR UPDATE SKIP LOCKED: открытые, по ACL, не захваченные или просроченные
    expires_at = now + timedelta(seconds=ttl_sec)
    token = _token()
    params["now_ts"] = now
    params["expires_at"] = expires_at
    params["token"] = token

    r = await db.execute(
        text(f"""
            WITH cand AS (
                SELECT hr.id
                FROM help_requests hr
                WHERE hr.status = 'open'
                  AND (hr.claim_expires_at IS NULL OR hr.claim_expires_at < :now_ts)
                  AND {HELP_REQUESTS_ACL_SQL}
                  {type_cond}
                  {course_cond}
                ORDER BY hr.priority ASC, hr.due_at ASC NULLS LAST, hr.created_at ASC
                LIMIT 1
                FOR UPDATE OF hr SKIP LOCKED
            )
            UPDATE help_requests hr
            SET claimed_by = :teacher_id, claim_token = :token, claim_expires_at = :expires_at
            FROM cand
            WHERE hr.id = cand.id
            RETURNING hr.id, hr.status, hr.request_type, hr.student_id, hr.task_id, hr.course_id,
                      hr.created_at, hr.priority, hr.due_at
        """),
        params,
    )
    row = r.fetchone()
    if row is None:
        if idempotency_key:
            cache_until = now + timedelta(seconds=_IDEM_EMPTY_TTL_SEC)
            async with _idempotency_lock:
                _prune_idempotency_cache(now)
                _idempotency_cache[cache_key] = (None, None, None, cache_until)
        return (None, None, None)

    due_at = row[8] if len(row) > 8 else None
    is_overdue = due_at is not None and due_at < now
    item = {
        "request_id": row[0],
        "status": row[1],
        "request_type": row[2],
        "student_id": row[3],
        "task_id": row[4],
        "course_id": row[5],
        "created_at": row[6],
        "priority": row[7] if len(row) > 7 else 100,
        "due_at": due_at,
        "is_overdue": is_overdue,
    }
    if idempotency_key:
        cache_until = expires_at + timedelta(seconds=_IDEM_SUCCESS_BUFFER_SEC)
        async with _idempotency_lock:
            _prune_idempotency_cache(now)
            _idempotency_cache[cache_key] = (item, token, expires_at, cache_until)
    return (item, token, expires_at)


async def release_help_request_claim(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
    lock_token: str,
) -> Tuple[bool, Optional[str]]:
    """
    Освободить блокировку заявки. Возвращает (released, error).
    error: None | "forbidden" (токен не совпал / кейс у другого) -> 409.
    """
    r = await db.execute(
        text("""
            SELECT id, claimed_by, claim_token, claim_expires_at
            FROM help_requests WHERE id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (False, "not_found")
    _id, claimed_by, current_token, claim_expires_at = row[0], row[1], row[2], row[3]

    now = datetime.now(timezone.utc)
    if claimed_by is None or current_token is None or claim_expires_at is None:
        return (False, None)  # уже свободен — идемпотентно
    if claim_expires_at < now:
        await db.execute(
            text("""
                UPDATE help_requests SET claimed_by = NULL, claim_token = NULL, claim_expires_at = NULL
                WHERE id = :id
            """),
            {"id": request_id},
        )
        return (True, None)  # просрочен — очищаем и считаем released
    if current_token != lock_token or claimed_by != teacher_id:
        return (False, "forbidden")  # 409

    await db.execute(
        text("""
            UPDATE help_requests SET claimed_by = NULL, claim_token = NULL, claim_expires_at = NULL
            WHERE id = :id
        """),
        {"id": request_id},
    )
    return (True, None)


async def claim_next_review(
    db: AsyncSession,
    *,
    teacher_id: int,
    ttl_sec: int = 120,
    course_id: Optional[int] = None,
    user_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str], Optional[datetime]]:
    """
    Атомарно взять следующий результат на ручную проверку (checked_at IS NULL).
    При переданном idempotency_key повторный вызов возвращает тот же (item, token, expires_at).
    ACL по teacher_courses/methodist. Возвращает (item_dict, lock_token, lock_expires_at) или (None, None, None).
    """
    global _idempotency_cache_hits, _idempotency_cache_misses
    now = datetime.now(timezone.utc)
    if idempotency_key:
        cache_key = (teacher_id, idempotency_key.strip()[:128], "review")
        async with _idempotency_lock:
            _prune_idempotency_cache(now)
            if cache_key in _idempotency_cache:
                item, token, expires_at, cache_until = _idempotency_cache[cache_key]
                if now < cache_until:
                    _idempotency_cache_hits += 1
                    logger.info(
                        "claim_idempotent_hit queue=review key_prefix=%s cache_size=%d",
                        idempotency_key[:16], len(_idempotency_cache),
                    )
                    return (item, token, expires_at)
                del _idempotency_cache[cache_key]
            _idempotency_cache_misses += 1

    expires_at = now + timedelta(seconds=ttl_sec)
    token = _token()
    course_cond = "AND t.course_id = :course_id" if course_id is not None else ""
    user_cond = "AND tr.user_id = :user_id" if user_id is not None else ""
    params: dict[str, Any] = {
        "teacher_id": teacher_id,
        "now_ts": now,
        "expires_at": expires_at,
        "token": token,
    }
    if course_id is not None:
        params["course_id"] = course_id
    if user_id is not None:
        params["user_id"] = user_id

    # Выбираем один task_result: checked_at IS NULL, не захвачен или просрочен, по ACL, FIFO
    r = await db.execute(
        text(f"""
            WITH cand AS (
                SELECT tr.id
                FROM task_results tr
                JOIN tasks t ON t.id = tr.task_id
                WHERE tr.checked_at IS NULL
                  AND (tr.review_claim_expires_at IS NULL OR tr.review_claim_expires_at < :now_ts)
                  AND {REVIEW_ACL_SQL}
                  {course_cond}
                  {user_cond}
                ORDER BY tr.submitted_at ASC
                LIMIT 1
                FOR UPDATE OF tr SKIP LOCKED
            )
            UPDATE task_results tr
            SET review_claimed_by = :teacher_id, review_claim_token = :token, review_claim_expires_at = :expires_at
            FROM cand
            WHERE tr.id = cand.id
            RETURNING tr.id, tr.task_id, tr.user_id, tr.score, tr.submitted_at, tr.max_score, tr.is_correct, tr.answer_json
        """),
        params,
    )
    row = r.fetchone()
    if row is None:
        if idempotency_key:
            cache_until = now + timedelta(seconds=_IDEM_EMPTY_TTL_SEC)
            async with _idempotency_lock:
                _prune_idempotency_cache(now)
                _idempotency_cache[cache_key] = (None, None, None, cache_until)
        return (None, None, None)

    result_id, task_id, user_id_val, score, submitted_at, max_score, is_correct, answer_json = row
    # Догрузить task title и user name для ответа
    r2 = await db.execute(
        text("""
            SELECT t.external_uid, t.course_id FROM tasks t WHERE t.id = :task_id
        """),
        {"task_id": task_id},
    )
    trow = r2.fetchone()
    task_title = trow[0] if trow else None
    course_id_val = trow[1] if trow and len(trow) > 1 else None
    r3 = await db.execute(
        text("SELECT full_name FROM users WHERE id = :uid"),
        {"uid": user_id_val},
    )
    urow = r3.fetchone()
    user_name = urow[0] if urow else None

    item = {
        "id": result_id,
        "task_id": task_id,
        "user_id": user_id_val,
        "score": score,
        "submitted_at": submitted_at,
        "max_score": max_score,
        "is_correct": is_correct,
        "answer_json": answer_json,
        "task_title": task_title,
        "user_name": user_name,
        "course_id": course_id_val,
    }
    if idempotency_key:
        cache_until = expires_at + timedelta(seconds=_IDEM_SUCCESS_BUFFER_SEC)
        async with _idempotency_lock:
            _prune_idempotency_cache(now)
            _idempotency_cache[cache_key] = (item, token, expires_at, cache_until)
    return (item, token, expires_at)


async def release_review_claim(
    db: AsyncSession,
    result_id: int,
    teacher_id: int,
    lock_token: str,
) -> Tuple[bool, Optional[str]]:
    """Освободить блокировку проверки. Возвращает (released, error). error: None | 'forbidden' | 'not_found'."""
    r = await db.execute(
        text("""
            SELECT id, review_claimed_by, review_claim_token, review_claim_expires_at
            FROM task_results WHERE id = :result_id
        """),
        {"result_id": result_id},
    )
    row = r.fetchone()
    if row is None:
        return (False, "not_found")
    _id, claimed_by, current_token, claim_expires_at = row[0], row[1], row[2], row[3]

    now = datetime.now(timezone.utc)
    if claimed_by is None or current_token is None or claim_expires_at is None:
        return (False, None)
    if claim_expires_at < now:
        await db.execute(
            text("""
                UPDATE task_results
                SET review_claimed_by = NULL, review_claim_token = NULL, review_claim_expires_at = NULL
                WHERE id = :id
            """),
            {"id": result_id},
        )
        return (True, None)
    if current_token != lock_token or claimed_by != teacher_id:
        return (False, "forbidden")

    await db.execute(
        text("""
            UPDATE task_results
            SET review_claimed_by = NULL, review_claim_token = NULL, review_claim_expires_at = NULL
            WHERE id = :id
        """),
        {"id": result_id},
    )
    return (True, None)


async def get_teacher_workload(
    db: AsyncSession,
    teacher_id: int,
) -> dict[str, int]:
    """
    Агрегат нагрузки: открытые help_requests по типам, pending reviews, просроченные.
    """
    now = datetime.now(timezone.utc)
    params: dict[str, Any] = {"teacher_id": teacher_id, "now_ts": now}

    r = await db.execute(
        text(f"""
            SELECT
                COUNT(*) FILTER (WHERE hr.request_type = 'manual_help') AS manual_help,
                COUNT(*) FILTER (WHERE hr.request_type = 'blocked_limit') AS blocked_limit,
                COUNT(*) FILTER (WHERE hr.due_at IS NOT NULL AND hr.due_at < :now_ts) AS overdue
            FROM help_requests hr
            WHERE hr.status = 'open' AND {HELP_REQUESTS_ACL_SQL}
        """),
        params,
    )
    row = r.fetchone()
    open_manual_help = int(row[0] or 0)
    open_blocked_limit = int(row[1] or 0)
    overdue_total = int(row[2] or 0)
    open_help_requests_total = open_manual_help + open_blocked_limit

    r2 = await db.execute(
        text(f"""
            SELECT COUNT(*)
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.checked_at IS NULL AND {REVIEW_ACL_SQL}
        """),
        params,
    )
    pending_manual_reviews_total = int(r2.scalar() or 0)

    return {
        "open_help_requests_total": open_help_requests_total,
        "open_blocked_limit_total": open_blocked_limit,
        "open_manual_help_total": open_manual_help,
        "pending_manual_reviews_total": pending_manual_reviews_total,
        "overdue_total": overdue_total,
    }
