"""tsk-995 (П6): срок ответа на заявку, счётчик «Просрочено», скорость проверки.

Что держат тесты:

1. Каждый путь создания ОТКРЫТОЙ заявки ставит `due_at = created_at + HELP_SLA_HOURS`
   (вопрос по заданию, вопрос по материалу, лимит попыток), а возврат заявки и
   перевод в индивидуальный разбор — новый срок от этого момента. Сразу
   закрытая заявка автопродления срока не получает.
2. `overdue_total` = просроченные заявки + просроченные обязательные работы и
   не равен 0 на данных с просрочкой; заявка, на которую преподаватель уже
   ответил, просроченной не считается; фильтр `overdue=1` совпадает с ячейкой.
3. Скорость проверки работ: только сдачи ученика (`spw_web`), без ручного
   зачёта, повтора и итогового среза; у методиста — по школе.
4. Миграция `tsk995_help_due_backfill`: срок от момента миграции, откат
   обнуляет только заполненные ею строки.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services import help_requests_service as hrs
from app.services import help_sla
from app.services import review_sla
from app.services.attempt_auto_extend_service import try_auto_extend
from app.services.learning_events_service import record_help_requested
from app.services.teacher_dashboard_service import get_review_speed
from app.services.teacher_queue_service import get_teacher_workload

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _svc() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


# ── фабрики ──────────────────────────────────────────────────────────────────

async def _user(db, name: str, *, role: str | None = None) -> int:
    u = Users(
        email=f"t995-{random.randint(10**8, 10**10)}@example.com",
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


async def _course(db) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"t995 курс {random.randint(10**6, 10**8)}"},
        )
    ).scalar_one()


async def _task(db, course_id: int, *, typ: str = "SA", rules: dict | None = None) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
                "course_id, difficulty_id, max_attempts) "
                "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :course, "
                "        (SELECT MIN(id) FROM difficulties), NULL) RETURNING id"
            ),
            {
                "e": f"t995-{random.randint(10**8, 10**10)}",
                "c": json.dumps({"type": typ, "stem": "x"}),
                "r": json.dumps(rules or {"max_score": 10}),
                "course": course_id,
            },
        )
    ).scalar_one()


async def _link(db, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )


async def _hr(db, rid: int) -> dict:
    row = (
        await db.execute(
            text(
                "SELECT status, request_type, created_at, due_at, "
                "       now() AS tx_now FROM help_requests WHERE id = :id"
            ),
            {"id": rid},
        )
    ).mappings().one()
    return dict(row)


async def _open_request(db, *, student_id: int, task_id: int, teacher_id: int,
                        due_shift: str | None, request_type: str = "manual_help") -> int:
    """Открытая заявка с заданным сдвигом срока от now() (`None` — без срока)."""
    due_sql = "NULL" if due_shift is None else f"now() + interval '{due_shift}'"
    return (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "assigned_teacher_id, auto_created, context_json, priority, "
                "created_at, updated_at, due_at) "
                "VALUES ('open', :s, :t, :rt, :at, false, '{}'::jsonb, 100, "
                f"        now() - interval '2 days', now(), {due_sql}) RETURNING id"
            ),
            {"s": student_id, "t": task_id, "rt": request_type, "at": teacher_id},
        )
    ).scalar_one()


def _sla() -> timedelta:
    return timedelta(hours=help_sla.help_sla_hours())


# ── 1. срок во всех путях создания ───────────────────────────────────────────

def test_help_due_at_helper_matches_setting():
    """`help_due_at` = момент + HELP_SLA_HOURS; naive трактуется как UTC; мусор → None."""
    moment = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    assert help_sla.help_due_at(moment) == moment + _sla()
    assert help_sla.help_due_at(datetime(2026, 10, 17, 10, 0)) == moment + _sla()
    assert help_sla.help_due_at(None) is None
    assert help_sla.help_due_at("2026-10-17") is None  # type: ignore[arg-type]


def test_help_sla_hours_default_is_24(monkeypatch):
    """Решение оператора Р1: по умолчанию 24 часа, число — из HELP_SLA_HOURS."""
    monkeypatch.delenv("HELP_SLA_HOURS", raising=False)
    assert help_sla.help_sla_hours() == 24
    monkeypatch.setenv("HELP_SLA_HOURS", "6")
    assert help_sla.help_sla_hours() == 6


async def test_manual_help_by_task_gets_due_at(db):
    """Путь 1: вопрос по заданию (`get_or_create_help_request`)."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    task = await _task(db, course)
    event_id, dedup = await record_help_requested(db, student, task, "не понимаю")
    rid, created = await hrs.get_or_create_help_request(
        db, student_id=student, task_id=task, event_id=event_id,
        message="не понимаю", course_id=course, deduplicated=dedup,
    )
    assert created
    row = await _hr(db, rid)
    assert row["status"] == "open"
    assert row["due_at"] == row["created_at"] + _sla()


