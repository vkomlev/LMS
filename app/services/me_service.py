"""Сервис /me — identities, прогресс по курсам, last-position, streak (Phase Y-3).

См. tech-spec Y-3 (LMS backend) §5.1-5.4, §7.6.
TZ-handling: streak compute ВСЕГДА в Europe/Moscow на стороне сервера; SPW
отображает значения как есть, без TZ-конверсии (см. tech-spec §4 и §5.4).
"""
import logging
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_link import IdentityLink
# Y-3.2 (S3-A4): единая точка правды — учебный движок.
from app.services.learning_engine_service import PASS_THRESHOLD_RATIO

logger = logging.getLogger(__name__)

IdentityKind = Literal["email", "tg", "vk"]


def mask_value(kind: IdentityKind, value: str) -> str:
    """Маскирование identity value для публичного response.

    Правила (см. tech-spec §5.1):
    - email → первые 3 + '***' + '@<домен>'; если local короче 3 → '***@<домен>'
    - tg → '***' + последние 4 символа
    - vk → первые 8 символов + '...' (если value длиннее 8; иначе как есть)
    """
    if kind == "email":
        if "@" not in value:
            return (value[:3] + "***") if len(value) >= 3 else "***"
        local, domain = value.split("@", 1)
        if len(local) >= 3:
            return f"{local[:3]}***@{domain}"
        return f"***@{domain}"
    if kind == "tg":
        return f"***{value[-4:]}" if len(value) >= 4 else f"***{value}"
    if kind == "vk":
        return f"{value[:8]}..." if len(value) > 8 else value
    return value


# ── /me/identities ──────────────────────────────────────────────────────────

async def get_identities(db: AsyncSession, user_id: int) -> list[dict]:
    """Список identities пользователя с masked values, sorted by created_at ASC."""
    result = await db.execute(
        select(IdentityLink)
        .where(IdentityLink.user_id == user_id)
        .order_by(IdentityLink.created_at.asc())
    )
    items: list[dict] = []
    for link in result.scalars():
        items.append(
            {
                "kind": link.kind,
                "value_masked": mask_value(link.kind, link.value),
                "created_at": link.created_at,
                "last_used_at": link.last_used_at,
            }
        )
    return items


# ── /me/courses ─────────────────────────────────────────────────────────────

