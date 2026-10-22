"""tsk-996, группа 3: выбор и порядок не зависят от плана запроса.

Очереди преподавателя, заявки помощи, аудит вложений, «буксует на задании»,
ленты и списки с пагинацией. Везде, где ключ сортировки допускает ничью
(одинаковый `now()` у строк одной транзакции), раньше победителя ничьей решал
план PostgreSQL. Приём тот же, что в `test_tsk996_node_sequence.py`: равные
значения ключа, строка с меньшим id — в конце кучи (`move_to_heap_end`), вызов
под всеми планами (`run_under_plans`), результат один и равен ожидаемому.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

from app.auth.current_user import CurrentUser
from app.models.users import Users
from app.services import attachment_audit_service as audit
from app.services import help_requests_service as hrs
from app.services import inbox_service
from app.services import teacher_activity_feed_service as feed
from app.services import teacher_queue_service as queue
from app.services.stuck_tasks_service import load_stuck_tasks
from tests.plan_order import assert_same, move_to_heap_end, run_under_plans

pytestmark = pytest.mark.asyncio

#: Строк с равным ключом: с семью строками порядок хеша/кучи совпал бы
#: с порядком id лишь случайно.
_N = 7
#: Общий момент «одной пачки» — у всех строк ключ сортировки одинаковый.
_TS = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


# ── фабрики ──────────────────────────────────────────────────────────────────

async def _user(db: Any, name: str, *, role: str | None = None) -> int:
    u = Users(
        email=f"t996g3-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=name, tg_id=None,
    )
    db.add(u)
    await db.flush()
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    return u.id


async def _course(db: Any) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"t996g3 курс {random.randint(10**6, 10**8)}"},
        )
    ).scalar_one()


async def _task(db: Any, course_id: int, *, typ: str = "SA") -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
                "course_id, difficulty_id) "
                "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :course, "
                "        (SELECT MIN(id) FROM difficulties)) RETURNING id"
            ),
            {
                "e": f"t996g3-{random.randint(10**8, 10**10)}",
                "c": json.dumps({"type": typ, "stem": "x", "title": "задание"}),
                "r": json.dumps({"max_score": 10}),
                "course": course_id,
            },
        )
    ).scalar_one()


async def _result(
    db: Any, *, user_id: int, task_id: int, is_correct: bool | None,
    answer: dict | None = None, attempt_id: int | None = None,
) -> int:
    """Работа ученика с общим `submitted_at` (`_TS`)."""
    return (
        await db.execute(
            text(
                "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
                " received_at, max_score, source_system, is_correct, answer_json, attempt_id) "
                "VALUES (0, :u, :t, :ts, 0, :ts, 10, 'spw', :ic, CAST(:a AS jsonb), :att) "
                "RETURNING id"
            ),
            {
                "u": user_id, "t": task_id, "ts": _TS, "ic": is_correct,
                "a": json.dumps(answer) if answer is not None else None, "att": attempt_id,
            },
        )
    ).scalar_one()


async def _open_request(
    db: Any, *, student_id: int, course_id: int | None = None, teacher_id: int | None = None,
    event_id: int | None = None,
) -> int:
    """Открытая заявка: приоритет, срок и `created_at` — как у всех соседей."""
    return (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, course_id, request_type, "
                " assigned_teacher_id, auto_created, context_json, priority, event_id, "
                " created_at, updated_at, due_at) "
                "VALUES ('open', :s, :c, 'manual_help', :t, false, '{}'::jsonb, 100, :e, "
                "        now(), now(), NULL) RETURNING id"
            ),
            {"s": student_id, "c": course_id, "t": teacher_id, "e": event_id},
        )
    ).scalar_one()


async def _to_heap_end(db: Any, table: str, ids: list[int]) -> None:
    """Меньший id — в конец кучи: чтение кучи отдаст его последним."""
    await move_to_heap_end(db, table, min(ids))


async def _reverse_results_heap(db: Any, ids: list[int]) -> None:
    """Работы в обратном порядке id — и в куче, и в индексе по `submitted_at`.

    `move_to_heap_end` (`SET id = id`) — HOT-обновление: индекс продолжает
    указывать на старое место строки, и чтение по индексу равных ключей идёт
    по-прежнему по id. Здесь индексируемая колонка меняется и возвращается —
    обновление не HOT, строка получает новую запись в индексе, как на живой
    базе после правки работы.
    """
    for row_id in sorted(ids, reverse=True):
        for shift in ("+", "-"):
            await db.execute(
                text(
                    f"UPDATE task_results SET submitted_at = submitted_at {shift} "
                    "interval '1 second' WHERE id = :id"
                ),
                {"id": row_id},
            )


# ── А: очереди преподавателя — кому достаётся работа ─────────────────────────

async def test_claim_next_help_request_same_under_any_plan(db):
    """claim-next заявки: при полной ничьей выдаётся старшая по id — при любом плане."""
    methodist = await _user(db, "t996g3 методист", role="methodist")
    student = await _user(db, "t996g3 ученик")
    course = await _course(db)
    ids = [await _open_request(db, student_id=student, course_id=course) for _ in range(_N)]
    await _to_heap_end(db, "help_requests", ids)
    await db.commit()

    async def claim() -> int | None:
        item, _, _ = await queue.claim_next_help_request(
            db, teacher_id=methodist, course_id=course,
        )
        # Захват снимается, чтобы следующий план видел ту же очередь.
        await db.execute(
            text(
                "UPDATE help_requests SET claimed_by = NULL, claim_token = NULL, "
                "claim_expires_at = NULL WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        return item["request_id"] if item else None

    assert_same(await run_under_plans(db, claim), min(ids))


async def test_claim_next_review_same_under_any_plan(db):
    """claim-next проверки: при равном `submitted_at` выдаётся работа с меньшим id."""
    methodist = await _user(db, "t996g3 методист", role="methodist")
    student = await _user(db, "t996g3 ученик")
    course = await _course(db)
    task = await _task(db, course, typ="TA")
    ids = [
        await _result(db, user_id=student, task_id=task, is_correct=True) for _ in range(_N)
    ]
    await _reverse_results_heap(db, ids)
    await db.commit()

    async def claim() -> int | None:
        item, _, _ = await queue.claim_next_review(db, teacher_id=methodist, course_id=course)
        await db.execute(
            text(
                "UPDATE task_results SET review_claimed_by = NULL, review_claim_token = NULL, "
                "review_claim_expires_at = NULL WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        return item["id"] if item else None

    assert_same(await run_under_plans(db, claim), min(ids))


# ── А: какую заявку обновить ─────────────────────────────────────────────────

async def test_help_request_event_dedupe_same_under_any_plan(db):
    """Две заявки по одному событию (гонка): дедуп возвращает меньший id при любом плане."""
    student = await _user(db, "t996g3 ученик")
    event = (
        await db.execute(
            text(
                "INSERT INTO learning_events (student_id, event_type, payload) "
                "VALUES (:s, 'help_requested', '{}'::jsonb) RETURNING id"
            ),
            {"s": student},
        )
    ).scalar_one()
    ids = [await _open_request(db, student_id=student, event_id=event) for _ in range(_N)]
    await _to_heap_end(db, "help_requests", ids)
    await db.commit()

    async def dedupe() -> tuple[int, bool]:
        return await hrs.get_or_create_help_request(
            db, student_id=student, task_id=0, event_id=event, deduplicated=True,
        )

    assert_same(await run_under_plans(db, dedupe), (min(ids), False))


# ── А: аудит вложений — владелец в уведомлении ───────────────────────────────

async def test_attachment_audit_owner_same_under_any_plan(db):
    """Одно вложение в нескольких работах: владелец — работа с меньшим id."""
    student = await _user(db, "t996g3 ученик")
    course = await _course(db)
    task = await _task(db, course)
    name = f"996_{uuid.uuid4().hex}_scan.png"
    answer = {"response": {"meta": {"attachments": [{"attachment_id": name}]}}}
    ids = [
        await _result(db, user_id=student, task_id=task, is_correct=None, answer=answer)
        for _ in range(_N)
    ]
    await _to_heap_end(db, "task_results", ids)
    await db.commit()

    async def owner() -> Any:
        return (await audit._collect_references(db))[(audit.attachment_storage.ATTEMPTS, name)]

    assert_same(await run_under_plans(db, owner), ("task_result", min(ids)))


# ── А: «буксует на задании» — какое задание названо ──────────────────────────

async def test_stuck_tasks_named_task_same_under_any_plan(db):
    """Равный last_at у заданий ученика: первым идёт задание с большим id (как `DESC`)."""
    student = await _user(db, "t996g3 ученик")
    course = await _course(db)
    tasks = [await _task(db, course) for _ in range(_N)]
    for task in tasks:
        for _ in range(3):
            await _result(db, user_id=student, task_id=task, is_correct=False)
    await move_to_heap_end(db, "tasks", max(tasks))
    await db.commit()

    async def named() -> list[int]:
        res = await load_stuck_tasks(
            db, student_ids=[student],
            since=_TS - timedelta(hours=1), until=_TS + timedelta(hours=1),
        )
        return [item["task_id"] for item in res[student]]

    assert_same(await run_under_plans(db, named), sorted(tasks, reverse=True))


# ── Б: списки с пагинацией — страницы не пересекаются ────────────────────────

async def _pages(fetch: Any, total: int, size: int = 3) -> list[int]:
    """Склейка страниц размера `size`: каждая строка ровно один раз."""
    out: list[int] = []
    for offset in range(0, total, size):
        out.extend(await fetch(size, offset))
    return out


async def test_help_requests_list_pages_same_under_any_plan(db):
    """Очередь заявок преподавателя: страницы при ничьей идут по id без дублей."""
    teacher = await _user(db, "t996g3 преподаватель")
    student = await _user(db, "t996g3 ученик")
    ids = [await _open_request(db, student_id=student, teacher_id=teacher) for _ in range(_N)]
    await _to_heap_end(db, "help_requests", ids)
    await db.commit()

    for sort in ("priority", "created_at", "due_at"):
        async def fetch(limit: int, offset: int, sort: str = sort) -> list[int]:
            items, _ = await hrs.list_help_requests(
                db, teacher, limit=limit, offset=offset, sort=sort,
            )
            return [it["request_id"] for it in items]

        seen = await run_under_plans(db, lambda: _pages(fetch, _N, size=4))
        assert_same(seen, sorted(ids))


async def test_pending_reviews_pages_same_under_any_plan(db):
    """Очередь проверки: страницы при равном `submitted_at` идут по id без дублей."""
    methodist = await _user(db, "t996g3 методист", role="methodist")
    student = await _user(db, "t996g3 ученик")
    course = await _course(db)
    task = await _task(db, course, typ="TA")
    ids = [
        await _result(db, user_id=student, task_id=task, is_correct=True) for _ in range(_N)
    ]
    await _reverse_results_heap(db, ids)
    await db.commit()

    async def fetch(limit: int, offset: int) -> list[int]:
        items, _ = await queue.list_pending_reviews(
            db, methodist, course_id=course, limit=limit, offset=offset,
        )
        return [it["id"] for it in items]

    assert_same(await run_under_plans(db, lambda: _pages(fetch, _N)), sorted(ids))


async def test_inbox_pages_same_under_any_plan(db):
    """Уведомления одной рассылки (равный `modified_at`): новые id сверху, без дублей."""
    user = await _user(db, "t996g3 получатель")
    ids = [
        (
            await db.execute(
                text(
                    "INSERT INTO notifications (content, user_id, kind, title, payload, "
                    " modified_at) VALUES ('x', :u, 'info', 't', '{}'::jsonb, now()) "
                    "RETURNING id"
                ),
                {"u": user},
            )
        ).scalar_one()
        for _ in range(_N)
    ]
    await move_to_heap_end(db, "notifications", max(ids))
    await db.commit()

    async def fetch(limit: int, offset: int) -> list[int]:
        items = await inbox_service.list_for_user(
            db, user_id=user, limit=limit, offset=offset, unread_only=False,
        )
        return [int(it.id) for it in items]

    assert_same(await run_under_plans(db, lambda: _pages(fetch, _N)), sorted(ids, reverse=True))


async def test_activity_feed_top_same_under_any_plan(db):
    """Лента преподавателя: при равном времени в срез LIMIT попадают старшие id."""
    student = await _user(db, "t996g3 ученик")
    course = await _course(db)
    tasks = [await _task(db, course) for _ in range(_N)]
    attempt = (
        await db.execute(
            text("INSERT INTO attempts (user_id) VALUES (:u) RETURNING id"), {"u": student}
        )
    ).scalar_one()
    for task in tasks:
        await _result(db, user_id=student, task_id=task, is_correct=True, attempt_id=attempt)
    await move_to_heap_end(db, "tasks", max(tasks))
    await db.commit()
    service = CurrentUser(id=0, is_service=True)

    async def top() -> list[int]:
        page, _, _, _ = await feed.get_activity_feed(db, service, limit=3, student_id=student)
        return [ev["task_id"] for ev in page if ev["type"] == "task_solved"]

    # task_results вставлены по возрастанию task_id: больший id работы — больший task_id.
    assert_same(await run_under_plans(db, top), sorted(tasks, reverse=True)[:3])