async def test_manual_help_by_material_gets_due_at(db):
    """Путь 2: вопрос «Я не понял» по материалу."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    material = (
        await db.execute(
            text(
                "INSERT INTO materials (course_id, type, content, title) "
                "VALUES (:c, 'text', '{\"text\": \"x\"}'::jsonb, 't995') RETURNING id"
            ),
            {"c": course},
        )
    ).scalar_one()
    rid, created = await hrs.get_or_create_material_help_request(
        db, student_id=student, material_id=material, course_id=course, message="непонятно",
    )
    assert created
    row = await _hr(db, rid)
    assert row["due_at"] == row["created_at"] + _sla()


async def test_blocked_limit_gets_due_at(db):
    """Путь 3: лимит попыток (`get_or_create_blocked_limit_help_request`)."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    task = await _task(db, course)
    rid, created, _ = await hrs.get_or_create_blocked_limit_help_request(
        db, student_id=student, task_id=task, course_id=course,
        attempts_used=3, attempts_limit_effective=3,
    )
    assert created
    row = await _hr(db, rid)
    assert row["request_type"] == "blocked_limit"
    assert row["due_at"] == row["created_at"] + _sla()


async def test_auto_extend_closed_trace_has_no_due_at(db):
    """Сразу закрытая заявка-след автопродления срока не получает."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    task = await _task(db, course)
    assert await try_auto_extend(
        db, student_id=student, task_id=task, course_id=None,
        attempt_id=None, attempts_used=3, attempts_limit_effective=3,
    ) is not None
    rows = (
        await db.execute(
            text("SELECT status, due_at FROM help_requests WHERE student_id = :s"),
            {"s": student},
        )
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("closed", None)]


async def test_reopen_and_individual_review_restart_due_at(db):
    """Путь 4: возврат заявки и перевод в индивидуальный разбор — новый срок от now()."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель")
    task = await _task(db, course)
    rid = (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "assigned_teacher_id, auto_created, context_json, created_at, updated_at, "
                "closed_at, closed_by, due_at) "
                "VALUES ('closed', :s, :t, 'manual_help', :at, false, '{}'::jsonb, "
                "        now() - interval '3 days', now(), now(), :at, "
                "        now() - interval '2 days') RETURNING id"
            ),
            {"s": student, "t": task, "at": teacher},
        )
    ).scalar_one()

    data, err = await hrs.reopen_help_request(db, rid, student)
    assert err is None, err
    row = await _hr(db, rid)
    assert row["status"] == "open"
    assert row["due_at"] == row["tx_now"] + _sla()

    data, err = await hrs.request_individual_review(db, rid, student)
    assert err is None, err
    row = await _hr(db, rid)
    assert row["request_type"] == "individual_review"
    assert row["due_at"] == row["tx_now"] + _sla()


# ── 2. «Просрочено» = заявки + работы ────────────────────────────────────────

