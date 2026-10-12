"""tsk-1322: где видеть действующие замки — карточка ученика и список админа.

Прод, 12.10: замок «88 ждёт 1455» висел у ученицы два месяца, и узнали о нём из
её жалобы. Граф теста: корень MAIN закрыт корнем IND (MAIN -> IND). Ученик
LOCKED записан на оба, FREE — только на MAIN (IND ему недоступен — замка нет).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.core.config import Settings
from app.api.main import app
from app.api.deps import get_current_user
from app.auth.current_user import CurrentUser
from app.services import dependency_lock_service

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def graph(db):
    """Граф с замком, два ученика и преподаватель без связки с ними."""
    s = db
    ids: dict[str, int] = {}
    did = (await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    for key in ("main", "ind"):
        ids[key] = (
            await s.execute(
                text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
                {"t": f"tsk1322 {key}"},
            )
        ).scalar()
        # Курс без заданий движок считает пройденным — замок бы не встал.
        await s.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, external_uid) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :c, :d, :u)"
            ),
            {
                "tc": '{"type":"SC","stem":"2+2?","options":[{"id":"a","text":"3"},{"id":"b","text":"4"}]}',
                "sr": '{"max_score":1,"correct_options":["b"]}',
                "c": ids[key], "d": did, "u": f"tsk1322-{key}",
            },
        )
    await s.execute(
        text("INSERT INTO course_dependencies (course_id, required_course_id, auto_assign) VALUES (:m, :i, false)"),
        {"m": ids["main"], "i": ids["ind"]},
    )
    for key in ("locked", "free", "teacher"):
        ids[key] = (
            await s.execute(text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"), {"n": f"tsk1322 {key}"})
        ).scalar()
    await s.execute(
        text("INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE name = 'teacher'"),
        {"u": ids["teacher"]},
    )
    for user, course_id in ((ids["locked"], ids["main"]), (ids["locked"], ids["ind"]), (ids["free"], ids["main"])):
        await s.execute(
            text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
            {"u": user, "c": course_id},
        )
    await s.commit()
    yield ids, s


async def test_describe_locks_gives_course_and_date(graph):
    """Замок описан: что закрыто, чем, с какой даты; у свободного — пусто."""
    ids, s = graph
    locks = await dependency_lock_service.describe_locks(s, ids["locked"])
    assert len(locks) == 1
    lock = locks[0]
    assert lock["course_id"] == ids["main"]
    assert lock["required_course_id"] == ids["ind"]
    assert lock["required_course_title"] == "tsk1322 ind"
    assert lock["since"] is not None and lock["days"] == 0
    assert await dependency_lock_service.describe_locks(s, ids["free"]) == []


async def test_admin_list_contains_only_locked(client, graph):
    """Список админа: закрытый ученик с именем есть, свободного нет."""
    ids, _ = graph
    resp = await client.get("/api/v1/admin/dependency-locks", headers=_service_headers())
    assert resp.status_code == 200, resp.text
    mine = [r for r in resp.json() if r["student_id"] in (ids["locked"], ids["free"])]
    assert [(r["student_id"], r["course_id"], r["student_name"]) for r in mine] == [
        (ids["locked"], ids["main"], "tsk1322 locked")
    ]


async def test_admin_list_forbidden_for_teacher(client, graph):
    """Общий список — только администратору."""
    ids, _ = graph
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=ids["teacher"], is_service=False)
    try:
        resp = await client.get("/api/v1/admin/dependency-locks")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 403


async def test_teacher_sees_locks_only_of_own_student(client, graph):
    """Чужой ученик — пустой блок; после связки — замок виден."""
    ids, s = graph
    url = f"/api/v1/teacher/students/{ids['locked']}/dependency-locks"
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=ids["teacher"], is_service=False)
    try:
        assert (await client.get(url)).json() == []
        await s.execute(
            text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
            {"s": ids["locked"], "t": ids["teacher"]},
        )
        await s.commit()
        body = (await client.get(url)).json()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert [(r["course_id"], r["required_course_id"]) for r in body] == [(ids["main"], ids["ind"])]