# Single-roundtrip CTE — батч-запрос по всем активным курсам пользователя,
# избегаем N+1 (см. tech-spec §5.2). Дерево курса собирается рекурсивно через
# course_parents; tasks_done считает заданий с PASS по последней завершённой
# попытке (та же логика, что в learning_engine_service.compute_course_state).
_COURSES_PROGRESS_SQL = """
WITH RECURSIVE active_uc AS (
    SELECT user_id, course_id, order_number
    FROM user_courses
    WHERE user_id = :user_id AND is_active = true
),
course_trees AS (
    SELECT root.course_id AS root_course_id,
           root.course_id AS member_course_id,
           0 AS depth
    FROM active_uc root
    UNION ALL
    SELECT ct.root_course_id, cp.course_id, ct.depth + 1
    FROM course_trees ct
    JOIN course_parents cp ON cp.parent_course_id = ct.member_course_id
),
tasks_per_root AS (
    SELECT ct.root_course_id, t.id AS task_id
    FROM course_trees ct
    JOIN tasks t ON t.course_id = ct.member_course_id
),
materials_per_root AS (
    SELECT ct.root_course_id, m.id AS material_id
    FROM course_trees ct
    JOIN materials m ON m.course_id = ct.member_course_id
    WHERE m.is_active = true
),
last_attempt_per_task AS (
    SELECT DISTINCT ON (tr.task_id)
        tr.task_id, tr.score, tr.max_score, tr.received_at
    FROM task_results tr
    INNER JOIN attempts a
        ON a.id = tr.attempt_id
       AND a.user_id = :user_id
       AND a.finished_at IS NOT NULL
       AND a.cancelled_at IS NULL
    WHERE tr.user_id = :user_id
    ORDER BY tr.task_id, a.finished_at DESC, a.id DESC
),
tasks_total_per_root AS (
    SELECT root_course_id, COUNT(*) AS tasks_total
    FROM tasks_per_root
    GROUP BY root_course_id
),
tasks_done_per_root AS (
    SELECT tpr.root_course_id,
           COUNT(*) FILTER (
               WHERE lap.max_score > 0
                 AND (lap.score::float / lap.max_score) >= :pass_ratio
           ) AS tasks_done
    FROM tasks_per_root tpr
    LEFT JOIN last_attempt_per_task lap USING (task_id)
    GROUP BY tpr.root_course_id
),
materials_total_per_root AS (
    SELECT root_course_id, COUNT(*) AS materials_total
    FROM materials_per_root
    GROUP BY root_course_id
),
materials_done_per_root AS (
    SELECT mpr.root_course_id, COUNT(*) AS materials_done
    FROM materials_per_root mpr
    JOIN student_material_progress smp
        ON smp.material_id = mpr.material_id
       AND smp.student_id = :user_id
       AND smp.status = 'completed'
    GROUP BY mpr.root_course_id
),
last_active_per_root AS (
    SELECT root_course_id,
           GREATEST(MAX(tr_max), MAX(smp_max)) AS last_active_at
    FROM (
        SELECT tpr.root_course_id,
               MAX(tr.received_at) AS tr_max,
               NULL::timestamptz AS smp_max
        FROM tasks_per_root tpr
        LEFT JOIN task_results tr ON tr.task_id = tpr.task_id AND tr.user_id = :user_id
        GROUP BY tpr.root_course_id
        UNION ALL
        SELECT mpr.root_course_id,
               NULL::timestamptz AS tr_max,
               MAX(smp.completed_at) AS smp_max
        FROM materials_per_root mpr
        LEFT JOIN student_material_progress smp
            ON smp.material_id = mpr.material_id
           AND smp.student_id = :user_id
           AND smp.status = 'completed'
        GROUP BY mpr.root_course_id
    ) U
    GROUP BY root_course_id
)
SELECT
    uc.course_id,
    c.course_uid,
    c.title,
    uc.order_number,
    COALESCE(tt.tasks_total, 0)      AS tasks_total,
    COALESCE(td.tasks_done, 0)       AS tasks_done,
    COALESCE(mt.materials_total, 0)  AS materials_total,
    COALESCE(md.materials_done, 0)   AS materials_done,
    la.last_active_at,
    scs.state                        AS course_state
FROM active_uc uc
JOIN courses c ON c.id = uc.course_id
LEFT JOIN tasks_total_per_root     tt ON tt.root_course_id = uc.course_id
LEFT JOIN tasks_done_per_root      td ON td.root_course_id = uc.course_id
LEFT JOIN materials_total_per_root mt ON mt.root_course_id = uc.course_id
LEFT JOIN materials_done_per_root  md ON md.root_course_id = uc.course_id
LEFT JOIN last_active_per_root     la ON la.root_course_id = uc.course_id
LEFT JOIN student_course_state    scs ON scs.course_id = uc.course_id AND scs.student_id = :user_id
ORDER BY la.last_active_at DESC NULLS LAST, uc.order_number ASC NULLS LAST
"""


async def get_courses_with_progress(db: AsyncSession, user_id: int) -> list[dict]:
    """Список активных курсов пользователя с агрегированным progress (single roundtrip).

    Логика progress:
    - tasks_total/done — count из дерева курса (root + потомки через course_parents)
    - tasks_done — count distinct task_id с PASS по последней завершённой попытке
      (score/max_score >= 0.5; та же логика, что в LearningEngineService.compute_course_state)
    - materials_total/done — count из student_material_progress (status='completed')
    - last_active_at — MAX(received_at task_results) ∪ MAX(completed_at student_material_progress)
    - is_completed — student_course_state.state == 'COMPLETED'
    - percent — округлённое (tasks_done + materials_done) / (tasks_total + materials_total) * 100

    Sort: last_active_at DESC NULLS LAST, order_number ASC NULLS LAST.
    """
    result = await db.execute(
        text(_COURSES_PROGRESS_SQL),
        {"user_id": user_id, "pass_ratio": PASS_THRESHOLD_RATIO},
    )
    rows = result.mappings().all()
    items: list[dict] = []
    for row in rows:
        tasks_total = row["tasks_total"]
        materials_total = row["materials_total"]
        tasks_done = row["tasks_done"]
        materials_done = row["materials_done"]
        denominator = tasks_total + materials_total
        if denominator > 0:
            percent = round(((tasks_done + materials_done) / denominator) * 100)
        else:
            percent = 0
        items.append(
            {
                "course_id": row["course_id"],
                "course_uid": row["course_uid"],
                "title": row["title"],
                "order_number": row["order_number"],
                "progress": {
                    "tasks_total": tasks_total,
                    "tasks_done": tasks_done,
                    "materials_total": materials_total,
                    "materials_done": materials_done,
                    "percent": percent,
                },
                "last_active_at": row["last_active_at"],
                "is_completed": row["course_state"] == "COMPLETED",
            }
        )
    return items


