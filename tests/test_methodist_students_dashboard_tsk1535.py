"""tsk-1535: дашборд методиста — галочка «убрать выпускников и тариф Тест».

По умолчанию (exclude_graduates_test=True) ученики с АКТИВНОЙ подпиской
(student_subscription.ends_on IS NULL) на планы subscription_plan id 1
«Test» или 9 «Выпускник» исключаются из дашборда. С exclude_graduates_test=False
и у учеников с закончившейся подпиской они видны как раньше.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import methodist_students_dashboard_service as svc
from tests.test_methodist_students_dashboard_tsk1516 import _period_dates
from tests.test_teacher_lesson_summary_tsk022_410 import (
    _insert_task_result,
    _new_course,
    _new_task,
    _new_user,
)

UTC = timezone.utc


async def _ensure_plan(db, *, plan_id: int, code: str) -> None:
    await db.execute(
        text(
            "INSERT INTO subscription_plan (id, code, name) VALUES (:i, :c, :c) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"i": plan_id, "c": code},
    )
    await db.commit()


async def _insert_subscription(
    db, *, student_id: int, plan_id: int, ends_on,
) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on, ends_on) "
            "VALUES (:s, :p, CURRENT_DATE - 30, :e)"
        ),
        {"s": student_id, "p": plan_id, "e": ends_on},
    )
    await db.commit()


async def _active_student_with_solved_task(db, *, tag: str) -> int:
    """Ученик с активностью за период (иначе он вообще не попадёт в дашборд)."""
    student_id, _ = await _new_user(db, role="student", name=tag)
    now = datetime.now(UTC)
    course_id = await _new_course(db, f"tsk1535 {tag}")
    task_id = await _new_task(db, course_id=course_id, uid=f"{tag}1")
    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )
    return student_id


@pytest.mark.asyncio
async def test_active_graduate_and_test_plan_excluded_by_default(db):
    await _ensure_plan(db, plan_id=1, code="test")
    await _ensure_plan(db, plan_id=9, code="graduate")
    grad_id = await _active_student_with_solved_task(db, tag="a1535grad")
    test_id = await _active_student_with_solved_task(db, tag="a1535test")
    await _insert_subscription(db, student_id=grad_id, plan_id=9, ends_on=None)
    await _insert_subscription(db, student_id=test_id, plan_id=1, ends_on=None)

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    ids = {r["student_id"] for r in data["students"]}
    assert grad_id not in ids
    assert test_id not in ids


@pytest.mark.asyncio
async def test_included_when_exclude_flag_false(db):
    await _ensure_plan(db, plan_id=9, code="graduate")
    grad_id = await _active_student_with_solved_task(db, tag="a1535grad2")
    await _insert_subscription(db, student_id=grad_id, plan_id=9, ends_on=None)

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(
        db, from_date=from_d, to_date=to_d, exclude_graduates_test=False,
    )
    ids = {r["student_id"] for r in data["students"]}
    assert grad_id in ids


@pytest.mark.asyncio
async def test_endpoint_exclude_flag_parsing(client, db):
    """API: exclude_graduates_test=false возвращает выпускника, default — нет."""
    from tests.test_methodist_students_dashboard_tsk1516 import _new_user as _mk_user

    await _ensure_plan(db, plan_id=9, code="graduate")
    _methodist_id, token = await _mk_user(db, role="methodist", name="a1535m")
    grad_id = await _active_student_with_solved_task(db, tag="a1535api")
    await _insert_subscription(db, student_id=grad_id, plan_id=9, ends_on=None)

    now = datetime.now(UTC)
    from_d, to_d = _period_dates(now)
    headers = {"Authorization": f"Bearer {token}"}
    base = {"from": from_d, "to": to_d}

    resp_default = await client.get(
        "/api/v1/methodist/students-dashboard", params=base, headers=headers,
    )
    assert resp_default.status_code == 200, resp_default.text
    ids_default = {r["student_id"] for r in resp_default.json()["students"]}
    assert grad_id not in ids_default

    resp_false = await client.get(
        "/api/v1/methodist/students-dashboard",
        params={**base, "exclude_graduates_test": "false"},
        headers=headers,
    )
    assert resp_false.status_code == 200, resp_false.text
    ids_false = {r["student_id"] for r in resp_false.json()["students"]}
    assert grad_id in ids_false


@pytest.mark.asyncio
async def test_expired_subscription_not_excluded(db):
    """Подписка завершилась (ends_on NOT NULL) — ученик виден при галочке."""
    await _ensure_plan(db, plan_id=9, code="graduate")
    grad_id = await _active_student_with_solved_task(db, tag="a1535exp")
    await _insert_subscription(
        db, student_id=grad_id, plan_id=9, ends_on=date(2026, 11, 1),
    )

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    ids = {r["student_id"] for r in data["students"]}
    assert grad_id in ids
