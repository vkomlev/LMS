"""
Сервис заявок на помощь (Learning Engine V1, этап 3.8 / 3.8.1).

- Создание/обновление заявки при request-help (manual_help).
- Auto-create при BLOCKED_LIMIT (blocked_limit, этап 3.8.1).
- Назначение преподавателя (student_teacher_links → teacher_courses).
- ACL: teacher/methodist по назначению, связям или роли.
- Закрытие и ответ с идемпотентностью.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.help_requests import HelpRequests
from app.models.help_request_replies import HelpRequestReplies
from app.utils.task_title import HINT_MAX_LEN, TITLE_MAX_LEN, humanize_task_title
from app.services.learning_events_service import (
    record_help_request_opened,
    record_help_request_closed,
    record_help_request_replied,
    record_attempt_limit_reached,
)
from app.services import inbox_service
from app.services import methodist_notify_service
from app.services.messages_service import MessagesService
from app.services.student_teacher_links_service import StudentTeacherLinksService
from app.services.teacher_courses_service import TeacherCoursesService
from app.services.teacher_queue_service import (
    HELP_REQUESTS_ACL_SQL,
    teacher_course_acl,
)

logger = logging.getLogger(__name__)


def _normalize_due_at(due_at: Any) -> Optional[datetime]:
    """Приводит due_at из сырого SQL (str или datetime) к timezone-aware datetime для сравнения с now."""
    if due_at is None:
        return None
    if isinstance(due_at, str):
        s = due_at.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(due_at, datetime):
        return due_at if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
    return None


def _claim_state(
    claimed_by: Optional[int],
    claim_expires_at: Any,
    claimed_by_name: Optional[str],
    viewer_teacher_id: int,
    now: datetime,
) -> dict[str, Any]:
    """Состояние захвата заявки для списка и карточки (tsk-592).

    Единственное место, где «занята» выводится из трёх колонок, — чтобы список
    и карточка не разъехались в определении занятости (тот же принцип, что у
    общих предикатов очереди в `teacher_queue_service`).

    Просроченный захват — это СВОБОДНАЯ заявка: `claim_expires_at` и есть
    защита от «вечно занятых», если клиент не позвал release. Поэтому
    `is_claimed=False` при истёкшем сроке, хотя `claimed_by` в строке остался.

    :param viewer_teacher_id: кто смотрит — от него зависит `claimed_by_me`
        (свой захват интерфейс не показывает как блокировку).
    """
    expires_norm = _normalize_due_at(claim_expires_at)
    is_claimed = (
        claimed_by is not None
        and expires_norm is not None
        and expires_norm >= now
    )
    return {
        "claimed_by": claimed_by if is_claimed else None,
        "claimed_by_name": claimed_by_name if is_claimed else None,
        "claim_expires_at": expires_norm if is_claimed else None,
        "is_claimed": is_claimed,
        "claimed_by_me": bool(is_claimed and claimed_by == viewer_teacher_id),
    }


def _task_title_display(
    task_id: int,
    external_uid: Optional[str],
    title: Optional[str] = None,
    stem: Optional[str] = None,
    *,
    max_len: int = TITLE_MAX_LEN,
) -> str:
    """Человекочитаемый заголовок задания для отображения (tsk-298 follow-up).

    Делегирует в общий helper: curated title → очищенный stem → external_uid
    → «Задание #id». Раньше отдавал сырой external_uid (MVP-заглушка).

    ``max_len=HINT_MAX_LEN`` (tsk-342) — полное условие для карточки заявки
    (учителю нужен весь контекст задачи, чтобы ответить), а не короткий
    фрагмент для списка/шапки.
    """
    return humanize_task_title(task_id, title, stem, external_uid, max_len=max_len)


async def resolve_assigned_teacher(
    db: AsyncSession,
    student_id: int,
    course_id: Optional[int],
) -> Optional[int]:
    """
    MVP: первый доступный преподаватель из student_teacher_links;
    fallback — из teacher_courses по course_id.
    """
    links_svc = StudentTeacherLinksService()
    teachers = await links_svc.list_teachers(db, student_id)
    if teachers:
        return teachers[0].id
    if course_id is not None:
        tc_svc = TeacherCoursesService()
        teachers_list, _ = await tc_svc.list_teachers(db, course_id, limit=1)
        if teachers_list:
            return teachers_list[0].id
    return None


async def get_or_create_help_request(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    event_id: int,
    message: Optional[str] = None,
    course_id: Optional[int] = None,
    attempt_id: Optional[int] = None,
    deduplicated: bool = False,
) -> Tuple[int, bool]:
    """
    После record_help_requested: получить или создать запись в help_requests.
    Если deduplicated и заявка с event_id уже есть — вернуть её id и created=False.
    Иначе создать новую, записать help_request_opened, вернуть (id, True).
    """
    r = await db.execute(
        text("SELECT id FROM help_requests WHERE event_id = :event_id LIMIT 1"),
        {"event_id": event_id},
    )
    row = r.fetchone()
    if row is not None:
        await db.execute(
            text("UPDATE help_requests SET updated_at = now() WHERE id = :id"),
            {"id": row[0]},
        )
        return (int(row[0]), False)

    assigned = await resolve_assigned_teacher(db, student_id, course_id)
    msg_truncated = (message or "")[:2000] if message else None

    r = await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id, course_id, attempt_id, event_id, message, assigned_teacher_id, created_at, updated_at)
            VALUES ('open', 'manual_help', false, '{}'::jsonb, :student_id, :task_id, :course_id, :attempt_id, :event_id, :message, :assigned_teacher_id, now(), now())
            RETURNING id
        """),
        {
            "student_id": student_id,
            "task_id": task_id,
            "course_id": course_id,
            "attempt_id": attempt_id,
            "event_id": event_id,
            "message": msg_truncated,
            "assigned_teacher_id": assigned,
        },
    )
    new_id = r.scalar()
    await record_help_request_opened(
        db, student_id, new_id, event_id, task_id, course_id
    )
    if assigned is not None:
        # tsk-348 follow-up: раньше учитель узнавал о заявке ТОЛЬКО через
        # pending-count (число в бейдже/боте) — негде было прочитать, что
        # именно случилось, и перейти к заявке одним кликом. Inbox-запись
        # даёт реальную ленту (как у ученика) + CTA-переход.
        await inbox_service.create_for_user(
            db,
            user_id=assigned,
            kind="help_request_opened",
            title="Новый вопрос от ученика",
            content=msg_truncated or "Ученик запросил помощь по заданию.",
            payload={"request_id": int(new_id), "task_id": task_id, "student_id": student_id},
            created_by=student_id,
        )
    return (int(new_id), True)