async def _pending_review(db, *, student: int, task: int, submitted_shift: str) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO task_results (score, user_id, task_id, submitted_at, "
                "max_score, source_system) "
                f"VALUES (0, :u, :t, now() - interval '{submitted_shift}', 10, 'spw_web') "
                "RETURNING id"
            ),
            {"u": student, "t": task},
        )
    ).scalar_one()


async def test_workload_overdue_counts_help_and_reviews(db, client):
    """overdue_total = просроченные заявки + просроченные обязательные работы, не 0."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель", role="teacher")
    await db.execute(
        text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (:t, :c)"),
        {"t": teacher, "c": course},
    )
    task = await _task(db, course)
    ta_task = await _task(db, course, typ="TA")

    overdue_rid = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="-1 hour")
    await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="+1 hour")
    # Ответили, ученик не закрыл: срок прошёл, но ход за учеником — не просрочка.
    answered_rid = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="-1 hour",
        request_type="blocked_limit")
    _, err = await hrs.reply_help_request(db, answered_rid, teacher, "Продлил лимит")
    assert err is None, err
    # Разбор без ссылки: ждёт преподавателя, даже если последним писал он.
    review_rid = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="-1 hour",
        request_type="individual_review")
    _, err = await hrs.reply_help_request(db, review_rid, teacher, "Скоро пришлю ссылку")
    assert err is None, err

    sla_h = review_sla.sla_hours()
    await _pending_review(db, student=student, task=ta_task, submitted_shift=f"{sla_h + 1} hours")
    await _pending_review(db, student=student, task=ta_task, submitted_shift="1 hour")

    data = await get_teacher_workload(db, teacher)
    assert data["overdue_help"] == 2, data
    assert data["overdue_reviews"] == 1, data
    assert data["overdue_total"] == 3
    assert data["pending_manual_reviews_total"] == 2

    # Фильтр списка совпадает с ячейкой.
    resp = await client.get(
        f"/api/v1/teacher/help-requests?teacher_id={teacher}&status=open&overdue=1&limit=100",
        headers=_svc(),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert {it["request_id"] for it in items} == {overdue_rid, review_rid}
    assert all(it["is_overdue"] for it in items)

    resp = await client.get(
        f"/api/v1/teacher/help-requests/{answered_rid}?teacher_id={teacher}", headers=_svc(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_overdue"] is False

    resp = await client.get(f"/api/v1/teacher/workload?teacher_id={teacher}", headers=_svc())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["overdue_total"], body["overdue_help"], body["overdue_reviews"]) == (3, 2, 1)
    assert body["review_speed"]["scope"] == "teacher"


async def test_closed_request_with_past_due_is_not_overdue(db, client):
    """Закрытая заявка с прошедшим сроком — не просрочка (и в списке status=all)."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель")
    task = await _task(db, course)
    rid = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="-1 hour")
    await hrs.close_help_request(db, rid, closed_by=teacher)
    data = await get_teacher_workload(db, teacher)
    assert data["overdue_help"] == 0
    resp = await client.get(
        f"/api/v1/teacher/help-requests?teacher_id={teacher}&status=all&limit=100",
        headers=_svc(),
    )
    assert resp.status_code == 200, resp.text
    item = next(it for it in resp.json()["items"] if it["request_id"] == rid)
    assert item["is_overdue"] is False


# ── 3. скорость проверки ─────────────────────────────────────────────────────

async def _checked(db, *, student: int, task: int, checker: int, hours: float,
                   source: str = "spw_web", days_ago: int = 1) -> None:
    await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, checked_at, "
            "checked_by, max_score, source_system) "
            "VALUES (10, :u, :t, now() - make_interval(days => :d) - make_interval(secs => :sec), "
            "        now() - make_interval(days => :d), :ch, 10, :src)"
        ),
        {"u": student, "t": task, "d": days_ago, "sec": hours * 3600, "ch": checker, "src": source},
    )


