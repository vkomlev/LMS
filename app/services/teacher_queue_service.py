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

# ─── Y-4.1: иерархический ACL teacher_courses через course_parents ──────────
# Teacher, привязанный к корневому курсу (DB-триггер требует root), автоматически
# видит сущности (review, help-requests) для всех потомков в `course_parents`.
# Глубина дерева = 2 (verified MCP 2026-04-30); WITH RECURSIVE безопасен без
# cycle protection — `course_parents` спроектирована как DAG (см. M2 + триггер
# 20260127). Backward-compat: для root-курса ancestor_chain={root} → поведение
# идентично прежнему точному равенству.

TEACHER_COURSE_HIERARCHY_ACL_TEMPLATE = """
    EXISTS (
        WITH RECURSIVE ancestor_chain AS (
            SELECT ({target_course_col})::integer AS course_id
            UNION ALL
            SELECT cp.parent_course_id
            FROM course_parents cp
            JOIN ancestor_chain a ON a.course_id = cp.course_id
        )
        SELECT 1 FROM teacher_courses tc
        WHERE tc.teacher_id = :teacher_id
          AND tc.course_id IN (SELECT course_id FROM ancestor_chain)
    )
"""


def teacher_course_acl(target_course_col: str) -> str:
    """Вернуть SQL-фрагмент `EXISTS(...)` для проверки доступа :teacher_id к
    target_course_col через дерево course_parents.

    target_course_col — column reference из закрытого набора call-sites (например
    't.course_id', 'hr.course_id'). User-input не попадает в format() — все
    динамические значения идут через bind (:teacher_id).
    """
    return TEACHER_COURSE_HIERARCHY_ACL_TEMPLATE.format(
        target_course_col=target_course_col
    )  # nosec B608 — target_course_col из закрытого набора литералов модуля


# ACL для заявок (совпадает с help_requests_service). Y-4.1: hierarchical через
# teacher_course_acl(); methodist-bypass сохранён как escape hatch.
# nosec B608 — единственная f-string подстановка идёт от teacher_course_acl(),
# который сам подставляет литералы из закрытого набора (см. helper выше).
HELP_REQUESTS_ACL_SQL = f"""
    (hr.assigned_teacher_id = :teacher_id
     OR EXISTS (SELECT 1 FROM student_teacher_links stl WHERE stl.student_id = hr.student_id AND stl.teacher_id = :teacher_id)
     OR (hr.course_id IS NOT NULL AND {teacher_course_acl('hr.course_id')})
     OR EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :teacher_id AND r.name = 'methodist'))
"""  # nosec B608

# ACL для pending review (Y-4.1: hierarchical через teacher_course_acl()).
# nosec B608 — то же обоснование, что и выше.
REVIEW_ACL_SQL = f"""
    ({teacher_course_acl('t.course_id')}
     OR EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :teacher_id AND r.name = 'methodist'))
"""  # nosec B608


# ─── tsk-247: единый предикат «обязательной проверки» ───────────────────────
# ЕДИНСТВЕННОЕ определение обязательной очереди. Его обязаны использовать и
# claim-next, и список `GET /task-results/by-pending-review?review_kind=mandatory`.
# До tsk-247 условия разъезжались: список требовал `is_correct IS NULL` (Y-4.2),
# claim-next — `is_correct IS TRUE` (Y-6/tsk-210). Множества не пересекались:
# работу из обязательного списка невозможно было взять в работу, и наоборот.
#
# Ось «обязательная / опциональная» — это `manual_review_required` (SA_COM) и
# тип TA, а НЕ `is_correct`:
#   - TA — всегда ручная (рубрики), submit ставит optimistic-PASSED is_correct=TRUE;
#   - SA_COM с manual_review_required=true — авто-чек намеренно не выносит вердикт
#     (is_correct=NULL, см. tsk-230 `_check_short_answer`);
#   - SA_COM с manual_review_required=false — авто-проверена, очередь опциональная
#     (review_kind=optional). Сюда же попадают честно-заваленные (is_correct=false),
#     которые tsk-210 не хотел показывать как обязательные — теперь их отсекает
#     сама ось mrr, без хрупкой опоры на is_correct.
MANDATORY_REVIEW_TEMPLATE = """
    ({tasks}.task_content->>'type' = 'TA'
     OR ({tasks}.task_content->>'type' = 'SA_COM'
         AND COALESCE(({tasks}.solution_rules->>'manual_review_required')::boolean, false) IS TRUE))
"""