async def get_or_create_blocked_limit_help_request(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    course_id: Optional[int] = None,
    attempt_id: Optional[int] = None,
    attempts_used: int = 0,
    attempts_limit_effective: int = 3,
    last_based_status: str = "BLOCKED_LIMIT",
) -> Tuple[int, bool, bool]:
    """
    Получить или создать open заявку типа blocked_limit для пары (student_id, task_id).
    Идемпотентно: одна open заявка blocked_limit на пару; повтор — обновление updated_at/context.
    Returns: (request_id, created, deduplicated).
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": student_id, "k2": task_id},
    )
    r = await db.execute(
        text("""
            SELECT id, attempt_id, context_json FROM help_requests
            WHERE student_id = :student_id AND task_id = :task_id
              AND status = 'open' AND request_type = 'blocked_limit'
            LIMIT 1
        """),
        {"student_id": student_id, "task_id": task_id},
    )
    row = r.fetchone()
    context = {
        "attempts_used": attempts_used,
        "attempts_limit_effective": attempts_limit_effective,
        "last_based_status": last_based_status,
        "trigger": "blocked_limit",
    }
    context_str = json.dumps(context)
    if row is not None:
        request_id = int(row[0])
        await db.execute(
            text("""
                UPDATE help_requests
                SET updated_at = now(), attempt_id = COALESCE(:attempt_id, attempt_id),
                    context_json = :context_json
                WHERE id = :id
            """),
            {"id": request_id, "attempt_id": attempt_id, "context_json": context_str},
        )
        await record_attempt_limit_reached(
            db, student_id, request_id, task_id, attempts_used, attempts_limit_effective, course_id
        )
        return (request_id, False, True)
    assigned = await resolve_assigned_teacher(db, student_id, course_id)
    r = await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id, course_id, attempt_id, event_id, assigned_teacher_id, created_at, updated_at)
            VALUES ('open', 'blocked_limit', true, :context_json, :student_id, :task_id, :course_id, :attempt_id, NULL, :assigned_teacher_id, now(), now())
            RETURNING id
        """),
        {
            "context_json": context_str,
            "student_id": student_id,
            "task_id": task_id,
            "course_id": course_id,
            "attempt_id": attempt_id,
            "assigned_teacher_id": assigned,
        },
    )
    new_id = r.scalar()
    await record_attempt_limit_reached(
        db, student_id, new_id, task_id, attempts_used, attempts_limit_effective, course_id
    )
    if assigned is not None:
        await inbox_service.create_for_user(
            db,
            user_id=assigned,
            kind="help_request_opened",
            title="Ученик заблокирован лимитом попыток",
            content=f"Использовано попыток: {attempts_used}/{attempts_limit_effective}.",
            payload={"request_id": int(new_id), "task_id": task_id, "student_id": student_id},
            created_by=None,
        )
    return (int(new_id), True, False)


def awaiting_teacher_sql(alias: str = "hr") -> str:
    """SQL-условие «заявка ждёт ответа УЧИТЕЛЯ», без привязки к преподавателю.

    tsk-742: правило вынесено из счётчика ниже, потому что читать его стало
    нужно и другому экрану (доска куратора). Скопированный вариант неизбежно
    выродился бы в `status = 'open'` — а это, как объяснено в
    `get_help_requests_pending_count`, СОВСЕМ другое множество: заявка остаётся
    открытой, пока её не закроет ученик, в том числе после ответа учителя.
    Тогда у куратора на экране вечно висели бы «просроченные» заявки, на
    которые он уже ответил, — верный способ отучить смотреть на экран.

    :param alias: алиас `help_requests` в вызывающем запросе. Только литералы
        из закрытого набора call-sites, user-input сюда не попадает.
    """
    return f"""
        {alias}.status = 'open'
        AND (
            {alias}.thread_id IS NULL
            OR (
                SELECT m.sender_id
                FROM messages m
                WHERE m.thread_id = {alias}.thread_id
                ORDER BY m.sent_at DESC, m.id DESC
                LIMIT 1
            ) = {alias}.student_id
        )
    """  # nosec B608 — alias из закрытого набора литералов


async def get_help_requests_pending_count(
    db: AsyncSession,
    teacher_id: int,
) -> Tuple[int, Optional[datetime]]:
    """Количество заявок помощи, ждущих ОТВЕТА УЧИТЕЛЯ (manual_help + blocked_limit),
    назначенных на преподавателя, + oldest created_at среди них.

    tsk-348: используется TG_LMS bot-поллером и веб-бейджем учителя в SPW.
    До этой заявки поллер отслеживал только очередь ручной проверки заданий
    (`teacher_queue_service.get_pending_count` / task_results) — help_requests
    вообще не опрашивались, из-за чего живой запрос помощи от ученика оставался
    незамеченным. Прямой фильтр по assigned_teacher_id — та же «своя» очередь,
    что видит преподаватель в разделе «Вопросы студентов».

    tsk-415: `status='open'` сам по себе не значит «требует внимания учителя» —
    заявка остаётся open, пока ученик её не закроет, даже после того как учитель
    уже ответил. Считаем только заявки, где последнее слово осталось за учеником:
    либо треда ещё нет (учитель вообще не отвечал), либо последнее сообщение в
    треде — от student_id (ученик написал после ответа учителя, напр. через
    /messages/{id}/reply). Если последним писал учитель — заявка ждёт ученика,
    в счётчик не попадает (но остаётся open в списке заявок).
    """
    r = await db.execute(
        text(f"""
            SELECT COUNT(*) AS cnt, MIN(hr.created_at) AS oldest
            FROM help_requests hr
            WHERE hr.assigned_teacher_id = :teacher_id
              AND {awaiting_teacher_sql('hr')}
        """),  # nosec B608 — фрагмент собран из литералов модуля
        {"teacher_id": teacher_id},
    )
    row = r.fetchone()
    if row is None:
        return (0, None)
    return (int(row[0] or 0), row[1])


async def help_request_exists(db: AsyncSession, request_id: int) -> bool:
    """Проверка существования заявки по id (без ACL)."""
    r = await db.execute(
        text("SELECT id FROM help_requests WHERE id = :request_id LIMIT 1"),
        {"request_id": request_id},
    )
    return r.fetchone() is not None