async def test_review_speed_counts_only_student_submissions(db):
    """Медиана/перцентиль/доля в срок — только по сдачам ученика за 30 дней."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель", role="teacher")
    other = await _user(db, "t995 другой учитель", role="teacher")
    ta_task = await _task(db, course, typ="TA")
    sa_auto = await _task(db, course, typ="SA")  # не обязательная проверка

    sla_h = review_sla.sla_hours()
    # 10 сдач ученика: 1..9 ч и одна позже срока.
    for h in [1, 2, 3, 4, 5, 6, 7, 8, 9]:
        await _checked(db, student=student, task=ta_task, checker=teacher, hours=h)
    await _checked(db, student=student, task=ta_task, checker=teacher, hours=sla_h + 10)
    # Не входят: ручной зачёт, повтор, итоговый срез, служебный источник,
    # необязательная проверка, проверка старше окна, переоценённая работа.
    for src in ("manual_teacher", "retrieval", "final_check", "system"):
        await _checked(db, student=student, task=ta_task, checker=teacher, hours=500, source=src)
    await _checked(db, student=student, task=sa_auto, checker=teacher, hours=500)
    await _checked(db, student=student, task=ta_task, checker=teacher, hours=500, days_ago=40)
    await _checked(db, student=student, task=ta_task, checker=teacher, hours=500)
    await db.execute(
        text(
            "UPDATE task_results SET metrics = '{\"regrade_history\": [{\"by\": 1}]}'::jsonb "
            "WHERE id = (SELECT MAX(id) FROM task_results WHERE user_id = :u)"
        ),
        {"u": student},
    )
    # Чужая проверка — не у этого преподавателя, но в школе.
    await _checked(db, student=student, task=ta_task, checker=other, hours=1)

    mine = await get_review_speed(db, checker_id=teacher)
    assert mine["scope"] == "teacher"
    assert mine["checked"] == 10
    assert mine["within_sla"] == 9
    assert mine["within_sla_rate"] == 0.9
    assert mine["median_hours"] == 5.5
    assert mine["p90_hours"] is not None and 9 <= mine["p90_hours"] <= sla_h + 10
    assert mine["sla_hours"] == sla_h

    school = await get_review_speed(db, checker_id=None)
    assert school["scope"] == "school"
    assert school["checked"] >= 11


async def test_review_speed_small_sample_is_null(db):
    """Меньше 10 проверок — медиана и доля `None` («мало данных»), число есть."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель", role="teacher")
    ta_task = await _task(db, course, typ="TA")
    await _checked(db, student=student, task=ta_task, checker=teacher, hours=2)
    speed = await get_review_speed(db, checker_id=teacher)
    assert speed["checked"] == 1
    assert speed["median_hours"] is None and speed["within_sla_rate"] is None


async def test_methodist_workload_speed_is_school_scope(db):
    """Методист видит скорость по школе (та же ACL-логика bypass, что у счётчиков)."""
    methodist = await _user(db, "t995 методист", role="methodist")
    data = await get_teacher_workload(db, methodist)
    assert data["review_speed"]["scope"] == "school"


# ── 4. миграция данных ──────────────────────────────────────────────────────