def mandatory_review_sql(tasks_alias: str = "t") -> str:
    """SQL-фрагмент «работа требует обязательной ручной проверки».

    :param tasks_alias: алиас таблицы `tasks` в вызывающем запросе ('t' в
        claim-next, 'tasks' в SQLAlchemy-select списка). User-input сюда не
        попадает — только литералы из закрытого набора call-sites.
    """
    return MANDATORY_REVIEW_TEMPLATE.format(tasks=tasks_alias)  # nosec B608


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

    # Выбираем один task_result для ручной проверки. tsk-247: очередь
    # определяется общим предикатом mandatory_review_sql() — тем же, что и у
    # списка `by-pending-review?review_kind=mandatory`. Опора на `is_correct`
    # (Y-4.2 `IS NULL` / Y-6 `IS TRUE`) убрана: она разъезжалась между двумя
    # местами и делала очередь недостижимой (см. комментарий у предиката).
    r = await db.execute(
        text(f"""
            WITH cand AS (
                SELECT tr.id
                FROM task_results tr
                JOIN tasks t ON t.id = tr.task_id
                WHERE tr.checked_at IS NULL
                  AND {mandatory_review_sql('t')}
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
            RETURNING tr.id, tr.task_id, tr.user_id, tr.score, tr.submitted_at, tr.max_score, tr.is_correct, tr.answer_json, tr.attempt_id
        """),  # nosec B608 — REVIEW_ACL_SQL/course_cond/user_cond из закрытого набора литералов
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

    result_id, task_id, user_id_val, score, submitted_at, max_score, is_correct, answer_json, attempt_id_val = row
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
        "attempt_id": attempt_id_val,
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


class GradeError(Exception):
    """Базовая ошибка grade_review (Phase Y-4)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GradeNotFoundError(GradeError):
    """task_results не существует."""

    def __init__(self) -> None:
        super().__init__("not_found", "task_result не найден")


class GradeConflictError(GradeError):
    """409: lock_token mismatch / expired / already graded."""

    def __init__(self, reason: str) -> None:
        super().__init__("conflict", reason)


class GradeValidationError(GradeError):
    """422: business validation failure (score > max_score)."""

    def __init__(self, reason: str) -> None:
        super().__init__("validation", reason)


class ClaimForbiddenError(GradeError):
    """403: работа вне зоны ответственности преподавателя (ACL)."""

    def __init__(self) -> None:
        super().__init__("forbidden", "Работа вне вашей зоны ответственности")


async def claim_review_by_id(
    db: AsyncSession,
    *,
    result_id: int,
    teacher_id: int,
    ttl_sec: int = 120,
) -> Tuple[dict, str, datetime]:
    """Захватить КОНКРЕТНУЮ работу под оценку (tsk-247).

    Дополняет `claim_next_review` (тот выдаёт следующую из обязательной очереди).
    Нужен для оценки опциональных работ (авто-проверенные SA_COM,
    `manual_review_required=false`), которые преподаватель открывает из списка
    вручную: `grade_review` требует валидный lock_token, а взять его было негде.

    Проверки (те же инварианты, что у claim-next): работа существует, не
    оценена, тип SA_COM/TA, ACL преподавателя, замок свободен или уже наш.

    :returns: (item, lock_token, lock_expires_at)
    :raises GradeNotFoundError: работы нет / тип не подлежит ручной оценке.
    :raises ClaimForbiddenError: работа вне ACL преподавателя.
    :raises GradeConflictError: уже оценена или захвачена другим преподавателем.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_sec)
    token = _token()

    # Диагностика до записи: разводим 404 / 403 / 409 явными причинами —
    # иначе бот покажет «конфликт» там, где на деле нет доступа.
    r = await db.execute(
        text(f"""
            SELECT tr.checked_at, tr.review_claimed_by, tr.review_claim_expires_at,
                   t.task_content->>'type' AS task_type,
                   {REVIEW_ACL_SQL} AS acl_ok
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.id = :result_id
        """),  # nosec B608 — REVIEW_ACL_SQL собран из литералов модуля
        {"result_id": result_id, "teacher_id": teacher_id},
    )
    row = r.fetchone()
    if row is None:
        raise GradeNotFoundError()
    checked_at, claimed_by, claim_expires_at, task_type, acl_ok = row
    if task_type not in ("SA_COM", "TA"):
        raise GradeNotFoundError()
    if not acl_ok:
        raise ClaimForbiddenError()
    if checked_at is not None:
        raise GradeConflictError("Заявка уже оценена")

    # Атомарный захват: условие в WHERE повторяет проверки выше — между SELECT
    # и UPDATE работу мог забрать другой преподаватель.
    r2 = await db.execute(
        text("""
            UPDATE task_results tr
            SET review_claimed_by = :teacher_id, review_claim_token = :token,
                review_claim_expires_at = :expires_at
            FROM tasks t
            WHERE t.id = tr.task_id
              AND tr.id = :result_id
              AND tr.checked_at IS NULL
              AND (tr.review_claim_expires_at IS NULL
                   OR tr.review_claim_expires_at < :now_ts
                   OR tr.review_claimed_by = :teacher_id)
            RETURNING tr.id, tr.task_id, tr.user_id, tr.score, tr.submitted_at,
                      tr.max_score, tr.is_correct, tr.answer_json, t.external_uid, t.course_id,
                      tr.attempt_id
        """),
        {
            "result_id": result_id,
            "teacher_id": teacher_id,
            "token": token,
            "expires_at": expires_at,
            "now_ts": now,
        },
    )
    urow = r2.fetchone()
    if urow is None:
        logger.info(
            "claim_by_id conflict result_id=%s teacher_id=%s claimed_by=%s expires=%s",
            result_id, teacher_id, claimed_by, claim_expires_at,
        )
        raise GradeConflictError("Работу уже проверяет другой преподаватель")

    (
        rid, task_id, user_id_val, score, submitted_at,
        max_score, is_correct, answer_json, task_title, course_id_val, attempt_id_val,
    ) = urow
    r3 = await db.execute(
        text("SELECT full_name FROM users WHERE id = :uid"), {"uid": user_id_val}
    )
    urow2 = r3.fetchone()
    item = {
        "id": rid,
        "task_id": task_id,
        "user_id": user_id_val,
        "score": score,
        "submitted_at": submitted_at,
        "max_score": max_score,
        "is_correct": is_correct,
        "answer_json": answer_json,
        "task_title": task_title,
        "user_name": urow2[0] if urow2 else None,
        "course_id": course_id_val,
        "attempt_id": attempt_id_val,
    }
    return (item, token, expires_at)