async def can_access_help_request(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
) -> bool:
    """
    Доступ: assigned_teacher_id = teacher_id, или связь student_teacher_links,
    или teacher_courses по course_id, или роль methodist.
    """
    r = await db.execute(
        text("""
            SELECT hr.id, hr.assigned_teacher_id, hr.student_id, hr.course_id
            FROM help_requests hr
            WHERE hr.id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return False
    rid, assigned, student_id, course_id = row[0], row[1], row[2], row[3]

    if assigned == teacher_id:
        return True
    r = await db.execute(
        text("""
            SELECT 1 FROM student_teacher_links
            WHERE student_id = :student_id AND teacher_id = :teacher_id LIMIT 1
        """),
        {"student_id": student_id, "teacher_id": teacher_id},
    )
    if r.fetchone() is not None:
        return True
    if course_id is not None:
        # Y-4.1: hierarchical ACL — teacher на root-курсе видит потомков.
        # `teacher_course_acl(':course_id')` строит EXISTS с WITH RECURSIVE,
        # параметры course_id и teacher_id идут через bind.
        r = await db.execute(
            text(f"SELECT 1 WHERE {teacher_course_acl(':course_id')}"),
            {"teacher_id": teacher_id, "course_id": course_id},
        )
        if r.fetchone() is not None:
            return True
    r = await db.execute(
        text("""
            SELECT 1 FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = :teacher_id AND r.name = 'methodist' LIMIT 1
        """),
        {"teacher_id": teacher_id},
    )
    if r.fetchone() is not None:
        return True
    return False


def _order_by_sort(sort: str) -> str:
    """ORDER BY для списка заявок (этап 3.9). sort: priority | created_at | due_at."""
    if sort == "due_at":
        return "ORDER BY hr.due_at ASC NULLS LAST, hr.created_at ASC"
    if sort == "created_at":
        return "ORDER BY hr.created_at ASC"
    return "ORDER BY hr.priority ASC, hr.due_at ASC NULLS LAST, hr.created_at ASC"


async def list_help_requests(
    db: AsyncSession,
    teacher_id: int,
    status_filter: str = "open",
    request_type_filter: str = "all",
    limit: int = 20,
    offset: int = 0,
    sort: str = "priority",
    overdue: bool = False,
    student_id: Optional[int] = None,
) -> Tuple[list[dict[str, Any]], int]:
    """
    Список заявок с ACL. status_filter: open | closed | all.
    request_type_filter: manual_help | blocked_limit | all (этап 3.8.1).
    sort: priority | created_at | due_at (этап 3.9).
    overdue: True — только просроченные (due_at < now), ортогонально типу (tsk-312).
    student_id: tsk-473 — сузить до ОДНОГО ученика на уровне SQL (не постфильтр
    в Python по общей странице учителя) — иначе `status_filter="all"` с большой
    историей учителя рискует не влезть в `limit` и потерять недавние заявки
    именно этого ученика (сортировка не по recency-для-ученика, а по
    priority/due_at по ВСЕМ его заявкам сразу).
    Возвращает (items, total). items — словари для HelpRequestListItem.
    """
    status_cond = ""
    if status_filter == "open":
        status_cond = "AND hr.status = 'open'"
    elif status_filter == "closed":
        status_cond = "AND hr.status = 'closed'"
    type_cond = ""
    if request_type_filter == "manual_help":
        type_cond = "AND hr.request_type = 'manual_help'"
    elif request_type_filter == "blocked_limit":
        type_cond = "AND hr.request_type = 'blocked_limit'"
    elif request_type_filter == "individual_review":
        type_cond = "AND hr.request_type = 'individual_review'"
    student_cond = ""
    order_sql = _order_by_sort(sort)

    # Y-4.1: переиспользуем общий HELP_REQUESTS_ACL_SQL из teacher_queue_service —
    # hierarchical через teacher_course_acl(); methodist-bypass сохранён.
    acl_sql = HELP_REQUESTS_ACL_SQL
    now = datetime.now(timezone.utc)
    params: dict[str, Any] = {"teacher_id": teacher_id}
    if student_id is not None:
        student_cond = "AND hr.student_id = :student_id"
        params["student_id"] = student_id

    # tsk-312: отдельная ось фильтра «только просроченные» (ортогональна типу).
    # Предикат зеркалит get_teacher_workload.overdue_total, чтобы ячейка
    # «Просрочено» и её список считались по одному правилу (TZ-aware bind :now_ts,
    # как в _normalize_due_at). Фильтруем на сервере, а не по is_overdue поверх
    # limit/offset, иначе просроченные за пределами страницы пропадут из списка.
    overdue_cond = ""
    if overdue:
        overdue_cond = "AND hr.due_at IS NOT NULL AND hr.due_at < :now_ts"
        params["now_ts"] = now

    r = await db.execute(
        text(f"""
            SELECT COUNT(*) FROM help_requests hr
            WHERE {acl_sql} {status_cond} {type_cond} {student_cond} {overdue_cond}
        """),
        params,
    )
    total = r.scalar() or 0

    r = await db.execute(
        text(f"""
            SELECT hr.id, hr.status, hr.request_type, hr.auto_created, hr.context_json,
                   hr.student_id, hr.task_id, hr.course_id, hr.attempt_id,
                   hr.created_at, hr.updated_at, hr.thread_id, hr.event_id,
                   hr.priority, hr.due_at,
                   u.full_name AS student_name,
                   t.external_uid AS task_external_uid,
                   c.title AS course_title,
                   t.task_content->>'title' AS task_title_raw,
                   t.task_content->>'stem' AS task_stem,
                   -- tsk-592: кто держит заявку в работе и до какого времени.
                   -- Колонки в БД были с этапа 3.9, но наружу не отдавались —
                   -- поэтому второй преподаватель не видел занятость и брался
                   -- за ту же заявку.
                   hr.claimed_by, hr.claim_expires_at, cu.full_name AS claimed_by_name
            FROM help_requests hr
            LEFT JOIN users u ON u.id = hr.student_id
            LEFT JOIN tasks t ON t.id = hr.task_id
            LEFT JOIN courses c ON c.id = hr.course_id
            LEFT JOIN users cu ON cu.id = hr.claimed_by
            WHERE {acl_sql} {status_cond} {type_cond} {student_cond} {overdue_cond}
            {order_sql}
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": limit, "offset": offset},
    )
    rows = r.fetchall()
    items = []
    for row in rows:
        ctx = row[4] if row[4] is not None else {}
        due_at = row[14] if len(row) > 14 else None
        due_at_norm = _normalize_due_at(due_at)
        priority_val = int(row[13]) if len(row) > 13 and row[13] is not None else 100
        items.append({
            "request_id": row[0],
            "status": row[1],
            "request_type": row[2] or "manual_help",
            "auto_created": bool(row[3]) if row[3] is not None else False,
            "context": ctx if isinstance(ctx, dict) else {},
            "student_id": row[5],
            "task_id": row[6],
            "course_id": row[7],
            "attempt_id": row[8],
            "created_at": row[9],
            "updated_at": row[10],
            "thread_id": row[11],
            "event_id": row[12],
            "priority": priority_val,
            "due_at": due_at_norm,
            "is_overdue": due_at_norm is not None and due_at_norm < now,
            "student_name": row[15] if len(row) > 15 else None,
            "task_title": _task_title_display(
                row[6],
                row[16] if len(row) > 16 else None,
                row[18] if len(row) > 18 else None,
                row[19] if len(row) > 19 else None,
            ),
            "course_title": row[17] if len(row) > 17 else None,
            # tsk-592: занятость заявки — колонки 20-22 в порядке SELECT выше.
            **_claim_state(
                row[20] if len(row) > 20 else None,
                row[21] if len(row) > 21 else None,
                row[22] if len(row) > 22 else None,
                teacher_id,
                now,
            ),
        })
    return (items, total)


async def get_help_request_detail(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Карточка заявки с историей ответов.
    Возвращает (detail_dict, error). error: None | "not_found" | "forbidden".
    """
    r = await db.execute(
        text("SELECT id FROM help_requests WHERE id = :request_id"),
        {"request_id": request_id},
    )
    if r.fetchone() is None:
        return (None, "not_found")
    ok = await can_access_help_request(db, request_id, teacher_id)
    if not ok:
        return (None, "forbidden")

    r = await db.execute(
        text("""
            SELECT hr.id, hr.status, hr.student_id, hr.task_id, hr.course_id, hr.attempt_id,
                   hr.created_at, hr.updated_at, hr.thread_id, hr.event_id,
                   hr.request_type, hr.auto_created, hr.context_json,
                   hr.message, hr.closed_at, hr.closed_by, hr.resolution_comment,
                   hr.priority, hr.due_at,
                   u.full_name AS student_name,
                   t.external_uid AS task_external_uid,
                   c.title AS course_title,
                   t.task_content->>'title' AS task_title_raw,
                   t.task_content->>'stem' AS task_stem,
                   hr.webinar_link, hr.review_understood, hr.escalated_to_methodist_at,
                   (SELECT COUNT(*) FROM help_request_reopens rr
                     WHERE rr.request_id = hr.id) AS reopen_count,
                   -- tsk-592: занятость заявки, то же поле, что в списке.
                   hr.claimed_by, hr.claim_expires_at, cu.full_name AS claimed_by_name
            FROM help_requests hr
            LEFT JOIN users u ON u.id = hr.student_id
            LEFT JOIN tasks t ON t.id = hr.task_id
            LEFT JOIN courses c ON c.id = hr.course_id
            LEFT JOIN users cu ON cu.id = hr.claimed_by
            WHERE hr.id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (None, "not_found")
    ctx = row[12] if row[12] is not None else {}
    if not isinstance(ctx, dict):
        ctx = {}
    now = datetime.now(timezone.utc)
    # tsk-298 (fix): off-by-one — SELECT отдаёт 22 колонки (0-21), а маппинг
    # читал row[18..22], сдвигая priority/due_at/student_name/task_title/
    # course_title на +1. Из-за этого «Ученик» показывал external_uid задания
    # вместо ФИО. Индексы приведены к реальному порядку колонок.
    due_at = row[18] if len(row) > 18 else None
    due_at_norm = _normalize_due_at(due_at)
    is_overdue = due_at_norm is not None and due_at_norm < now
    priority_val = int(row[17]) if len(row) > 17 and row[17] is not None else 100

    r2 = await db.execute(
        text("""
            SELECT id, teacher_id, message_id, body, close_after_reply, created_at
            FROM help_request_replies
            WHERE request_id = :request_id
            ORDER BY created_at ASC
        """),
        {"request_id": request_id},
    )
    replies = [
        {
            "reply_id": r[0],
            "teacher_id": r[1],
            "message_id": r[2],
            "body": r[3],
            "close_after_reply": r[4],
            "created_at": r[5],
        }
        for r in r2.fetchall()
    ]

    return ({
        "request_id": row[0],
        "status": row[1],
        "student_id": row[2],
        "task_id": row[3],
        "course_id": row[4],
        "attempt_id": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "thread_id": row[8],
        "event_id": row[9],
        "request_type": row[10] or "manual_help",
        "auto_created": bool(row[11]) if row[11] is not None else False,
        "context": ctx,
        "message": row[13],
        "closed_at": row[14],
        "closed_by": row[15],
        "resolution_comment": row[16],
        "priority": priority_val,
        "due_at": due_at_norm,
        "is_overdue": is_overdue,
        "student_name": row[19] if len(row) > 19 else None,
        "task_title": _task_title_display(
            row[3],
            row[20] if len(row) > 20 else None,
            row[22] if len(row) > 22 else None,
            row[23] if len(row) > 23 else None,
        ),
        # tsk-342: полное условие задания (не обрезка в 80 симв., а под
        # разумный предел карточки) — учителю нужен весь контекст, чтобы
        # ответить на заявку помощи, не только фрагмент в шапке.
        "task_full_title": _task_title_display(
            row[3],
            row[20] if len(row) > 20 else None,
            row[22] if len(row) > 22 else None,
            row[23] if len(row) > 23 else None,
            max_len=HINT_MAX_LEN,
        ),
        "course_title": row[21] if len(row) > 21 else None,
        # tsk-303: колонки 24-27 — состояние лестницы помощи. Индексы считаются
        # от порядка SELECT выше; в этом же маппинге когда-то уже был сдвиг на
        # +1 (см. комментарий про off-by-one), поэтому новые поля добавлены в
        # конец, а не в середину.
        "webinar_link": row[24] if len(row) > 24 else None,
        "review_understood": row[25] if len(row) > 25 else None,
        "escalated_to_methodist_at": row[26] if len(row) > 26 else None,
        "reopen_count": int(row[27]) if len(row) > 27 and row[27] is not None else 0,
        # tsk-592: занятость заявки — колонки 28-30 в порядке SELECT выше.
        # Считается тем же `_claim_state`, что в списке: два экрана обязаны
        # одинаково понимать «в работе».
        **_claim_state(
            row[28] if len(row) > 28 else None,
            row[29] if len(row) > 29 else None,
            row[30] if len(row) > 30 else None,
            teacher_id,
            now,
        ),
        "history": replies,
    }, None)


async def check_help_request_lock(
    db: AsyncSession,
    request_id: int,
    teacher_id: Optional[int],
    lock_token: Optional[str],
) -> Optional[str]:
    """
    Проверить право писать в заявку. Возвращает None если можно, иначе
    "lock_conflict" (409).

    Правила:
    - `teacher_id is None` — системное действие (tsk-339: ученик решил задание
      сам; tsk-303: ученик подтвердил, что разбор помог). Оно не конкурирует с
      преподавателем и проходит всегда, иначе заявка зависла бы открытой только
      потому, что кто-то держит её карточку.
    - Передан `lock_token` — строгая сверка: заявка захвачена ЭТИМ
      преподавателем, тем же токеном, срок не истёк.
    - Токена нет (tsk-592) — раньше это означало «пиши свободно», и второй
      преподаватель отвечал поверх чужой работы, даже когда заявка была явно
      взята. Теперь без токена пропускаем, только если заявку никто не держит
      или держит сам вызывающий. Чужая ДЕЙСТВУЮЩАЯ отметка — отказ; выйти из
      него можно перехватом (`POST /{id}/claim` с `takeover=true`), который
      выдаёт свой токен и пишется в журнал событий.
    - Истёкшая отметка не считается: она и есть защита от «вечно занятых»
      заявок, если клиент не позвал release.
    """
    if teacher_id is None:
        return None
    r = await db.execute(
        text("""
            SELECT claimed_by, claim_token, claim_expires_at FROM help_requests WHERE id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return None
    claimed_by, claim_token, claim_expires_at = row[0], row[1], row[2]
    now = datetime.now(timezone.utc)
    claim_expires_norm = _normalize_due_at(claim_expires_at)
    claim_active = claimed_by is not None and claim_expires_norm is not None and claim_expires_norm >= now

    if not lock_token:
        if claim_active and claimed_by != teacher_id:
            logger.info(
                "tsk-592 write denied: request_id=%s teacher_id=%s claimed_by=%s (нет токена, заявка у другого)",
                request_id, teacher_id, claimed_by,
            )
            # Отдельный код, а не общий lock_conflict: причина другая, и текст
            # «токен невалиден» здесь сбил бы с толку — токена не было вовсе.
            return "claimed_by_other"
        return None

    if claimed_by != teacher_id or claim_token != lock_token:
        return "lock_conflict"
    if claim_expires_norm is None or claim_expires_norm < now:
        return "lock_conflict"
    return None


async def close_help_request(
    db: AsyncSession,
    request_id: int,
    closed_by: Optional[int],
    resolution_comment: Optional[str] = None,
    lock_token: Optional[str] = None,
) -> Tuple[Optional[dict[str, Any]], Optional[bool], Optional[str]]:
    """
    Закрыть заявку. Возвращает (data_dict, already_closed, error).
    error: None | "lock_conflict" (этап 3.9, при невалидном lock_token) |
    "claimed_by_other" (tsk-592: заявку ведёт другой преподаватель).
    data_dict: request_id, status, closed_at, updated_at. Если заявка не найдена — (None, None, None).

    ``closed_by=None`` — системное закрытие (tsk-339: задание решено учеником
    самостоятельно, без действия учителя); `check_help_request_lock` в этом
    случае не участвует (без `lock_token` возвращает None сразу).
    """
    r = await db.execute(
        text("""
            SELECT id, status, student_id, task_id FROM help_requests WHERE id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (None, None, None)
    hid, current_status, student_id, task_id = row[0], row[1], row[2], row[3]

    err = await check_help_request_lock(db, request_id, closed_by, lock_token)
    if err is not None:
        return (None, None, err)

    if current_status == "closed":
        r = await db.execute(
            text("SELECT closed_at, updated_at FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
        rw = r.fetchone()
        return ({
            "request_id": request_id,
            "status": "closed",
            "closed_at": rw[0],
            "updated_at": rw[1],
            "already_closed": True,
        }, True, None)

    now = datetime.now(timezone.utc)
    comment_truncated = (resolution_comment or "")[:2000] or None
    # tsk-303: TTL вебинар-ссылки — она живёт ровно пока заявка открыта
    # (решение оператора). Обнуление стоит здесь, в единственной точке закрытия,
    # чтобы инвариант держался на ВСЕХ путях сразу: закрытие учителем, ответ с
    # закрытием, системное закрытие и подтверждение ученика после разбора.
    await db.execute(
        text("""
            UPDATE help_requests
            SET status = 'closed', closed_at = :closed_at, closed_by = :closed_by,
                resolution_comment = :resolution_comment, updated_at = :updated_at,
                webinar_link = NULL
            WHERE id = :id
        """),
        {
            "id": request_id,
            "closed_at": now,
            "closed_by": closed_by,
            "resolution_comment": comment_truncated,
            "updated_at": now,
        },
    )
    await record_help_request_closed(db, student_id, request_id, closed_by, resolution_comment)
    if closed_by is not None:
        # tsk-348: только явное закрытие учителем (не системное авто-закрытие
        # tsk-339, когда ученик решил задачу сам, — там пуш не нужен).
        await inbox_service.create_for_user(
            db,
            user_id=student_id,
            kind="help_request_closed",
            title="Ваш вопрос закрыт учителем",
            content=comment_truncated or "Учитель отметил вопрос как решённый.",
            payload={"request_id": request_id, "task_id": task_id, "resolution_comment": comment_truncated},
            created_by=closed_by,
        )
    return ({
        "request_id": request_id,
        "status": "closed",
        "closed_at": now,
        "updated_at": now,
        "already_closed": False,
    }, False, None)


async def close_blocked_limit_if_resolved(
    db: AsyncSession,
    student_id: int,
    task_id: int,
    resolution_comment: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    tsk-339: если по паре (student_id, task_id) есть открытая заявка
    `blocked_limit` — закрыть её системно (``closed_by=None``).

    Вызывается после того, как задание перешло в PASSED НЕ через выдачу лимита
    учителем (tsk-335, там закрытие явное — `useCloseBlockedLimitRequest`), а
    тем, что ученик всё же решил его сам: блокировка снялась естественно,
    заявка больше не отражает реальное состояние. Без этого шага заявка висела
    в очереди «из них лимит» бессрочно — найдено живым прогоном tsk-335/336
    (9 подтверждённых стухших заявок на проде на момент находки).

    Возвращает результат `close_help_request`, либо None, если открытой
    заявки не было (частый случай — не считать его ошибкой).
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": student_id, "k2": task_id},
    )
    row = (
        await db.execute(
            text(
                "SELECT id FROM help_requests "
                "WHERE student_id = :student_id AND task_id = :task_id "
                "  AND status = 'open' AND request_type = 'blocked_limit'"
            ),
            {"student_id": student_id, "task_id": task_id},
        )
    ).fetchone()
    if row is None:
        return None
    data, _already_closed, _err = await close_help_request(
        db, int(row[0]), closed_by=None, resolution_comment=resolution_comment,
    )
    return data


# ----- tsk-303: лестница помощи, сторона ученика -----
#
# Три шага поверх уже готовой заявки `manual_help`:
#   уровень 1 → «Вернуть заявку» (ответ не помог, начисляется в KPI учителя);
#   уровень 2 → «Запросить индивидуальный разбор» (только после возврата);
#   уровень 3 → оценка после разбора; «непонятно» уводит заявку методисту.
#
# Новых записей в `learning_events` эти шаги НЕ делают: каждый из них durable
# отражён в доменных таблицах (строка в `help_request_reopens`, смена
# `request_type`, отметка `escalated_to_methodist_at`), а `learning_events`
# в приложении никем не читается — второй, несогласуемый источник правды тут
# был бы лишним. Закрытие заявки своё событие пишет как и раньше — через
# `close_help_request`.

_LADDER_TYPES = ("manual_help", "individual_review")

# Отдельное пространство ключей advisory-локов: в этом же файле уже есть
# блокировки по паре (student_id, task_id) для blocked_limit, и совпадение
# ключей заблокировало бы несвязанные между собой вещи.
_LADDER_LOCK_NAMESPACE = 303


async def _lock_request(db: AsyncSession, request_id: int) -> None:
    """Сериализовать шаги лестницы по одной заявке.

    Кнопки ученика легко нажать дважды (двойной клик, повтор запроса сети):
    без блокировки два «Вернуть заявку» дали бы две строки возврата, то есть
    задвоенный KPI учителя, а два запроса разбора — гонку на смене типа.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": _LADDER_LOCK_NAMESPACE, "k2": request_id},
    )


# tsk-599: сколько заявок за период должно быть у преподавателя, чтобы доля
# вообще считалась. Ниже порога процент — шум: 0 из 2 поставило бы человека в
# лучшие ни за что, а 1 из 3 — в худшие. Порог решён оператором (2026-08-17)
# по фактическому распределению на проде: у трёх преподавателей из четырёх
# меньше десяти заявок в месяц.
MIN_REQUESTS_FOR_RATE = 10


async def get_reopen_kpi(
    db: AsyncSession,
    *,
    teacher_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Агрегат возвратов заявок по преподавателям — KPI «текст не помог».

    Один запрос на двух потребителей (решение плана, чтобы не разошлись):
    преподаватель смотрит свой показатель (`teacher_id=<свой>`), методист и
    админ — по всем сразу (`teacher_id=None`).

    **Что именно считается (tsk-599).** Это единственная в продукте поверхность
    сравнения людей, поэтому определение метрики зафиксировано здесь, а не
    вычитывается из формы запроса:

    * *Когорта периода* — заявки лестницы (`manual_help`, `individual_review`),
      созданные не раньше `since`. Окно режется по `created_at` ЗАЯВКИ, а не по
      `reopened_at` возврата: числитель и знаменатель обязаны считать одно и то
      же множество, иначе доля способна превысить 100% (возврат внутри окна по
      заявке, созданной до его начала).
    * *Хозяин заявки* — `COALESCE(кому приписан последний возврат, closed_by,
      assigned_teacher_id)`. Решение оператора: «его заявка» — та, которую он
      закрыл. Но возврат обнуляет `closed_by` (заявка снова открыта), и без
      первого слагаемого возвращённая-и-ещё-не-закрытая заявка осталась бы без
      хозяина, а её возврат — без знаменателя. Порядок слагаемых гарантирует,
      что заявка с возвратом всегда принадлежит тому, кого возврат обвиняет, —
      то есть числитель по построению не бывает больше знаменателя.
    * *Знаменатель* `requests` — сколько заявок когорты у этого хозяина.
    * *Числитель* `reopened_requests` — сколько из них вернули хотя бы раз.
      Именно заявок, а не возвратов: три возврата по одной заявке — это один
      неудачный разбор, а не три.
    * `reopens` — сколько всего возвратов; справочное число рядом с долей.
    * `reopen_rate` — доля 0..1 либо `None`, если заявок меньше
      `MIN_REQUESTS_FOR_RATE`. `None` читается как «мало данных, сравнивать
      нельзя» и отличается от честного нуля.

    В выборку попадают ВСЕ действующие преподаватели, а не только те, у кого
    есть возвраты: прежний запрос «от таблицы возвратов» делал «нет строки»
    одновременно и «сработал отлично», и «к нему не обращались» — по такому
    списку нельзя ни сравнить, ни оправдаться. Преподаватель без заявок за
    период приходит с `requests=0` и `reopen_rate=None`, панель показывает «нет
    данных». Тот, кто уже не работает (`is_active=false` или объединённая
    учётка), в списке не висит — но остаётся, если заявки за период у него
    были, иначе они молча исчезли бы из сводки.

    Известное ограничение: если заявку вернули из-за ответа одного человека, а
    закрыл её потом другой, она остаётся у первого. Так возврат не теряется;
    второй лишается одной заявки в знаменателе, но его доля от этого не растёт.
    """
    params: dict[str, Any] = {}
    since_sql = ""
    if since is not None:
        since_sql = "AND h.created_at >= :since"
        params["since"] = since
    teacher_sql = ""
    if teacher_id is not None:
        teacher_sql = "WHERE roster.teacher_id = :teacher_id"
        params["teacher_id"] = teacher_id

    rows = (
        await db.execute(
            text(f"""
                WITH ladder AS (
                    SELECT h.id,
                           COALESCE(
                               (SELECT rr.teacher_id
                                  FROM help_request_reopens rr
                                 WHERE rr.request_id = h.id
                                   AND rr.teacher_id IS NOT NULL
                                 ORDER BY rr.reopened_at DESC, rr.id DESC
                                 LIMIT 1),
                               h.closed_by,
                               h.assigned_teacher_id
                           ) AS owner_id
                      FROM help_requests h
                     WHERE h.request_type IN ('manual_help', 'individual_review')
                           {since_sql}
                ),
                per_request AS (
                    SELECT l.owner_id,
                           rr.cnt,
                           rr.last_at
                      FROM ladder l
                      LEFT JOIN LATERAL (
                          SELECT COUNT(*) AS cnt, MAX(x.reopened_at) AS last_at
                            FROM help_request_reopens x
                           WHERE x.request_id = l.id
                      ) rr ON TRUE
                     WHERE l.owner_id IS NOT NULL
                ),
                agg AS (
                    SELECT owner_id AS teacher_id,
                           COUNT(*) AS requests,
                           COUNT(*) FILTER (WHERE cnt > 0) AS reopened_requests,
                           COALESCE(SUM(cnt), 0) AS reopens,
                           MAX(last_at) AS last_reopened_at
                      FROM per_request
                     GROUP BY owner_id
                ),
                roster AS (
                    SELECT ur.user_id AS teacher_id
                      FROM user_roles ur
                      JOIN roles r ON r.id = ur.role_id
                      JOIN users u ON u.id = ur.user_id
                     WHERE r.name = 'teacher'
                       AND u.is_active
                       AND u.merged_into_user_id IS NULL
                    UNION
                    SELECT teacher_id FROM agg
                )
                SELECT roster.teacher_id,
                       u.full_name,
                       COALESCE(a.requests, 0),
                       COALESCE(a.reopened_requests, 0),
                       COALESCE(a.reopens, 0),
                       a.last_reopened_at
                  FROM roster
                  LEFT JOIN agg a ON a.teacher_id = roster.teacher_id
                  LEFT JOIN users u ON u.id = roster.teacher_id
                  {teacher_sql}
            """),  # nosec B608 — фрагменты собраны из литералов модуля, значения идут через bind
            params,
        )
    ).fetchall()

    items = [
        {
            "teacher_id": int(r[0]),
            "teacher_name": r[1],
            "requests": int(r[2] or 0),
            "reopened_requests": int(r[3] or 0),
            "reopens": int(r[4] or 0),
            "reopen_rate": (
                round(int(r[3] or 0) / int(r[2]), 4)
                if int(r[2] or 0) >= MIN_REQUESTS_FOR_RATE
                else None
            ),
            "last_reopened_at": r[5],
        }
        for r in rows
    ]
    # Сортировка на стороне Python: строк единицы, зато правило видно целиком.
    # Сначала те, кого вообще можно сравнивать (доля посчитана), худшие сверху —
    # как и было. Затем «мало данных» по убыванию объёма, затем «нет данных».
    items.sort(
        key=lambda it: (
            0 if it["reopen_rate"] is not None else (1 if it["requests"] > 0 else 2),
            -(it["reopen_rate"] or 0.0),
            -it["requests"],
            it["teacher_id"],
        )
    )
    return items


async def _repeat_ask_count(
    db: AsyncSession,
    request_id: int,
    student_id: int,
    task_id: int,
) -> int:
    """Сколько раз ученик обращался по этому заданию СВЕРХ первого раза.

    Оператор задал гейт уровня 2 как «повторная заявка по тому же `task_id`».
    Повтор бывает двух видов, и засчитывать надо оба:

    1. ученик вернул эту же заявку кнопкой «Вернуть заявку» (штатный путь);
    2. ученик завёл по тому же заданию ВТОРУЮ заявку — `request-help`
       дедуплицирует обращения лишь окном в несколько минут, так что это
       достижимо и без всякой кнопки.

    Считать только (1) значило бы отказать в разборе ученику, который просил
    помощь дважды — то есть ровно тому, для кого уровень 2 и придуман.
    """
    return int(
        (
            await db.execute(
                text("""
                    SELECT
                      (SELECT COUNT(*) FROM help_request_reopens rr
                        WHERE rr.request_id = :request_id)
                      +
                      (SELECT COUNT(*) FROM help_requests hr
                        WHERE hr.student_id = :student_id
                          AND hr.task_id = :task_id
                          AND hr.request_type = ANY(:types)
                          AND hr.id <> :request_id)
                """),
                {
                    "request_id": request_id,
                    "student_id": student_id,
                    "task_id": task_id,
                    "types": list(_LADDER_TYPES),
                },
            )
        ).scalar()
        or 0
    )


async def get_student_help_request(
    db: AsyncSession,
    student_id: int,
    task_id: int,
) -> Optional[dict[str, Any]]:
    """Текущая заявка помощи ученика по заданию — для его собственного экрана.

    Берётся последняя заявка лестницы (`manual_help`/`individual_review`) по
    паре (ученик, задание). `blocked_limit` сюда не попадает: это другой
    механизм со своим интерфейсом, лестницы помощи у него нет.

    Признаки `can_*` считает сервер, а не клиент: гейты уровней — часть
    правил, и второй их экземпляр в UI неизбежно разъедется с этим.
    """
    row = (
        await db.execute(
            text("""
                SELECT hr.id, hr.status, hr.request_type, hr.message,
                       hr.created_at, hr.updated_at, hr.closed_at,
                       hr.webinar_link, hr.review_understood,
                       hr.escalated_to_methodist_at, hr.resolution_comment,
                       (SELECT COUNT(*) FROM help_request_reopens rr
                         WHERE rr.request_id = hr.id) AS reopen_count
                FROM help_requests hr
                WHERE hr.student_id = :student_id
                  AND hr.task_id = :task_id
                  AND hr.request_type = ANY(:types)
                ORDER BY hr.created_at DESC, hr.id DESC
                LIMIT 1
            """),
            {"student_id": student_id, "task_id": task_id, "types": list(_LADDER_TYPES)},
        )
    ).fetchone()
    if row is None:
        return None

    request_id = int(row[0])
    status_val, request_type = row[1], row[2]
    webinar_link, review_understood = row[7], row[8]
    reopen_count = int(row[11] or 0)
    is_open = status_val == "open"

    replies = [
        {"body": r[0], "created_at": r[1]}
        for r in (
            await db.execute(
                text("""
                    SELECT body, created_at FROM help_request_replies
                    WHERE request_id = :request_id ORDER BY created_at ASC
                """),
                {"request_id": request_id},
            )
        ).fetchall()
    ]
    repeat_asks = await _repeat_ask_count(db, request_id, student_id, task_id)

    return {
        "request_id": request_id,
        "status": status_val,
        "request_type": request_type or "manual_help",
        "message": row[3],
        "created_at": row[4],
        "updated_at": row[5],
        "closed_at": row[6],
        "webinar_link": webinar_link,
        "review_understood": review_understood,
        "escalated_to_methodist_at": row[9],
        "resolution_comment": row[10],
        "reopen_count": reopen_count,
        "replies": replies,
        # Гейты уровней — те же условия, что проверяют сами операции ниже.
        "can_reopen": (not is_open) and request_type == "manual_help",
        "can_request_individual_review": (
            is_open and request_type == "manual_help" and repeat_asks >= 1
        ),
        "can_rate_review": (
            is_open
            and request_type == "individual_review"
            and webinar_link is not None
            and review_understood is None
        ),
    }


async def reopen_help_request(
    db: AsyncSession,
    request_id: int,
    student_id: int,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """«Вернуть заявку»: ответ учителя не помог, заявка снова открыта.

    Возврат начисляется тому, чей ответ не помог — `closed_by` на момент
    возврата, с откатом на `assigned_teacher_id`, если заявку закрыли системно.
    Снимок делается ДО очистки полей закрытия, иначе атрибутировать будет нечем.

    Возвращает (data, error). error: None | "not_found" | "forbidden" |
    "not_closed" | "wrong_type".
    """
    await _lock_request(db, request_id)
    row = (
        await db.execute(
            text("""
                SELECT student_id, task_id, status, request_type, closed_by,
                       assigned_teacher_id
                FROM help_requests WHERE id = :id
            """),
            {"id": request_id},
        )
    ).fetchone()
    if row is None:
        return (None, "not_found")
    owner_id, task_id, status_val, request_type, closed_by, assigned = row
    if owner_id != student_id:
        return (None, "forbidden")
    if request_type != "manual_help":
        # Разбор и лимит попыток возвращать нечем: у первого своя оценка,
        # у второго — свой механизм закрытия.
        return (None, "wrong_type")
    if status_val != "closed":
        return (None, "not_closed")

    blamed_teacher_id = closed_by if closed_by is not None else assigned

    await db.execute(
        text("""
            UPDATE help_requests
            SET status = 'open', closed_at = NULL, closed_by = NULL,
                resolution_comment = NULL, updated_at = now()
            WHERE id = :id
        """),
        {"id": request_id},
    )
    # Поля закрытия чистятся намеренно: иначе открытая заявка несла бы дату
    # закрытия и комментарий об уже отвергнутом решении.
    await db.execute(
        text("""
            INSERT INTO help_request_reopens (request_id, teacher_id)
            VALUES (:request_id, :teacher_id)
        """),
        {"request_id": request_id, "teacher_id": blamed_teacher_id},
    )
    reopen_count = int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM help_request_reopens WHERE request_id = :id"),
                {"id": request_id},
            )
        ).scalar()
        or 0
    )

    if blamed_teacher_id is not None:
        await inbox_service.create_for_user(
            db,
            user_id=blamed_teacher_id,
            kind="help_request_reopened",
            title="Ученик вернул заявку: ответ не помог",
            content="Ученик отметил, что после ответа всё равно не разобрался.",
            payload={
                "request_id": request_id,
                "task_id": task_id,
                "student_id": student_id,
                "reopen_count": reopen_count,
            },
            created_by=student_id,
        )

    return (
        {
            "request_id": request_id,
            "status": "open",
            "reopen_count": reopen_count,
            "can_request_individual_review": True,
        },
        None,
    )


async def request_individual_review(
    db: AsyncSession,
    request_id: int,
    student_id: int,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """Уровень 2: ученик просит индивидуальный разбор.

    Доступно только после возврата заявки — то есть когда текстовый ответ уже
    подтверждённо не помог (решение оператора: «повторная заявка по тому же
    заданию»). Повторный вызов идемпотентен: заявка уже в нужном классе, и
    отвечать ошибкой на второй клик незачем.

    Возвращает (data, error). error: None | "not_found" | "forbidden" |
    "not_open" | "wrong_type" | "no_reopen".
    """
    await _lock_request(db, request_id)
    row = (
        await db.execute(
            text("""
                SELECT student_id, task_id, status, request_type, assigned_teacher_id
                FROM help_requests WHERE id = :id
            """),
            {"id": request_id},
        )
    ).fetchone()
    if row is None:
        return (None, "not_found")
    owner_id, task_id, status_val, request_type, assigned = row
    if owner_id != student_id:
        return (None, "forbidden")
    if status_val != "open":
        return (None, "not_open")
    if request_type == "individual_review":
        return (
            {"request_id": request_id, "request_type": request_type, "already": True},
            None,
        )
    if request_type != "manual_help":
        return (None, "wrong_type")
    if await _repeat_ask_count(db, request_id, student_id, task_id) < 1:
        return (None, "no_reopen")

    await db.execute(
        text("""
            UPDATE help_requests
            SET request_type = 'individual_review', updated_at = now()
            WHERE id = :id
        """),
        {"id": request_id},
    )
    if assigned is not None:
        await inbox_service.create_for_user(
            db,
            user_id=assigned,
            kind="individual_review_requested",
            title="Ученик просит индивидуальный разбор",
            content="Текстовый ответ не помог — ученик просит разбор в личной встрече.",
            payload={
                "request_id": request_id,
                "task_id": task_id,
                "student_id": student_id,
            },
            created_by=student_id,
        )

    return (
        {"request_id": request_id, "request_type": "individual_review", "already": False},
        None,
    )


async def rate_individual_review(
    db: AsyncSession,
    request_id: int,
    student_id: int,
    understood: bool,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """Уровень 3: оценка ученика после разбора.

    «Понятно» закрывает заявку, «непонятно» уводит её методисту. Оценка
    принимается один раз: она развилка маршрута, а не мнение, которое можно
    менять — второй ответ либо переоткрыл бы закрытую заявку, либо повторно
    дёрнул методистов.

    Возвращает (data, error). error: None | "not_found" | "forbidden" |
    "wrong_type" | "not_open" | "no_webinar_link" | "already_rated".
    """
    await _lock_request(db, request_id)
    row = (
        await db.execute(
            text("""
                SELECT student_id, task_id, course_id, status, request_type,
                       webinar_link, review_understood
                FROM help_requests WHERE id = :id
            """),
            {"id": request_id},
        )
    ).fetchone()
    if row is None:
        return (None, "not_found")
    owner_id, task_id, course_id, status_val, request_type, webinar_link, rated = row
    if owner_id != student_id:
        return (None, "forbidden")
    if request_type != "individual_review":
        return (None, "wrong_type")
    if status_val != "open":
        return (None, "not_open")
    if webinar_link is None:
        # Оценивать нечего: преподаватель ещё не прислал ссылку на разбор.
        return (None, "no_webinar_link")
    if rated is not None:
        return (None, "already_rated")

    await db.execute(
        text("UPDATE help_requests SET review_understood = :v, updated_at = now() WHERE id = :id"),
        {"v": understood, "id": request_id},
    )

    if understood:
        # Системное закрытие (`closed_by=None`), как в tsk-339: инициатор —
        # сам ученик, и уведомление «учитель закрыл ваш вопрос» тут было бы
        # неправдой. Кто и почему закрыл, видно по `review_understood`.
        await close_help_request(
            db,
            request_id,
            closed_by=None,
            resolution_comment="Ученик подтвердил: после разбора всё понятно",
        )
        return (
            {"request_id": request_id, "understood": True, "status": "closed", "escalated": False},
            None,
        )

    await db.execute(
        text(
            "UPDATE help_requests SET escalated_to_methodist_at = now(), updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": request_id},
    )
    escalated_count = await methodist_notify_service.escalate_help_request(
        db,
        request_id=request_id,
        student_id=student_id,
        task_id=task_id,
        course_id=course_id,
    )
    logger.info(
        "tsk-303: заявка %s ушла методисту после разбора (уведомлено: %s)",
        request_id,
        escalated_count,
    )
    return (
        {"request_id": request_id, "understood": False, "status": "open", "escalated": True},
        None,
    )


async def set_webinar_link(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
    webinar_link: str,
    lock_token: Optional[str] = None,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """Уровень 2, сторона преподавателя: прислать ссылку на разбор.

    Заявку НЕ закрывает: разбор ещё впереди, а закроет заявку оценка ученика
    после него (или методист, если разбор не помог).

    Ссылку преподаватель вводит вручную любым инструментом — интеграции с
    видео-сервисом здесь нет по решению оператора. Живёт она, пока заявка
    открыта: при закрытии обнуляется.

    Возвращает (data, error). error: None | "not_found" | "forbidden" |
    "not_open" | "wrong_type" | "lock_conflict".
    """
    await _lock_request(db, request_id)
    row = (
        await db.execute(
            text("""
                SELECT student_id, task_id, status, request_type
                FROM help_requests WHERE id = :id
            """),
            {"id": request_id},
        )
    ).fetchone()
    if row is None:
        return (None, "not_found")
    student_id, task_id, status_val, request_type = row

    err = await check_help_request_lock(db, request_id, teacher_id, lock_token)
    if err is not None:
        return (None, err)
    if not await can_access_help_request(db, request_id, teacher_id):
        return (None, "forbidden")
    if request_type != "individual_review":
        return (None, "wrong_type")
    if status_val != "open":
        return (None, "not_open")

    link = webinar_link.strip()
    await db.execute(
        text("UPDATE help_requests SET webinar_link = :l, updated_at = now() WHERE id = :id"),
        {"l": link, "id": request_id},
    )

    # Ссылка бесполезна, если ученик о ней не узнает: шлём и в переписку
    # (там же, где живут текстовые ответы), и в inbox — как это уже делает
    # ответ на заявку.
    messages_svc = MessagesService()
    thread_row = (
        await db.execute(
            text("SELECT thread_id FROM help_requests WHERE id = :id"), {"id": request_id}
        )
    ).fetchone()
    thread_id = thread_row[0] if thread_row is not None else None
    msg = await messages_svc.send_message(
        db,
        message_type="teacher_reply",
        content={"text": f"Приглашение на индивидуальный разбор: {link}"},
        recipient_id=student_id,
        sender_id=teacher_id,
        source_system="help_request_webinar_link",
        thread_id=thread_id,
    )
    await db.flush()
    if thread_id is None:
        await db.execute(
            text("UPDATE help_requests SET thread_id = :tid WHERE id = :id"),
            {"tid": msg.thread_id or msg.id, "id": request_id},
        )

    await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind="individual_review_scheduled",
        title="Преподаватель пригласил на разбор",
        content=f"Ссылка на разбор: {link}",
        payload={"request_id": request_id, "task_id": task_id, "webinar_link": link},
        created_by=teacher_id,
    )

    return ({"request_id": request_id, "webinar_link": link, "status": status_val}, None)


async def reply_help_request(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
    message: str,
    close_after_reply: bool = False,
    idempotency_key: Optional[str] = None,
    lock_token: Optional[str] = None,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Ответ на заявку: отправить сообщение студенту, записать reply, опционально закрыть.
    Возвращает (response_dict, error). error: None | "not_found" | "forbidden" | "closed" | "lock_conflict".
    response_dict: request_id, message_id, thread_id, request_status, deduplicated.
    """
    r = await db.execute(
        text("""
            SELECT id, student_id, status, thread_id, task_id, request_type
            FROM help_requests WHERE id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (None, "not_found")
    hid, student_id, req_status, thread_id, task_id = row[0], row[1], row[2], row[3], row[4]
    request_type = row[5] or "manual_help"

    if req_status == "closed":
        return (None, "closed")

    err = await check_help_request_lock(db, request_id, teacher_id, lock_token)
    if err is not None:
        # tsk-592: причина отказа доходит до клиента как есть. Раньше здесь
        # стоял литерал `lock_conflict`, и новый случай («заявку ведёт другой»)
        # объяснялся бы ученику словами про невалидный токен, которого он не
        # присылал.
        return (None, err)

    ok = await can_access_help_request(db, request_id, teacher_id)
    if not ok:
        return (None, "forbidden")

    if idempotency_key:
        r = await db.execute(
            text("""
                SELECT message_id FROM help_request_replies
                WHERE request_id = :request_id AND idempotency_key = :key LIMIT 1
            """),
            {"request_id": request_id, "key": idempotency_key},
        )
        dup = r.fetchone()
        if dup is not None:
            return ({
                "request_id": request_id,
                "message_id": dup[0],
                "thread_id": thread_id,
                "request_status": req_status,
                "deduplicated": True,
            }, None)

    messages_svc = MessagesService()
    body_trimmed = message[:4000] if len(message) > 4000 else message
    content: dict[str, str] = {"text": body_trimmed}

    msg = await messages_svc.send_message(
        db,
        message_type="teacher_reply",
        content=content,
        recipient_id=student_id,
        sender_id=teacher_id,
        source_system="help_request_reply",
        thread_id=thread_id,
    )
    await db.flush()
    new_thread_id = msg.thread_id or msg.id
    if thread_id is None:
        await db.execute(
            text("UPDATE help_requests SET thread_id = :tid, updated_at = now() WHERE id = :id"),
            {"tid": new_thread_id, "id": request_id},
        )
        await db.flush()
    thread_id = thread_id or new_thread_id

    key_val = idempotency_key[:128] if idempotency_key else None
    await db.execute(
        text("""
            INSERT INTO help_request_replies (request_id, teacher_id, message_id, body, close_after_reply, idempotency_key, created_at)
            VALUES (:request_id, :teacher_id, :message_id, :body, :close_after_reply, :idem_key, now())
        """),
        {
            "request_id": request_id,
            "teacher_id": teacher_id,
            "message_id": msg.id,
            "body": body_trimmed,
            "close_after_reply": close_after_reply,
            "idem_key": key_val,
        },
    )
    await record_help_request_replied(db, student_id, request_id, teacher_id, msg.id, thread_id)

    # tsk-303: текстовый ответ на заявку помощи ЗАКРЫВАЕТ её — это правило
    # (решение оператора), а не выбор клиента. Держим его на сервере, иначе
    # каждый клиент (веб, бот) решал бы по-своему и лестница помощи собиралась
    # бы из разных правил. Дальше ход за учеником: не помогло — «Вернуть
    # заявку», и возврат попадёт в KPI преподавателя.
    #
    # Только `manual_help`: у `individual_review` заявку закрывает оценка
    # ученика после разбора, у `blocked_limit` — своё закрытие при выдаче
    # лимита.
    force_close = request_type == "manual_help"
    final_status = req_status
    if close_after_reply or force_close:
        # Вторая точка закрытия заявки в этом файле (первая — `close_help_request`).
        # Делегировать туда нельзя: та шлёт ученику отдельное уведомление
        # «вопрос закрыт», и вместе с уже уходящим «учитель ответил» получилось
        # бы два пуша на одно действие. Поэтому обнуление вебинар-ссылки (TTL,
        # tsk-303) продублировано здесь осознанно — оба пути обязаны его делать.
        await db.execute(
            text("""
                UPDATE help_requests
                SET status = 'closed', closed_at = now(), closed_by = :closed_by,
                    updated_at = now(), webinar_link = NULL
                WHERE id = :id
            """),
            {"id": request_id, "closed_by": teacher_id},
        )
        await record_help_request_closed(db, student_id, request_id, teacher_id, None)
        final_status = "closed"

    # tsk-348: reply_help_request раньше отправлял только `messages`-запись
    # (видна ученику лишь если он сам откроет переписку — pull). Дублируем
    # в inbox (`notifications`), который уже опрашивает существующий
    # /me/notifications/unread-count + UnreadBadge в SPW — push без нового
    # эндпоинта/компонента на стороне ученика.
    await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind="help_request_replied",
        title="Учитель ответил на ваш вопрос",
        content=body_trimmed,
        payload={"request_id": request_id, "task_id": task_id, "thread_id": thread_id, "message_id": msg.id},
        created_by=teacher_id,
    )

    return ({
        "request_id": request_id,
        "message_id": msg.id,
        "thread_id": thread_id,
        "request_status": final_status,
        "deduplicated": False,
    }, None)