def _load_migration():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app/db/migrations/versions/2026_10_17_tsk995_help_due_backfill.py"
    )
    spec = importlib.util.spec_from_file_location("tsk995_help_due_backfill", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    """Миграция встаёт за головой tsk-1361 и заполняет срок от момента миграции."""
    mod = _load_migration()
    assert mod.revision == "tsk995_help_due_backfill"
    assert mod.down_revision == "tsk1361_requirement_sync"
    src = (mod.__doc__ or "") + open(mod.__file__, encoding="utf-8").read()
    assert "GREATEST(created_at, now())" in src
    assert "WHERE due_at IS NULL AND status = 'open'" in src


async def test_backfill_sql_sets_due_from_now_and_downgrade_restores(db):
    """SQL миграции на живых строках: срок от now(), откат — только своих строк."""
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель")
    task = await _task(db, course)
    old_open = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift=None)
    has_due = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="+5 hours")
    closed = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift=None)
    await db.execute(
        text("UPDATE help_requests SET status='closed', closed_at=now() WHERE id=:id"),
        {"id": closed},
    )
    before_has_due = (await _hr(db, has_due))["due_at"]

    # Таблица-маркер есть после upgrade head; строки старых заявок вставляем как миграция.
    await db.execute(text("SAVEPOINT t995"))
    await db.execute(
        text(
            """
            WITH upd AS (
                UPDATE help_requests
                   SET due_at = GREATEST(created_at, now()) + make_interval(hours => :hours)
                 WHERE due_at IS NULL AND status = 'open' AND student_id = :s
             RETURNING id, due_at
            )
            INSERT INTO help_request_due_backfill (request_id, due_at)
            SELECT id, due_at FROM upd
            """
        ),
        {"hours": help_sla.help_sla_hours(), "s": student},
    )
    row = await _hr(db, old_open)
    assert row["due_at"] == row["tx_now"] + _sla(), "срок от момента миграции, не от created_at"
    assert (await _hr(db, has_due))["due_at"] == before_has_due
    assert (await _hr(db, closed))["due_at"] is None
    # Старая заявка сразу после миграции не просрочена.
    assert (await get_teacher_workload(db, teacher))["overdue_help"] == 0

    await db.execute(
        text(
            """
            UPDATE help_requests hr SET due_at = NULL
              FROM help_request_due_backfill b
             WHERE b.request_id = hr.id AND hr.due_at = b.due_at
            """
        )
    )
    assert (await _hr(db, old_open))["due_at"] is None
    assert (await _hr(db, has_due))["due_at"] == before_has_due
    await db.execute(text("ROLLBACK TO SAVEPOINT t995"))


async def test_reopened_request_becomes_overdue_after_new_deadline(db):
    """Ответ → закрытие → возврат «не помогло»: ход снова за преподавателем.

    Возврат сообщения в тред не пишет, последним в переписке остаётся
    преподаватель — но заявка обязана просрочиться, когда истечёт новый срок.
    """
    course = await _course(db)
    student = await _user(db, "t995 ученик")
    teacher = await _user(db, "t995 учитель")
    task = await _task(db, course)
    rid = await _open_request(
        db, student_id=student, task_id=task, teacher_id=teacher, due_shift="+1 hour")
    _, err = await hrs.reply_help_request(db, rid, teacher, "Смотри формулу X")
    assert err is None, err
    assert (await _hr(db, rid))["status"] == "closed"
    # В тесте всё в одной транзакции, `now()` у ответа и возврата совпадает;
    # в жизни это разные запросы — ответ раньше возврата.
    await db.execute(
        text("UPDATE messages SET sent_at = sent_at - interval '2 hours' "
             "WHERE thread_id = (SELECT thread_id FROM help_requests WHERE id = :id)"),
        {"id": rid},
    )
    _, err = await hrs.reopen_help_request(db, rid, student)
    assert err is None, err
    # Новый срок ещё идёт — не просрочено.
    assert (await get_teacher_workload(db, teacher))["overdue_help"] == 0
    # Срок истёк — просрочено, хотя последним в треде писал преподаватель.
    await db.execute(
        text("UPDATE help_requests SET due_at = now() - interval '1 minute' WHERE id = :id"),
        {"id": rid},
    )
    assert (await get_teacher_workload(db, teacher))["overdue_help"] == 1
    # Преподаватель ответил после возврата — ход снова за учеником.
    await db.execute(
        text("UPDATE help_request_reopens SET reopened_at = now() - interval '1 hour' "
             "WHERE request_id = :id"),
        {"id": rid},
    )
    _, err = await hrs.reply_help_request(db, rid, teacher, "Ещё раз: смотри шаг 2")
    assert err is None, err
    assert (await get_teacher_workload(db, teacher))["overdue_help"] == 0