# ── /me/last-position ───────────────────────────────────────────────────────

_LAST_ACTIVITY_SQL = """
WITH last_task AS (
    SELECT tr.received_at AS ts, t.course_id, 'task'::text AS kind, t.id AS task_id, t.external_uid, NULL::int AS material_id
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.user_id = :user_id
    ORDER BY tr.received_at DESC NULLS LAST
    LIMIT 1
),
last_material AS (
    SELECT smp.completed_at AS ts, m.course_id, 'material'::text AS kind, NULL::int AS task_id, NULL::text AS external_uid, m.id AS material_id
    FROM student_material_progress smp
    JOIN materials m ON m.id = smp.material_id
    WHERE smp.student_id = :user_id AND smp.status = 'completed'
    ORDER BY smp.completed_at DESC NULLS LAST
    LIMIT 1
)
SELECT * FROM (
    SELECT * FROM last_task
    UNION ALL
    SELECT * FROM last_material
) U
ORDER BY ts DESC NULLS LAST
LIMIT 1
"""


async def get_last_position(db: AsyncSession, user_id: int) -> dict | None:
    """Последняя активность пользователя + резолв next-item (CB §5.3, LMS spec §5.3).

    Возвращает dict (см. LastPositionRead) или None если ничего не открывал.

    Логика (per spec §5.3):
    1. Найти MAX(received_at task_results) ∪ MAX(completed_at student_material_progress) → course_id, last_active_at
    2. Если ученик ничего не открывал → None
    3. Если course_state == 'COMPLETED' → type='course_completed'
    4. Иначе вызвать `LearningEngineService.resolve_next_item(user_id)` → следующий material/task
       (через дерево курса; engine упорядочивает по user_courses.order_number)
    5. blocked_dependency / blocked_limit / none → возвращаем type='course_completed' если
       text/material progress есть, иначе None — frontend Continue widget может deep-link
       на курс с подсказкой о блокировке
    """
    # Step 1: найти последнюю активность
    result = await db.execute(text(_LAST_ACTIVITY_SQL), {"user_id": user_id})
    row = result.mappings().first()
    if row is None:
        return None

    last_active_course_id = row["course_id"]
    last_ts = row["ts"]

    # Получим course_uid + title + course_state для last-active курса
    course_row = (
        await db.execute(
            text(
                """
                SELECT c.id, c.course_uid, c.title, scs.state
                FROM courses c
                LEFT JOIN student_course_state scs
                    ON scs.course_id = c.id AND scs.student_id = :user_id
                WHERE c.id = :course_id
                """
            ),
            {"user_id": user_id, "course_id": last_active_course_id},
        )
    ).mappings().first()
    if course_row is None:
        return None

    if course_row["state"] == "COMPLETED":
        return {
            "course_id": last_active_course_id,
            "course_uid": course_row["course_uid"],
            "course_title": course_row["title"],
            "type": "course_completed",
            "last_active_at": last_ts,
        }

    # Step 4: вызов learning engine для NEXT item
    # Импорт inline во избежание circular import (engine → repos → ...)
    from app.services.learning_engine_service import LearningEngineService

    engine = LearningEngineService()
    next_item = await engine.resolve_next_item(db, student_id=user_id)
    next_course_id = next_item.course_id or last_active_course_id

    # Если NEXT в другом курсе — обновим course_uid/title под него
    if next_course_id != last_active_course_id:
        next_course_row = (
            await db.execute(
                text("SELECT id, course_uid, title FROM courses WHERE id = :course_id"),
                {"course_id": next_course_id},
            )
        ).mappings().first()
        if next_course_row is not None:
            course_row = next_course_row

    if next_item.type == "material":
        return {
            "course_id": next_course_id,
            "course_uid": course_row["course_uid"],
            "course_title": course_row["title"],
            "type": "material",
            "material_id": next_item.material_id,
            "last_active_at": last_ts,
        }

    if next_item.type == "task":
        # Получим external_uid для deeplink в SPW
        ext_row = (
            await db.execute(
                text("SELECT external_uid FROM tasks WHERE id = :task_id"),
                {"task_id": next_item.task_id},
            )
        ).mappings().first()
        return {
            "course_id": next_course_id,
            "course_uid": course_row["course_uid"],
            "course_title": course_row["title"],
            "type": "task",
            "task_id": next_item.task_id,
            "external_uid": ext_row["external_uid"] if ext_row else None,
            "last_active_at": last_ts,
        }

    # next_item.type ∈ {'none', 'blocked_dependency', 'blocked_limit'}:
    # все active курсы пройдены или заблокированы. Трактуем как course_completed
    # (last-active course — курс к которому виджет «Продолжить» отправит без действия).
    return {
        "course_id": last_active_course_id,
        "course_uid": course_row["course_uid"],
        "course_title": course_row["title"],
        "type": "course_completed",
        "last_active_at": last_ts,
    }


