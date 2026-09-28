"""tsk-1146: предпросмотр задания преподавателем проверяет ответ и НИЧЕГО не пишет.

Обычная сдача пишет attempts, task_results, learning_events (task_opened, tsk-578),
явку (tsk-439), заявки помощи, кеш student_course_state. Предпросмотр
`POST /api/v1/check/tasks/{id}/preview` — отдельный путь: задание читается,
ответ проверяется stateless-движком. Гарантия проверяется по данным: во всех
таблицах с колонкой user_id / student_id у преподавателя не появилось ни одной
строки, и глобально не выросли attempts / task_results / learning_events по заданию.

Тесты бьют по HTTP, аутентификация подменяется через dependency_overrides.
Работают с dev-БД (Learn.public), подчищают за собой.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.core.config import Settings
from app.api.main import app
from app.api.deps import get_current_user
from app.auth.current_user import CurrentUser

pytestmark = pytest.mark.asyncio

_settings = Settings()


@pytest_asyncio.fixture(scope="function")
async def graph():
    """Курс + SC-задание + ученик + преподаватель (с ролью teacher). Полная уборка."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            ids["course"] = (
                await s.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES ('tsk1146 курс', 'self_guided') RETURNING id"
                    )
                )
            ).scalar()
            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            ids["task"] = (
                await s.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_attempts, is_active) VALUES "
                        "(CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 3, true) "
                        "RETURNING id"
                    ),
                    {
                        "tc": '{"type":"SC","stem":"2+2?","options":['
                        '{"id":"a","text":"3"},{"id":"b","text":"4"}]}',
                        "sr": '{"max_score":1,"correct_options":["b"]}',
                        "cid": ids["course"],
                        "did": difficulty_id,
                        "uid": f"tsk1146-task-{uuid.uuid4().hex[:12]}",
                    },
                )
            ).scalar()
            for key in ("student", "teacher"):
                ids[key] = (
                    await s.execute(
                        text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
                        {"n": f"tsk1146 {key}"},
                    )
                ).scalar()
            role_id = (
                await s.execute(text("SELECT id FROM roles WHERE name = 'teacher'"))
            ).scalar()
            await s.execute(
                text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
                {"u": ids["teacher"], "r": role_id},
            )
            student_role_id = (
                await s.execute(text("SELECT id FROM roles WHERE name = 'student'"))
            ).scalar()
            await s.execute(
                text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
                {"u": ids["student"], "r": student_role_id},
            )
            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            users = [ids[k] for k in ("student", "teacher") if k in ids]
            for table, column in (
                ("task_results", "user_id"),
                ("attempts", "user_id"),
                ("learning_events", "student_id"),
                ("user_roles", "user_id"),
            ):
                await s.execute(
                    text(f"DELETE FROM {table} WHERE {column} = ANY(:u)"), {"u": users}
                )
            if "task" in ids:
                await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
            await s.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
            if "course" in ids:
                await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
            await s.commit()
            await engine.dispose()


async def _user_footprint(s: AsyncSession, user_id: int) -> dict[str, int]:
    """Число строк пользователя во ВСЕХ таблицах с колонкой user_id / student_id."""
    rows = (
        await s.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name IN ('user_id', 'student_id') "
                "AND table_name IN (SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE')"
            )
        )
    ).all()
    footprint: dict[str, int] = {}
    for table, column in rows:
        footprint[f"{table}.{column}"] = (
            await s.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = :u'), {"u": user_id}
            )
        ).scalar()
    return footprint


async def _task_rows(s: AsyncSession, task_id: int) -> dict[str, int]:
    """Строки, привязанные к заданию, в таблицах, куда пишет обычная сдача."""
    queries = {
        "task_results": "SELECT COUNT(*) FROM task_results WHERE task_id = :t",
        "learning_events": (
            "SELECT COUNT(*) FROM learning_events WHERE (payload->>'task_id')::bigint = :t"
        ),
        "help_requests": "SELECT COUNT(*) FROM help_requests WHERE task_id = :t",
    }
    return {k: (await s.execute(text(q), {"t": task_id})).scalar() for k, q in queries.items()}


@pytest.mark.parametrize("option, expected_correct", [("b", True), ("a", False)])
async def test_preview_checks_answer_and_writes_nothing(client, graph, option, expected_correct):
    """Вердикт движка возвращается, у преподавателя и по заданию ни одной новой строки."""
    ids, s = graph
    before_user = await _user_footprint(s, ids["teacher"])
    before_task = await _task_rows(s, ids["task"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ids["teacher"], is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/check/tasks/{ids['task']}/preview",
            json={"type": "SC", "response": {"selected_option_ids": [option]}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_correct"] is expected_correct
    assert body["max_score"] == 1

    await s.rollback()  # свежий снимок, а не кеш сессии
    assert await _user_footprint(s, ids["teacher"]) == before_user
    assert await _task_rows(s, ids["task"]) == before_task


async def test_preview_flags_write_nothing(client, graph):
    """GET флагов формы — вместо ученического state, который создаёт заявки и считает попытки."""
    ids, s = graph
    before_user = await _user_footprint(s, ids["teacher"])
    before_task = await _task_rows(s, ids["task"])
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ids["teacher"], is_service=False
    )
    try:
        resp = await client.get(f"/api/v1/check/tasks/{ids['task']}/preview")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "task_id": ids["task"],
        "requires_attachment": False,
        "partial_auto_check": False,
        "has_reference_answer": True,
        "has_io_tests": False,
    }
    await s.rollback()
    assert await _user_footprint(s, ids["teacher"]) == before_user
    assert await _task_rows(s, ids["task"]) == before_task


async def test_preview_forbidden_for_student(client, graph):
    """Ученику путь закрыт: вердикт по произвольному ответу раскрыл бы эталон."""
    ids, s = graph
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ids["student"], is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/check/tasks/{ids['task']}/preview",
            json={"type": "SC", "response": {"selected_option_ids": ["b"]}},
        )
        flags = await client.get(f"/api/v1/check/tasks/{ids['task']}/preview")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 403, resp.text
    assert flags.status_code == 403, flags.text


async def test_preview_type_mismatch_and_missing_task(client, graph):
    """Неверный тип ответа — 400, несуществующее задание — 404."""
    ids, _ = graph
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ids["teacher"], is_service=False
    )
    try:
        wrong = await client.post(
            f"/api/v1/check/tasks/{ids['task']}/preview",
            json={"type": "SA", "response": {"value": "4"}},
        )
        missing = await client.post(
            "/api/v1/check/tasks/2147483000/preview",
            json={"type": "SC", "response": {"selected_option_ids": ["b"]}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert wrong.status_code == 400, wrong.text
    assert missing.status_code == 404, missing.text
