"""Регрессия tsk-272: приём ответа проверяет доступ ученика к заданию.

Дыра (найдена в Risks/Follow-ups п.3 ревью tsk-269, подтверждена на живых данных):
ученик без единой активной записи `user_courses (is_active = true)` успешно отправлял
ответы через POST /api/v1/attempts/{id}/answers и наращивал task_results. Чтение
задания защищено `assert_task_access` (GET /tasks/*), а запись task_results — нет.

Фикс: тот же `assert_task_access` в `submit_attempt_answers` (per-item, до записи).
Bypass helper'а: is_service (X-API-Key) и роли teacher/methodist/admin.

Тесты бьют по HTTP (дыра была в HTTP-слое). Аутентификация подменяется через
`app.dependency_overrides[get_current_user]`, чтобы отличить обычного ученика
(is_service=False) от сервисного ключа. Работают с dev-БД (Learn.public),
подчищают за собой.
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

_WRONG_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["a"]}}


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def fixture_graph():
    """Курс + SC-задание + ученик БЕЗ записи user_courses + учитель. Полная уборка."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            ids["course"] = (
                await s.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES ('tsk272 курс', 'self_guided') RETURNING id"
                    )
                )
            ).scalar()

            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            assert difficulty_id is not None, "нет difficulties — задание не собрать"

            ids["task"] = (
                await s.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_attempts) VALUES "
                        "(CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, :ma) "
                        "RETURNING id"
                    ),
                    {
                        "tc": (
                            '{"type":"SC","stem":"2+2?","options":['
                            '{"id":"a","text":"3"},{"id":"b","text":"4"}]}'
                        ),
                        "sr": '{"max_score":1,"correct_options":["b"]}',
                        "cid": ids["course"],
                        "did": difficulty_id,
                        # Уникальный uid на прогон: фиксированный 'tsk272-task'
                        # переживал прерванный прогон и валил следующий с
                        # UniqueViolation на tasks_external_uid_key (tsk-333).
                        "uid": f"tsk272-task-{uuid.uuid4().hex[:12]}",
                        "ma": 3,
                    },
                )
            ).scalar()

            # Ученик БЕЗ записи на курс.
            ids["outsider"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk272 без записи') RETURNING id")
                )
            ).scalar()

            # Ученик С активной записью на курс.
            ids["enrolled"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk272 записан') RETURNING id")
                )
            ).scalar()
            await s.execute(
                text(
                    "INSERT INTO user_courses (user_id, course_id, is_active) "
                    "VALUES (:u, :c, true)"
                ),
                {"u": ids["enrolled"], "c": ids["course"]},
            )

            # Преподаватель (роль teacher) — без записи на курс, но с расширенной ролью.
            ids["teacher"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk272 учитель') RETURNING id")
                )
            ).scalar()
            teacher_role_id = (
                await s.execute(text("SELECT id FROM roles WHERE name = 'teacher' LIMIT 1"))
            ).scalar()
            if teacher_role_id is not None:
                await s.execute(
                    text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
                    {"u": ids["teacher"], "r": teacher_role_id},
                )
            ids["teacher_role_id"] = teacher_role_id or -1

            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            users = [ids[k] for k in ("outsider", "enrolled", "teacher") if k in ids]
            if users:
                await s.execute(
                    text("DELETE FROM task_results WHERE user_id = ANY(:u)"), {"u": users}
                )
                await s.execute(
                    text("DELETE FROM attempts WHERE user_id = ANY(:u)"), {"u": users}
                )
                await s.execute(
                    text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": users}
                )
                await s.execute(
                    text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": users}
                )
            if "task" in ids:
                await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
            if users:
                await s.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
            if "course" in ids:
                await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
            await s.commit()
            await engine.dispose()


async def _count_results(s: AsyncSession, ids: dict[str, int], user_id: int) -> int:
    return (
        await s.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": user_id, "t": ids["task"]},
        )
    ).scalar()


async def _open_attempt_service(client, user_id: int, course_id: int) -> int:
    """Открыть попытку сервисным ключом (создание попытки — не предмет tsk-272)."""
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": user_id, "course_id": course_id, "source_system": "test_tsk272"},
        headers=_service_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_unenrolled_student_denied(client, fixture_graph):
    """Дыра закрыта: ученик без записи на курс получает 403, task_results не растёт."""
    ids, s = fixture_graph
    outsider = ids["outsider"]
    attempt_id = await _open_attempt_service(client, outsider, ids["course"])
    before = await _count_results(s, ids, outsider)

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=outsider, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 403, (
        f"ученик без записи на курс не должен отвечать на задание: {resp.text}"
    )
    assert await _count_results(s, ids, outsider) == before, (
        "при 403 task_results писаться не должен"
    )


async def test_enrolled_student_allowed(client, fixture_graph):
    """Записанный ученик отвечает как раньше (регресс не сломан)."""
    ids, s = fixture_graph
    enrolled = ids["enrolled"]
    attempt_id = await _open_attempt_service(client, enrolled, ids["course"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=enrolled, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"ученик с активной записью на курс обязан отвечать: {resp.text}"
    )


async def test_service_key_still_allowed(client, fixture_graph):
    """Сервисный ключ (X-API-Key) проходит без записи на курс: bypass сохранён (TG_LMS, CB)."""
    ids, s = fixture_graph
    outsider = ids["outsider"]
    attempt_id = await _open_attempt_service(client, outsider, ids["course"])

    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_service_headers(),
    )
    assert resp.status_code == 200, (
        f"сервисный ключ обязан проходить (боты TG_LMS, CB CLI): {resp.text}"
    )


async def test_teacher_role_allowed(client, fixture_graph):
    """Преподаватель (расширенная роль) проходит без записи на курс: bypass сохранён."""
    ids, s = fixture_graph
    if ids.get("teacher_role_id", -1) < 0:
        pytest.skip("нет роли teacher в справочнике roles")
    teacher = ids["teacher"]
    attempt_id = await _open_attempt_service(client, teacher, ids["course"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=teacher, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"преподаватель (teacher) обязан проходить проверку доступа: {resp.text}"
    )