# ── /me/streak ──────────────────────────────────────────────────────────────

# CTE расчёта streak в TZ Europe/Moscow (см. tech-spec §5.4):
# 1) active_days — distinct дни активности (по received_at::date в Europe/Moscow)
# 2) numbered — gap-detection через ROW_NUMBER:
#    для DESC-упорядоченных последовательных дней `d + rn*1d` константа
#    (newest=T,rn=1 → T+1; T-1,rn=2 → T+1; T-2,rn=3 → T+1)
#    Y-3.1 fix: исходный spec использовал DESC + минус (математически ломалось — см. test_streak_logic).
# 3) current_run — записи с MAX(grp) → самый свежий run; внешний Python-код обнуляет
#    streak если last < today-1.
_STREAK_SQL = """
WITH active_days AS (
    SELECT DISTINCT (received_at AT TIME ZONE 'Europe/Moscow')::date AS d
    FROM task_results
    WHERE user_id = :user_id
),
numbered AS (
    SELECT d,
           d + (ROW_NUMBER() OVER (ORDER BY d DESC))::int * INTERVAL '1 day' AS grp
    FROM active_days
),
current_run AS (
    SELECT COUNT(*) AS streak_days, MAX(d) AS last_active_date
    FROM numbered
    WHERE grp = (SELECT MAX(grp) FROM numbered)
)
SELECT streak_days, last_active_date FROM current_run
"""


async def get_streak(db: AsyncSession, user_id: int) -> dict:
    """Streak дней подряд в Europe/Moscow (см. tech-spec §5.4).

    Streak обнуляется если last_active_date < (today_moscow - 1 day).
    today_active — true если сегодня (Europe/Moscow) есть task_result.
    """
    row = (await db.execute(text(_STREAK_SQL), {"user_id": user_id})).mappings().first()
    streak_days = int(row["streak_days"]) if row and row["streak_days"] else 0
    last_active_date: date | None = row["last_active_date"] if row else None

    # Today в Europe/Moscow
    today_row = (
        await db.execute(text("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS today"))
    ).mappings().first()
    today_msk: date = today_row["today"]

    # Обнуление streak если last < yesterday
    if last_active_date is not None:
        gap_days = (today_msk - last_active_date).days
        if gap_days > 1:
            streak_days = 0
            last_active_date = None

    today_active = last_active_date == today_msk

    return {
        "streak_days": streak_days,
        "last_active_date": last_active_date,
        "today_active": today_active,
    }


# ── Phase Y-4: /me/history ──────────────────────────────────────────────────

HistoryFilter = Literal["all", "pending_review", "passed", "failed"]


