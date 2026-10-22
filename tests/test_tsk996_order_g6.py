"""tsk-996, группа 6: места класса А, где порядок строк менял поведение.

Аудит вложений (владелец), очереди тиков оценки кода и эскалации (кто попал
в пачку под LIMIT), дедуп открытой помощи (какая заявка «уже открыта»),
ответы в промпт подсказки, «где встретилась» битая ссылка и последнее
сообщение диалога. Везде ключ сортировки допускает ничью, и без добивки
до `id` победителя ничьей решал план.

Приём: равный ключ у всех строк, id берутся заранее (`nextval`) и строки
вставляются НЕ по порядку id — так порядок и кучи, и индекса (равные ключи
в btree идут по месту в куче) расходится с порядком id. `SET id = id` здесь
не годится: это HOT-обновление, индекс его не замечает (см. `plan_order`).
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import pytest
from sqlalchemy import text

from app.services import attachment_audit_service as audit
from app.services import code_review_cron_service as code_cron
from app.services import escalation_service
from app.services import help_requests_service as hrs
from app.services import hint_drafts_service as hints
from app.services import link_audit_service as link_audit
from app.services import methodist_notify_service
from app.services.messages_service import MessagesService
from tests.plan_order import assert_same, run_under_plans

pytestmark = pytest.mark.asyncio

#: Строк с равным ключом.
_N = 7
#: Общий момент «одной пачки» в прошлом: раньше любых чужих строк очереди.
_OLD_TS = datetime(1990, 1, 1, 10, 0, tzinfo=timezone.utc)


# ── порядок вставки ──────────────────────────────────────────────────────────

async def _reserve_ids(db: Any, table: str, n: int) -> list[int]:
    """Взять `n` id из последовательности таблицы (по возрастанию)."""
    rows = await db.execute(
        text(
            "SELECT nextval(pg_get_serial_sequence(:t, 'id')) "
            "FROM generate_series(1, :n) ORDER BY 1"
        ),
        {"t": table, "n": n},
    )
    return [int(r[0]) for r in rows]


def _scrambled(ids: list[int]) -> list[int]:
    """Порядок вставки, где ни меньший, ни больший id не стоит с краю."""
    mid = len(ids) // 2
    return ids[1:mid] + [ids[0], ids[-1]] + ids[mid:-1]


async def _insert_in_order(
    db: Any, table: str, n: int, order: Callable[[list[int]], list[int]],
    insert: Callable[[int], Awaitable[None]],
) -> list[int]:
    """Вставить `n` строк с заранее взятыми id в порядке `order(ids)`."""
    ids = await _reserve_ids(db, table, n)
    for row_id in order(ids):
        await insert(row_id)
    await db.commit()
    return ids


def _reverse(ids: list[int]) -> list[int]:
    return list(reversed(ids))


def _natural(ids: list[int]) -> list[int]:
    return list(ids)


# ── фабрики ──────────────────────────────────────────────────────────────────

async def _user(db: Any, name: str, *, role: str | None = None) -> int:
    uid = (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES (:n, :e, true) RETURNING id"
            ),
            {"n": name, "e": f"t996g6-{random.randint(10**8, 10**10)}@example.com"},
        )
    ).scalar_one()
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": uid, "role": role},
        )
    return int(uid)


async def _course(db: Any) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"t996g6 курс {random.randint(10**6, 10**8)}"},
        )
    ).scalar_one()


async def _task(
    db: Any, course_id: int, *, typ: str = "SA", row_id: int | None = None,
    stem: str = "x",
) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (id, external_uid, max_score, task_content, solution_rules, "
                "course_id, difficulty_id, is_active) "
                "VALUES (COALESCE(:id, nextval(pg_get_serial_sequence('tasks', 'id'))), "
                "        :e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :course, "
                "        (SELECT MIN(id) FROM difficulties), true) RETURNING id"
            ),
            {
                "id": row_id,
                "e": f"t996g6-{random.randint(10**8, 10**10)}",
                "c": json.dumps({"type": typ, "stem": stem, "title": "задание"}),
                "r": json.dumps({"max_score": 10}),
                "course": course_id,
            },
        )
    ).scalar_one()


async def _result(
    db: Any, row_id: int, *, user_id: int, task_id: int, is_correct: bool | None,
    code_review: dict | None = None,
) -> None:
    """Работа ученика с общим `submitted_at` (`_OLD_TS`)."""
    await db.execute(
        text(
            "INSERT INTO task_results (id, score, user_id, task_id, submitted_at, count_retry, "
            " received_at, max_score, source_system, is_correct, code_review) "
            "VALUES (:id, 0, :u, :t, :ts, 0, :ts, 10, 'spw', :ic, CAST(:cr AS jsonb))"
        ),
        {
            "id": row_id, "u": user_id, "t": task_id, "ts": _OLD_TS, "ic": is_correct,
            "cr": json.dumps(code_review) if code_review is not None else None,
        },
    )


async def _open_request(
    db: Any, row_id: int, *, student_id: int, task_id: int | None = None,
    material_id: int | None = None, status: str = "open",
) -> None:
    """Заявка `manual_help`; `created_at = now()` — общий у всей транзакции."""
    await db.execute(
        text(
            "INSERT INTO help_requests (id, status, student_id, task_id, material_id, "
            " request_type, auto_created, context_json, priority, created_at, updated_at) "
            "VALUES (:id, :st, :s, :t, :m, 'manual_help', false, '{}'::jsonb, 100, "
            "        now(), now())"
        ),
        {"id": row_id, "st": status, "s": student_id, "t": task_id, "m": material_id},
    )


async def _message(
    db: Any, row_id: int, *, sender: int, recipient: int,
    attachment_id: str | None = None, sent_at: datetime | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO messages (id, message_type, content, sender_id, recipient_id, "
            " attachment_id, sent_at) "
            "VALUES (:id, 'text', '{\"text\": \"x\"}'::jsonb, :s, :r, :a, "
            "        COALESCE(:ts, now()))"
        ),
        {"id": row_id, "s": sender, "r": recipient, "a": attachment_id, "ts": sent_at},
    )


# ── 1–2: аудит вложений — владелец файла в уведомлении ───────────────────────

async def test_attachment_audit_message_owner_same_under_any_plan(db):
    """Один файл в нескольких сообщениях: владелец — сообщение с меньшим id."""
    a = await _user(db, "t996g6 a")
    b = await _user(db, "t996g6 b")
    name = f"996g6_{uuid.uuid4().hex}.png"
    ids = await _insert_in_order(
        db, "messages", _N, _reverse,
        lambda i: _message(db, i, sender=a, recipient=b, attachment_id=name),
    )

    async def owner() -> Any:
        return (await audit._collect_references(db))[(audit.attachment_storage.MESSAGES, name)]

    assert_same(await run_under_plans(db, owner), ("message", min(ids)))


async def test_attachment_audit_receipt_owner_same_under_any_plan(db):
    """Один чек у нескольких оплат: владелец — оплата с меньшим id."""
    student = await _user(db, "t996g6 ученик")
    name = f"996g6_{uuid.uuid4().hex}.pdf"

    async def insert(row_id: int) -> None:
        await db.execute(
            text(
                "INSERT INTO student_payment (id, student_id, amount_minor, method, purpose, "
                " status, receipt_file) "
                "VALUES (:id, :s, 100, 'manual', 'ai_package', 'pending', :f)"
            ),
            {"id": row_id, "s": student, "f": name},
        )

    ids = await _insert_in_order(db, "student_payment", _N, _reverse, insert)

    async def owner() -> Any:
        return (await audit._collect_references(db))[(audit.attachment_storage.RECEIPTS, name)]

    assert_same(await run_under_plans(db, owner), ("payment", min(ids)))


# ── 3–4: пачки тиков под LIMIT — кто попал в проход ──────────────────────────

async def test_code_review_tick_batch_same_under_any_plan(db):
    """Пачка оценки кода при равном `submitted_at`: младшие id, по порядку."""
    student = await _user(db, "t996g6 ученик")
    course = await _course(db)
    task = await _task(db, course)
    ids = await _insert_in_order(
        db, "task_results", _N, _reverse,
        lambda i: _result(
            db, i, user_id=student, task_id=task, is_correct=None,
            code_review={"status": "pending"},
        ),
    )

    async def batch() -> list[int]:
        rows = await db.execute(
            text(code_cron._PENDING_SQL), {"limit": 3, "claim_ttl_min": 10},
        )
        return [int(r[0]) for r in rows]

    assert_same(await run_under_plans(db, batch), ids[:3])


async def test_escalation_tick_batch_same_under_any_plan(db, db_session_factory, monkeypatch):
    """Эскалация берёт 100 старейших: при ничьей — 100 младших id."""
    student = await _user(db, "t996g6 ученик")
    course = await _course(db)
    task = await _task(db, course, typ="TA")
    ids = await _insert_in_order(
        db, "task_results", 103, _reverse,
        lambda i: _result(db, i, user_id=student, task_id=task, is_correct=True),
    )
    seen_ids: list[int] = []

    async def fake_escalate(_db: Any, *, result_id: int, **_: Any) -> int:
        seen_ids.append(result_id)
        return 0

    monkeypatch.setattr(methodist_notify_service, "escalate_pending_timeout", fake_escalate)

    async def tick() -> list[int]:
        seen_ids.clear()
        await escalation_service.escalation_cron_tick(session_factory=db_session_factory)
        return [i for i in seen_ids if i in set(ids)]

    assert_same(await run_under_plans(db, tick), ids[:100])


# ── 5–6: какая открытая заявка «уже есть» ────────────────────────────────────

async def test_material_help_dedupe_same_under_any_plan(db):
    """Несколько открытых заявок по материалу с равным `created_at`: берётся старший id."""
    student = await _user(db, "t996g6 ученик")
    course = await _course(db)
    material = (
        await db.execute(
            text(
                "INSERT INTO materials (course_id, type, content, title, is_active) "
                "VALUES (:c, 'text', '{}'::jsonb, 't996g6 материал', true) RETURNING id"
            ),
            {"c": course},
        )
    ).scalar_one()
    ids = await _insert_in_order(
        db, "help_requests", _N, _natural,
        lambda i: _open_request(db, i, student_id=student, material_id=material),
    )

    async def dedupe() -> tuple[int, bool]:
        return await hrs.get_or_create_material_help_request(
            db, student_id=student, material_id=material, course_id=course, message="?",
        )

    assert_same(await run_under_plans(db, dedupe), (max(ids), False))


async def test_oral_help_already_open_same_under_any_plan(db):
    """Устная заявка при открытых по заданию: «уже открыта» — старший id."""
    teacher = await _user(db, "t996g6 преподаватель")
    student = await _user(db, "t996g6 ученик")
    course = await _course(db)
    task = await _task(db, course)
    ids = await _insert_in_order(
        db, "help_requests", _N, _natural,
        lambda i: _open_request(db, i, student_id=student, task_id=task),
    )

    async def oral() -> Any:
        return await hrs.create_oral_help_request(
            db, teacher_id=teacher, student_id=student, task_id=task,
            course_id=course, note="устно", close=False,
        )

    assert_same(await run_under_plans(db, oral), ({"request_id": max(ids)}, "already_open"))


# ── 7: ответы преподавателей в промпт подсказки ──────────────────────────────

async def test_hint_source_replies_same_under_any_plan(db):
    """Ответы с равным `created_at`: новые id первыми — граница REPLIES_LIMIT стабильна."""
    teacher = await _user(db, "t996g6 преподаватель")
    student = await _user(db, "t996g6 ученик")
    course = await _course(db)
    task = await _task(db, course)
    request = (await _reserve_ids(db, "help_requests", 1))[0]
    await _open_request(db, request, student_id=student, task_id=task, status="closed")
    body = "Распиши разряды справа налево и подумай, какие степени двойки стоят под единицами."

    async def insert(row_id: int) -> None:
        mid = (
            await db.execute(
                text(
                    "INSERT INTO messages (message_type, content, sender_id, recipient_id) "
                    "VALUES ('help_reply', '{}'::jsonb, :t, :s) RETURNING id"
                ),
                {"t": teacher, "s": student},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO help_request_replies (id, request_id, teacher_id, message_id, "
                " body, reply_kind, created_at) "
                "VALUES (:id, :r, :t, :m, :b, 'text', :ts)"
            ),
            {"id": row_id, "r": request, "t": teacher, "m": mid, "b": body, "ts": _OLD_TS},
        )

    ids = await _insert_in_order(
        db, "help_request_replies", hints.REPLIES_LIMIT + 3, _scrambled, insert,
    )

    async def replies() -> list[int]:
        return [r.reply_id for r in await hints.source_replies(db, task)]

    assert_same(await run_under_plans(db, replies), sorted(ids, reverse=True))


# ── 8: битая ссылка — «где встретилась» ──────────────────────────────────────

async def test_link_audit_owners_same_under_any_plan(db, db_session_factory, monkeypatch):
    """Одна ссылка в нескольких материалах и заданиях: материалы, затем задания, по id."""
    course = await _course(db)
    file_id = f"t996g6-{uuid.uuid4().hex}.png"
    body = f"см. /api/v1/materials/files/{file_id}"

    async def material(row_id: int) -> None:
        await db.execute(
            text(
                "INSERT INTO materials (id, course_id, type, content, title, is_active) "
                "VALUES (:id, :c, 'text', CAST(:b AS jsonb), 't996g6', true)"
            ),
            {"id": row_id, "c": course, "b": json.dumps({"text": body})},
        )

    async def task(row_id: int) -> None:
        await _task(db, course, row_id=row_id, stem=body)

    m_ids = await _insert_in_order(db, "materials", _N, _scrambled, material)
    t_ids = await _insert_in_order(db, "tasks", _N, _scrambled, task)
    target = f"material:{file_id}"
    captured: list[Any] = []
    orig_collect = link_audit._collect

    def spy(rows: Any, own_hosts: Any) -> Any:
        found = orig_collect(rows, own_hosts)
        captured.append(found.get(target))
        return found

    async def nothing_broken(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(link_audit, "_collect", spy)
    monkeypatch.setattr(link_audit, "_check_target", nothing_broken)

    async def owners() -> Any:
        captured.clear()
        await link_audit.link_audit_tick(session_factory=db_session_factory)
        return captured[0]

    expected = [("material", i) for i in m_ids] + [("task", i) for i in t_ids]
    assert_same(await run_under_plans(db, owners), expected)


# ── 9: последнее сообщение диалога ───────────────────────────────────────────

async def test_inbox_last_message_same_under_any_plan(db):
    """Сообщения диалога с равным `sent_at`: последним считается старший id."""
    me = await _user(db, "t996g6 я")
    peer = await _user(db, "t996g6 собеседник")
    ids = await _insert_in_order(
        db, "messages", _N, _scrambled,
        lambda i: _message(db, i, sender=peer, recipient=me, sent_at=_OLD_TS),
    )

    async def last() -> Any:
        rows = await MessagesService().get_inbox(db, user_id=me)
        return [(r["peer_id"], r["last_message"]["id"]) for r in rows]

    assert_same(await run_under_plans(db, last), [(peer, max(ids))])