async def grade_review(
    db: AsyncSession,
    *,
    result_id: int,
    teacher_id: int,
    lock_token: str,
    score: int,
    comment: Optional[str],
) -> dict:
    """Атомарно оценить task_result (Phase Y-4 → Y-6 derived).

    Шаги в одной транзакции (caller commit'ит):
    1. SELECT FROM task_results WHERE id=:rid FOR UPDATE
    2. Validate: not_found → 404; lock mismatch / expired → 409;
       checked_at IS NOT NULL → 409 «уже оценено»;
       score > max_score → 422.
    3. Compute derived: `is_correct = score/max_score >= REVIEW_PASS_THRESHOLD_RATIO`.
    4. UPDATE task_results: is_correct, score, checked_at, checked_by,
       metrics.comment, обнулить review_claim_*.

    Y-6 (2026-05-04): teacher больше не передаёт `is_correct` явно —
    он передаёт только `score` (и опц. `comment`). is_correct выводится
    server-side через REVIEW_PASS_THRESHOLD_RATIO. Idempotency check
    переключён с `is_correct IS NOT NULL` на `checked_at IS NOT NULL`,
    т.к. после Stage 1 optimistic-PASSED `is_correct` уже не NULL для
    SA_COM/TA даже до grade.

    Возвращает dict с полями для caller'а: result_id, task_id, user_id,
    user_email, score, max_score, is_correct, comment, task_title.
    Caller отвечает за inbox INSERT, audit, email scheduling, commit.
    """
    from app.core.config import Settings as _SettingsCls
    _settings = _SettingsCls()

    now = datetime.now(timezone.utc)

    # SELECT FOR UPDATE — сериализует двух teacher'ов
    r = await db.execute(
        text(
            "SELECT tr.id, tr.task_id, tr.user_id, tr.score, tr.max_score, "
            "       tr.is_correct, tr.checked_at, tr.review_claimed_by, "
            "       tr.review_claim_token, tr.review_claim_expires_at, "
            "       tr.attempt_id, tr.metrics "
            "FROM task_results tr WHERE tr.id = :rid FOR UPDATE"
        ),
        {"rid": result_id},
    )
    row = r.fetchone()
    if row is None:
        raise GradeNotFoundError()

    (
        _id, task_id, user_id, _score, max_score,
        _existing_is_correct, existing_checked_at,
        claimed_by, claim_token, claim_expires_at,
        attempt_id, metrics_existing,
    ) = row

    # Y-6: idempotency check по `checked_at IS NOT NULL`
    # (был `is_correct IS NOT NULL` в Y-4; Stage 1 optimistic-PASSED
    # делает is_correct предсказуемо TRUE на submit, поэтому он больше
    # не маркер «оценено»). Регрейд после grade — отдельный endpoint
    # POST /regrade (Stage 3).
    if existing_checked_at is not None:
        raise GradeConflictError("Заявка уже оценена")

    # lock_token mismatch / not claimed
    if claimed_by is None or claim_token is None or claim_expires_at is None:
        raise GradeConflictError(
            "Заявка не захвачена; вызовите claim-next перед grade"
        )
    if claim_expires_at < now:
        raise GradeConflictError(
            "lock_token истёк; вызовите claim-next повторно"
        )
    # Constant-time compare для lock_token (defence-in-depth, см. tech-lead m7)
    if not secrets.compare_digest(claim_token, lock_token) or claimed_by != teacher_id:
        raise GradeConflictError(
            "lock_token не совпадает или заявка захвачена другим преподавателем"
        )

    # Business validation. max_score обязателен — fallback скрыл бы data-quality
    # bug (см. tech-lead m3 review): если задача создана без max_score, оценка
    # не имеет смысла.
    effective_max = int(max_score) if max_score is not None else 0
    if effective_max <= 0:
        raise GradeValidationError(
            "max_score не задан для задачи; оценка невозможна без эталона"
        )
    if score > effective_max:
        raise GradeValidationError(
            f"score={score} превышает max_score={effective_max}"
        )

    # Y-6 derived: is_correct из ratio (configurable, default 0.2).
    # Trace: score=1, max=15 → 0.067 < 0.2 → False
    #        score=3, max=15 → 0.200 >= 0.2 → True
    #        score=15, max=15 → 1.0 >= 0.2 → True
    pass_ratio = float(_settings.review_pass_threshold_ratio)
    is_correct: bool = (float(score) / float(effective_max)) >= pass_ratio

    # Обновляем metrics.comment поверх существующих metrics (jsonb_set безопасен
    # для NULL через coalesce). comment может быть NULL — храним как JSON null.
    # Защита от legacy/тестовых данных, где metrics не-object (например, list).
    metrics_dict = dict(metrics_existing) if isinstance(metrics_existing, dict) else {}
    if comment is not None:
        metrics_dict["comment"] = comment
    elif "comment" in metrics_dict:
        # Явно стираем старый comment если новый — None
        metrics_dict.pop("comment", None)

    await db.execute(
        text(
            "UPDATE task_results SET "
            "  is_correct = :is_correct, "
            "  score = :score, "
            "  checked_at = :now_ts, "
            "  checked_by = :teacher_id, "
            "  metrics = CAST(:metrics AS jsonb), "
            "  review_claimed_by = NULL, "
            "  review_claim_token = NULL, "
            "  review_claim_expires_at = NULL "
            "WHERE id = :rid"
        ),
        {
            "is_correct": is_correct,
            "score": score,
            "now_ts": now,
            "teacher_id": teacher_id,
            "metrics": _json_dumps(metrics_dict),
            "rid": result_id,
        },
    )

    # Догрузить task_title и user_email для caller'а (для inbox + email).
    # tasks не имеет колонки title — заголовок хранится в task_content->>'title'
    # с fallback на external_uid.
    r2 = await db.execute(
        text(
            "SELECT COALESCE(task_content->>'title', external_uid) AS title, "
            "course_id FROM tasks WHERE id = :tid"
        ),
        {"tid": task_id},
    )
    trow = r2.fetchone()
    task_title = trow[0] if trow else None
    course_id = trow[1] if trow and len(trow) > 1 else None

    r3 = await db.execute(
        text("SELECT email FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    urow = r3.fetchone()
    user_email = urow[0] if urow else None

    return {
        "result_id": result_id,
        "task_id": task_id,
        "user_id": user_id,
        "user_email": user_email,
        "score": score,
        "max_score": effective_max,
        "is_correct": is_correct,
        "comment": comment,
        "task_title": task_title,
        "course_id": course_id,
        "attempt_id": attempt_id,
    }


def _json_dumps(payload: dict) -> str:
    """Сериализовать dict в JSON-строку для bind в text() с CAST AS jsonb."""
    import json
    return json.dumps(payload, ensure_ascii=False, default=str)


async def regrade_review(
    db: AsyncSession,
    *,
    result_id: int,
    actor_user_id: int,
    score: int,
    comment: Optional[str],
) -> dict:
    """Y-6 Stage 3: re-grade уже оценённой проверки.

    Шаги в одной транзакции (caller commit'ит):
    1. SELECT FOR UPDATE task_results WHERE id=:rid.
    2. Validate: not_found → 404; checked_at IS NULL → 409 «not yet graded»
       (regrade требует initial grade); score > max_score → 422.
    3. Snapshot old: old_score, old_is_correct.
    4. Compute new is_correct = (score / max_score) >= REVIEW_PASS_THRESHOLD_RATIO.
    5. Append regrade event в metrics.regrade_history JSON-array.
    6. UPDATE task_results: score, is_correct, checked_at=now() (re-bump),
       checked_by=actor, metrics.

    Concurrency: SELECT FOR UPDATE сериализует параллельные regrade.
    Re-grade НЕ idempotent — каждый event важен (audit полный history).

    ACL — caller'side: endpoint должен сначала проверить is_service /
    methodist / teacher_courses(course_id).
    """
    from app.core.config import Settings as _SettingsCls
    _settings = _SettingsCls()

    now = datetime.now(timezone.utc)

    r = await db.execute(
        text(
            "SELECT tr.id, tr.task_id, tr.user_id, tr.score, tr.max_score, "
            "       tr.is_correct, tr.checked_at, tr.attempt_id, tr.metrics "
            "FROM task_results tr WHERE tr.id = :rid FOR UPDATE"
        ),
        {"rid": result_id},
    )
    row = r.fetchone()
    if row is None:
        raise GradeNotFoundError()

    (
        _id, task_id, user_id, old_score, max_score,
        old_is_correct, existing_checked_at, attempt_id, metrics_existing,
    ) = row

    # regrade требует, чтобы initial grade уже состоялся.
    if existing_checked_at is None:
        raise GradeConflictError(
            "Заявка ещё не оценена — сначала вызовите grade"
        )

    effective_max = int(max_score) if max_score is not None else 0
    if effective_max <= 0:
        raise GradeValidationError(
            "max_score не задан для задачи; regrade невозможен"
        )
    if score > effective_max:
        raise GradeValidationError(
            f"score={score} превышает max_score={effective_max}"
        )

    pass_ratio = float(_settings.review_pass_threshold_ratio)
    new_is_correct: bool = (float(score) / float(effective_max)) >= pass_ratio
    old_score_int = int(old_score) if old_score is not None else 0
    old_is_correct_bool = bool(old_is_correct) if old_is_correct is not None else False

    # Append regrade event в metrics.regrade_history (JSON array).
    # Защита от legacy/тестовых данных, где metrics не-object.
    metrics_dict = dict(metrics_existing) if isinstance(metrics_existing, dict) else {}
    history = list(metrics_dict.get("regrade_history") or [])
    history.append({
        "at": now.isoformat(),
        "by": int(actor_user_id),
        "old_score": old_score_int,
        "old_is_correct": old_is_correct_bool,
        "new_score": int(score),
        "new_is_correct": new_is_correct,
        "comment": comment,
    })
    metrics_dict["regrade_history"] = history
    if comment is not None:
        metrics_dict["comment"] = comment
    elif "comment" in metrics_dict:
        metrics_dict.pop("comment", None)

    await db.execute(
        text(
            "UPDATE task_results SET "
            "  is_correct = :is_correct, "
            "  score = :score, "
            "  checked_at = :now_ts, "
            "  checked_by = :actor, "
            "  metrics = CAST(:metrics AS jsonb) "
            "WHERE id = :rid"
        ),
        {
            "is_correct": new_is_correct,
            "score": int(score),
            "now_ts": now,
            "actor": int(actor_user_id),
            "metrics": _json_dumps(metrics_dict),
            "rid": result_id,
        },
    )

    # Догрузить task_title + course_id + user_email для caller (notify/email).
    r2 = await db.execute(
        text(
            "SELECT COALESCE(task_content->>'title', external_uid) AS title, "
            "course_id FROM tasks WHERE id = :tid"
        ),
        {"tid": task_id},
    )
    trow = r2.fetchone()
    task_title = trow[0] if trow else None
    course_id = trow[1] if trow and len(trow) > 1 else None

    r3 = await db.execute(
        text("SELECT email FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    urow = r3.fetchone()
    user_email = urow[0] if urow else None

    return {
        "result_id": result_id,
        "task_id": task_id,
        "user_id": user_id,
        "user_email": user_email,
        "old_score": old_score_int,
        "old_is_correct": old_is_correct_bool,
        "new_score": int(score),
        "new_is_correct": new_is_correct,
        "max_score": effective_max,
        "comment": comment,
        "task_title": task_title,
        "course_id": course_id,
        "attempt_id": attempt_id,
        "checked_at": now,
    }


async def get_pending_count(
    db: AsyncSession,
    teacher_id: int,
) -> tuple[int, Optional[datetime]]:
    """Количество pending-заявок преподавателя (без захвата) + oldest submitted_at.

    Используется TG_LMS поллером (Phase Y-4 §4.2.6).
    Применяет тот же REVIEW_ACL_SQL, что и claim_next_review.
    Включает только заявки, которые сейчас НЕ захвачены (или захват просрочен).
    """
    now = datetime.now(timezone.utc)
    # tsk-247: счётчик обязан считать РОВНО то, что выдаёт claim-next, — иначе
    # у преподавателя фантомная очередь, которую он не может обнулить (мотив
    # tsk-210). Поэтому предикат один и тот же — mandatory_review_sql().
    r = await db.execute(
        text(
            f"""
            SELECT COUNT(*) AS cnt, MIN(tr.submitted_at) AS oldest
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.checked_at IS NULL
              AND {mandatory_review_sql('t')}
              AND (tr.review_claim_expires_at IS NULL OR tr.review_claim_expires_at < :now_ts)
              AND {REVIEW_ACL_SQL}
            """  # nosec B608 — REVIEW_ACL_SQL из закрытого набора литералов
        ),
        {"teacher_id": teacher_id, "now_ts": now},
    )
    row = r.fetchone()
    if row is None:
        return (0, None)
    cnt = int(row[0] or 0)
    oldest = row[1]
    return (cnt, oldest)


async def list_pending_reviews(
    db: AsyncSession,
    teacher_id: int,
    *,
    course_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[list[dict], int]:
    """Список ожидающих ручной проверки работ в зоне ответственности преподавателя.

    Тот же предикат обязательной очереди, что у `claim_next_review`
    (`mandatory_review_sql` + `REVIEW_ACL_SQL`, `checked_at IS NULL`), но БЕЗ
    захвата/lock — read-only для веб-очереди SPW (tsk-298 Фаза 2). FIFO по
    `submitted_at`. Item лёгкий (без `answer_json` — он тяжёлый; полный ответ
    приходит при claim конкретной работы). `is_claimed` помечает работу, уже
    взятую кем-то на проверку (действующий lock), — для UI.

    :param teacher_id: ID преподавателя (ACL-scope через REVIEW_ACL_SQL).
    :param course_id: опциональный фильтр по курсу.
    :param limit: размер страницы.
    :param offset: смещение.
    :returns: (items, total) — total игнорирует limit/offset.
    """
    now = datetime.now(timezone.utc)
    course_cond = "AND t.course_id = :course_id" if course_id is not None else ""
    params: dict[str, Any] = {"teacher_id": teacher_id, "now_ts": now}
    if course_id is not None:
        params["course_id"] = course_id

    where_sql = f"""
        WHERE tr.checked_at IS NULL
          AND {mandatory_review_sql('t')}
          AND {REVIEW_ACL_SQL}
          {course_cond}
    """

    total_row = await db.execute(
        text(f"""
            SELECT COUNT(*)
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            {where_sql}
        """),  # nosec B608 — where_sql из закрытого набора литералов модуля
        params,
    )
    total = int(total_row.scalar() or 0)

    page_params = dict(params)
    page_params["limit"] = int(limit)
    page_params["offset"] = int(offset)
    r = await db.execute(
        text(f"""
            SELECT tr.id, tr.attempt_id, tr.task_id, tr.user_id, tr.score,
                   tr.max_score, tr.is_correct, tr.submitted_at,
                   t.external_uid, t.course_id, u.full_name,
                   (tr.review_claim_expires_at IS NOT NULL
                    AND tr.review_claim_expires_at >= :now_ts) AS is_claimed
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            LEFT JOIN users u ON u.id = tr.user_id
            {where_sql}
            ORDER BY tr.submitted_at ASC
            LIMIT :limit OFFSET :offset
        """),  # nosec B608 — where_sql из закрытого набора литералов модуля
        page_params,
    )
    items = [
        {
            "id": row[0],
            "attempt_id": row[1],
            "task_id": row[2],
            "user_id": row[3],
            "score": row[4],
            "max_score": row[5],
            "is_correct": row[6],
            "submitted_at": row[7],
            "task_title": row[8],
            "course_id": row[9],
            "user_name": row[10],
            "is_claimed": bool(row[11]),
        }
        for row in r.fetchall()
    ]
    return (items, total)


async def teacher_can_review_attempt(
    db: AsyncSession,
    attempt_id: int,
    teacher_id: int,
) -> bool:
    """Есть ли у преподавателя ACL на проверку хотя бы одной работы этой попытки.

    Вложение ответа относится к `task_result` внутри `attempt` (attempts —
    course-level, задача резолвится через task_results). Преподаватель может
    скачать вложение из веб-портала (tsk-298 Фаза 2), если авторизован
    (`REVIEW_ACL_SQL`: teacher на course-tree задачи ИЛИ methodist) хотя бы на
    одну задачу этой попытки. Read-only.

    :returns: True — доступ разрешён; False — нет.
    """
    r = await db.execute(
        text(f"""
            SELECT EXISTS (
                SELECT 1
                FROM task_results tr
                JOIN tasks t ON t.id = tr.task_id
                WHERE tr.attempt_id = :attempt_id
                  AND {REVIEW_ACL_SQL}
            )
        """),  # nosec B608 — REVIEW_ACL_SQL из закрытого набора литералов модуля
        {"attempt_id": attempt_id, "teacher_id": teacher_id},
    )
    return bool(r.scalar())


async def teacher_can_override_limit(
    db: AsyncSession,
    teacher_id: int,
    student_id: int,
    task_id: int,
) -> bool:
    """Может ли преподаватель переопределить лимит попыток по (student, task).

    tsk-298 Фаза 3-Ⅱ: override открыт cookie-преподавателю, но он write и ранее
    не имел ACL (сервис-only). Разрешаем, если преподаватель авторизован на
    задачу этого ученика: teacher на course-tree задачи (`teacher_course_acl`)
    ИЛИ ученик закреплён за ним (`student_teacher_links`) ИЛИ роль
    methodist/admin. Тот же принцип, что `can_access_help_request` для
    blocked_limit-заявки, из которой override и вызывается. Read-only.

    :returns: True — можно; False — нет.
    """
    r = await db.execute(
        text(f"""
            SELECT
                EXISTS (
                    SELECT 1 FROM tasks t
                    WHERE t.id = :task_id AND {teacher_course_acl('t.course_id')}
                )
                OR EXISTS (
                    SELECT 1 FROM student_teacher_links stl
                    WHERE stl.student_id = :student_id AND stl.teacher_id = :teacher_id
                )
                OR EXISTS (
                    SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                    WHERE ur.user_id = :teacher_id AND r.name IN ('methodist', 'admin')
                )
        """),  # nosec B608 — teacher_course_acl() из закрытого набора литералов модуля
        {"task_id": task_id, "student_id": student_id, "teacher_id": teacher_id},
    )
    return bool(r.scalar())


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

    # Y-6 pivot: pending review = `checked_at IS NULL` + type-whitelist
    # `('SA_COM','TA')`. tsk-210: + `is_correct IS TRUE` (паритет с
    # claim_next_review / count_pending_reviews — не считаем первично-неверные
    # SA_COM, которые в очередь не выдаются).
    r2 = await db.execute(
        text(f"""
            SELECT COUNT(*)
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.checked_at IS NULL
              AND t.task_content->>'type' IN ('SA_COM', 'TA')
              AND tr.is_correct IS TRUE
              AND {REVIEW_ACL_SQL}
        """),  # nosec B608 — REVIEW_ACL_SQL из закрытого набора литералов
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