_HISTORY_SQL = """
SELECT
    tr.id AS task_result_id,
    tr.task_id,
    t.external_uid AS task_external_uid,
    t.course_id,
    c.course_uid,
    c.title AS course_title,
    COALESCE(t.task_content->>'title', t.external_uid) AS task_title,
    t.task_content->>'type' AS type,
    CASE
        WHEN tr.is_correct IS NULL THEN 'pending_review'
        WHEN tr.is_correct = TRUE  THEN 'passed'
        WHEN tr.is_correct = FALSE THEN 'failed'
        ELSE 'pending_review'
    END AS status,
    tr.score,
    tr.max_score,
    tr.metrics->>'comment' AS comment,
    tr.received_at,
    tr.submitted_at,
    tr.checked_at
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
LEFT JOIN courses c ON c.id = t.course_id
WHERE tr.user_id = :user_id
  AND (
       :filter = 'all'
    OR (:filter = 'pending_review' AND tr.is_correct IS NULL)
    OR (:filter = 'passed'         AND tr.is_correct = TRUE)
    OR (:filter = 'failed'         AND tr.is_correct = FALSE)
  )
ORDER BY tr.received_at DESC
LIMIT :limit OFFSET :offset
"""


# ── Phase Y-6.2: /me/courses/{course_id}/syllabus-states ────────────────────

# Single SQL: tasks + materials поддерева курса с last task_result и
# completed_at для материалов. tree_ids собирается заранее на стороне
# Python через LearningEngineService._collect_courses_in_order, чтобы
# сохранить depth-first порядок с учётом course_parents.order_number.
#
# attempts_used = COUNT task_results по active (cancelled_at IS NULL) attempts —
# парность с learning_engine_service.compute_task_state (Y-5.3 fix).
# attempts_limit_effective: override → tasks.max_attempts → DEFAULT_MAX_ATTEMPTS.
_SYLLABUS_TASKS_SQL = """
WITH last_per_task AS (
    SELECT DISTINCT ON (tr.task_id)
        tr.task_id,
        tr.is_correct,
        tr.checked_at,
        tr.score,
        tr.max_score,
        tr.submitted_at
    FROM task_results tr
    INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    WHERE tr.user_id = :user_id
      AND tr.task_id IN (
          SELECT id FROM tasks WHERE course_id = ANY(:tree_ids)
      )
    ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
),
attempts_per_task AS (
    SELECT tr.task_id, COUNT(*) AS used
    FROM task_results tr
    INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    WHERE tr.user_id = :user_id
      AND tr.task_id IN (
          SELECT id FROM tasks WHERE course_id = ANY(:tree_ids)
      )
    GROUP BY tr.task_id
),
override_per_task AS (
    SELECT task_id, max_attempts_override AS lim
    FROM student_task_limit_override
    WHERE student_id = :user_id
),
open_course_attempt AS (
    SELECT DISTINCT a.course_id
    FROM attempts a
    WHERE a.user_id = :user_id
      AND a.cancelled_at IS NULL
      AND a.finished_at IS NULL
      AND a.course_id = ANY(:tree_ids)
)
SELECT
    t.id AS task_id,
    t.course_id,
    lp.is_correct AS last_is_correct,
    lp.checked_at AS last_checked_at,
    lp.score AS last_score,
    lp.max_score AS last_max_score,
    lp.submitted_at AS last_submitted_at,
    COALESCE(ap.used, 0) AS attempts_used,
    COALESCE(op.lim, t.max_attempts, :default_max) AS attempts_limit_effective,
    EXISTS (
        SELECT 1 FROM open_course_attempt oc WHERE oc.course_id = t.course_id
    ) AS has_open_attempt
FROM tasks t
LEFT JOIN last_per_task lp ON lp.task_id = t.id
LEFT JOIN attempts_per_task ap ON ap.task_id = t.id
LEFT JOIN override_per_task op ON op.task_id = t.id
WHERE t.course_id = ANY(:tree_ids)
ORDER BY t.course_id, t.id
"""

_SYLLABUS_MATERIALS_SQL = """
SELECT
    m.id AS material_id,
    m.course_id,
    m.order_position,
    smp.completed_at
FROM materials m
LEFT JOIN student_material_progress smp
    ON smp.material_id = m.id
   AND smp.student_id = :user_id
   AND smp.status = 'completed'
WHERE m.course_id = ANY(:tree_ids)
  AND m.is_active = true
ORDER BY m.course_id,
         m.order_position ASC NULLS LAST,
         m.id ASC
"""

_BLOCKED_COURSES_SQL = """
SELECT DISTINCT cd.course_id
FROM course_dependencies cd
WHERE cd.course_id = ANY(:tree_ids)
  AND NOT EXISTS (
      SELECT 1 FROM student_course_state scs
      WHERE scs.student_id = :user_id
        AND scs.course_id = cd.required_course_id
        AND scs.state = 'COMPLETED'
  )
"""


def _compute_syllabus_task_status(row: dict) -> str:
    """Маппинг last task_result + attempts → public-status строка.

    Правила (см. tech-spec syllabus-states):
    - passed         — last is_correct=TRUE, checked_at NOT NULL
    - pending_review — last is_correct=TRUE, checked_at IS NULL (Y-6 optimistic);
                       также legacy IS NULL pending (pre-Y-6 SA_COM/TA)
    - failed         — last is_correct=FALSE и attempts_used < limit
    - blocked_limit  — last is_correct=FALSE и attempts_used >= limit
    - in_progress    — нет task_result, но есть открытый course-level attempt
    - not_started    — нет task_result и нет открытого attempt
    """
    last_submitted = row["last_submitted_at"]
    is_correct = row["last_is_correct"]
    checked_at = row["last_checked_at"]
    attempts_used = int(row["attempts_used"] or 0)
    limit_eff = int(row["attempts_limit_effective"] or 0)
    has_open = bool(row["has_open_attempt"])

    if last_submitted is None:
        return "in_progress" if has_open else "not_started"

    if is_correct is True:
        return "passed" if checked_at is not None else "pending_review"
    if is_correct is None:
        # legacy pre-Y-6 pending state (TA/SA_COM до optimistic-PASSED backfill)
        return "pending_review"
    # is_correct is False
    if limit_eff > 0 and attempts_used >= limit_eff:
        return "blocked_limit"
    return "failed"


async def _collect_section_meta(
    db: AsyncSession,
    courses_repo: Any,
    root_id: int,
) -> list[dict]:
    """Depth-first walk дерева с meta для рендера syllabus sections (Phase Y-6.2 ext).

    Параллельный walker к `LearningEngineService._collect_courses_in_order`
    (тот возвращает только IDs); этот возвращает богатый dict для SPW
    sticky-headers — `course_id`, `title`, `depth`, `parent_course_id`,
    `order_number`. Совпадает с logic _collect_courses_in_order по сортировке
    (order_number ASC NULLS LAST, затем id) — items[] и sections[] идут в одном
    порядке.

    Args:
        db: async session.
        courses_repo: instance CoursesRepository (для get_children + get).
        root_id: ID корневого course.

    Returns:
        list[dict] — depth-first walk; первый элемент = root.
    """
    result: list[dict] = []

    root_course = await courses_repo.get(db, root_id)
    if root_course is None:
        return result
    result.append(
        {
            "course_id": root_course.id,
            "title": root_course.title,
            "depth": 0,
            "parent_course_id": None,
            "order_number": None,
        }
    )

    async def walk(parent_id: int, depth: int) -> None:
        children = await courses_repo.get_children(db, parent_id)
        sorted_children = sorted(
            children,
            key=lambda x: (0 if x[1] is not None else 1, x[1] or 0, x[0].id),
        )
        for course, order in sorted_children:
            result.append(
                {
                    "course_id": course.id,
                    "title": course.title,
                    "depth": depth,
                    "parent_course_id": parent_id,
                    "order_number": order,
                }
            )
            await walk(course.id, depth + 1)

    await walk(root_id, 1)
    return result


async def get_syllabus_states(
    db: AsyncSession,
    user_id: int,
    root_course_id: int,
) -> dict:
    """Снимок состояний задач+материалов поддерева курса для SPW syllabus-рендера.

    Phase Y-6.2. Single SQL roundtrip per resource (tasks/materials/blocked):
    - tree_ids — depth-first traversal через `_collect_section_meta`
      (учитывает course_parents.order_number; те же правила сортировки,
      что и `LearningEngineService._collect_courses_in_order`)
    - per-task status — последний task_result × attempts_used × override
    - per-material status — student_material_progress.status='completed'
    - blocked_courses — course_dependencies без COMPLETED prerequisite
      в `student_course_state` пользователя.
    - sections (Y-6.2 ext) — список подкурсов с titles+depth для SPW
      sticky-headers (нужно потому что `/courses/{id}/tree` legacy
      service-key only — недоступен студенту под cookie auth).

    Items emit'ятся: для каждого course в depth-first order — материалы
    (по order_position), затем задания (по id) — паритет с
    `_first_incomplete_material` / `_first_incomplete_task` в learning engine.

    Args:
        db: async session.
        user_id: ID студента.
        root_course_id: ID корневого course (любой узел дерева).

    Returns:
        dict {course_id, items, blocked_courses, sections}.
    """
    # Импорт inline во избежание circular import (engine → repos → ...)
    from app.services.learning_engine_service import (
        DEFAULT_MAX_ATTEMPTS,
        LearningEngineService,
    )

    engine = LearningEngineService()
    section_meta = await _collect_section_meta(db, engine._courses_repo, root_course_id)
    if not section_meta:
        section_meta = [
            {
                "course_id": root_course_id,
                "title": "",
                "depth": 0,
                "parent_course_id": None,
                "order_number": None,
            }
        ]
    tree_ids: list[int] = [m["course_id"] for m in section_meta]

    tasks_res = await db.execute(
        text(_SYLLABUS_TASKS_SQL),
        {
            "user_id": user_id,
            "tree_ids": tree_ids,
            "default_max": DEFAULT_MAX_ATTEMPTS,
        },
    )
    task_rows = tasks_res.mappings().all()

    materials_res = await db.execute(
        text(_SYLLABUS_MATERIALS_SQL),
        {"user_id": user_id, "tree_ids": tree_ids},
    )
    material_rows = materials_res.mappings().all()

    blocked_res = await db.execute(
        text(_BLOCKED_COURSES_SQL),
        {"user_id": user_id, "tree_ids": tree_ids},
    )
    blocked_courses: list[int] = [int(r[0]) for r in blocked_res.fetchall()]

    # Группировка по course_id для depth-first emit
    materials_by_course: dict[int, list[dict]] = {}
    for r in material_rows:
        materials_by_course.setdefault(r["course_id"], []).append(dict(r))
    tasks_by_course: dict[int, list[dict]] = {}
    for r in task_rows:
        tasks_by_course.setdefault(r["course_id"], []).append(dict(r))

    items: list[dict] = []
    for cid in tree_ids:
        for m in materials_by_course.get(cid, []):
            items.append(
                {
                    "kind": "material",
                    "material_id": int(m["material_id"]),
                    "course_id": int(m["course_id"]),
                    "status": "completed" if m["completed_at"] is not None else "not_started",
                    "completed_at": m["completed_at"],
                }
            )
        for t in tasks_by_course.get(cid, []):
            items.append(
                {
                    "kind": "task",
                    "task_id": int(t["task_id"]),
                    "course_id": int(t["course_id"]),
                    "status": _compute_syllabus_task_status(t),
                    "attempts_used": int(t["attempts_used"] or 0),
                    "attempts_limit_effective": int(t["attempts_limit_effective"] or 0),
                    "last_score": int(t["last_score"]) if t["last_score"] is not None else None,
                    "last_max_score": (
                        int(t["last_max_score"]) if t["last_max_score"] is not None else None
                    ),
                    "last_submitted_at": t["last_submitted_at"],
                }
            )

    return {
        "course_id": root_course_id,
        "items": items,
        "blocked_courses": blocked_courses,
        "sections": section_meta,
    }


async def get_history(
    db: AsyncSession,
    user_id: int,
    *,
    filter_: HistoryFilter = "all",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """История попыток ученика с фильтрами (Phase Y-4 §4.2.5).

    Использует существующий M7 индекс idx_task_results_user_received
    для эффективной выборки по user_id + ORDER BY received_at DESC.
    """
    result = await db.execute(
        text(_HISTORY_SQL),
        {
            "user_id": user_id,
            "filter": filter_,
            "limit": limit,
            "offset": offset,
        },
    )
    return [dict(row) for row in result.mappings().all()]
