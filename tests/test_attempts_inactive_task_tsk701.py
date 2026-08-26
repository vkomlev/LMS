"""Регрессия tsk-701: приём ответа проверяет активность задания (`tasks.is_active`).

Дыра (хвост линии tsk-695 → tsk-697 → tsk-699): чтение выключенного задания
ученику закрыли — одиночные ручки `GET /tasks/{id}` и `/tasks/by-external/{uid}`
(tsk-697), список заданий курса `GET /tasks/by-course/{id}` (tsk-699), клиентская
фильтрация в ТГ-ботах (tsk-696, tsk-700). Запись осталась открытой: приём ответа
`POST /api/v1/attempts/{id}/answers` смотрел на запись ученика в курс
(`assert_task_access`, tsk-272) и на дерево корня попытки (`root_contains_course`,
tsk-264), но не на активность — ответ по снятому с публикации заданию проверялся
и ложился в `task_results`.

Фикс: `assert_task_active_for_student` в `submit_attempt_answers` (per-item, до
записи), 400 «Задание снято с публикации».

Ключевое отличие от tsk-272: отсечка по ВЛАДЕЛЬЦУ попытки, а не по вызывающему.
Сервисный ключ (боты TG_LMS) на попытке ученика ТОЖЕ получает отказ — иначе
блокировка обходилась бы сменой клиента (tsk-617/tsk-673). Владелец с расширенной
ролью (teacher/methodist/admin) проходит: превью «как ученик» и разбор старой сдачи.

Тесты бьют по HTTP (дыра была в HTTP-слое). Аутентификация подменяется через
`app.dependency_overrides[get_current_user]`. Работают с dev-БД (Learn.public),
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

_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["b"]}}
_DENY_DETAIL = "Задание снято с публикации и больше не принимает ответы."


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def graph():
    """Курс + активное и выключенное SC-задание + ученик, учитель. Полная уборка."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            ids["course"] = (
                await s.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES ('tsk701 курс', 'self_guided') RETURNING id"
                    )
                )
            ).scalar()

            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            assert difficulty_id is not None, "нет difficulties — задание не собрать"

            async def _make_task(*, is_active: bool) -> int:
                return (
                    await s.execute(
                        text(
                            "INSERT INTO tasks (task_content, solution_rules, course_id, "
                            "difficulty_id, external_uid, max_attempts, is_active) VALUES "
                            "(CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, "
                            ":ma, :act) RETURNING id"
                        ),
                        {
                            "tc": (
                                '{"type":"SC","stem":"2+2?","options":['
                                '{"id":"a","text":"3"},{"id":"b","text":"4"}]}'
                            ),
                            "sr": '{"max_score":1,"correct_options":["b"]}',
                            "cid": ids["course"],
                            "did": difficulty_id,
                            # Уникальный uid на прогон: фиксированный переживал бы
                            # прерванный прогон и валил следующий (tsk-333/tsk-668).
                            "uid": f"tsk701-task-{uuid.uuid4().hex[:12]}",
                            "ma": 10,
                            "act": is_active,
                        },
                    )
                ).scalar()

            ids["task_active"] = await _make_task(is_active=True)
            ids["task_inactive"] = await _make_task(is_active=False)

            ids["student"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk701 ученик') RETURNING id")
                )
            ).scalar()
            await s.execute(
                text(
                    "INSERT INTO user_courses (user_id, course_id, is_active) "
                    "VALUES (:u, :c, true)"
                ),
                {"u": ids["student"], "c": ids["course"]},
            )

            ids["teacher"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk701 учитель') RETURNING id")
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
            users = [ids[k] for k in ("student", "teacher") if k in ids]
            tasks = [ids[k] for k in ("task_active", "task_inactive") if k in ids]
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
            if tasks:
                await s.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": tasks})
            if users:
                await s.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
            if "course" in ids:
                await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
            await s.commit()
            await engine.dispose()


async def _count_results(s: AsyncSession, *, user_id: int, task_id: int) -> int:
    return (
        await s.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": user_id, "t": task_id},
        )
    ).scalar()


async def _open_attempt(client, user_id: int, course_id: int) -> int:
    """Открыть попытку сервисным ключом (создание попытки — не предмет tsk-701)."""
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": user_id, "course_id": course_id, "source_system": "test_tsk701"},
        headers=_service_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_student_denied_on_inactive_task(client, graph):
    """Дыра закрыта: ученик получает 400 по выключенному заданию, task_results не растёт."""
    ids, s = graph
    student, task_id = ids["student"], ids["task_inactive"]
    attempt_id = await _open_attempt(client, student, ids["course"])
    before = await _count_results(s, user_id=student, task_id=task_id)

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=student, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": _ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400, (
        f"ответ по выключенному заданию не должен приниматься: {resp.text}"
    )
    assert _DENY_DETAIL in resp.json()["detail"], resp.text
    assert await _count_results(s, user_id=student, task_id=task_id) == before, (
        "при отказе task_results писаться не должен"
    )


async def test_student_denied_by_external_uid(client, graph):
    """Вторая дверь того же цикла: адресация по external_uid закрыта так же."""
    ids, s = graph
    student, task_id = ids["student"], ids["task_inactive"]
    ext = (
        await s.execute(text("SELECT external_uid FROM tasks WHERE id = :t"), {"t": task_id})
    ).scalar()
    attempt_id = await _open_attempt(client, student, ids["course"])
    before = await _count_results(s, user_id=student, task_id=task_id)

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=student, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"external_uid": ext, "answer": _ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400, resp.text
    assert _DENY_DETAIL in resp.json()["detail"], resp.text
    assert await _count_results(s, user_id=student, task_id=task_id) == before


async def test_service_key_denied_on_student_attempt(client, graph):
    """Отсечка по УЧЕНИКУ, а не по транспорту: бот на попытке ученика тоже получает отказ.

    Все боты TG_LMS ходят по сервисному ключу. Bypass по `is_service` означал бы
    «через браузер нельзя, через Telegram можно» — ровно та дыра, которую в чтении
    пришлось закрывать клиентской фильтрацией (tsk-696, tsk-700).
    """
    ids, s = graph
    student, task_id = ids["student"], ids["task_inactive"]
    attempt_id = await _open_attempt(client, student, ids["course"])
    before = await _count_results(s, user_id=student, task_id=task_id)

    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": task_id, "answer": _ANSWER}]},
        headers=_service_headers(),
    )

    assert resp.status_code == 400, (
        f"сервисный ключ на попытке ученика не должен обходить гейт: {resp.text}"
    )
    assert _DENY_DETAIL in resp.json()["detail"], resp.text
    assert await _count_results(s, user_id=student, task_id=task_id) == before


async def test_student_allowed_on_active_task(client, graph):
    """Активное задание принимается как раньше — регресс не сломан."""
    ids, s = graph
    student, task_id = ids["student"], ids["task_active"]
    attempt_id = await _open_attempt(client, student, ids["course"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=student, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": _ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"ответ по активному заданию обязан приниматься: {resp.text}"
    )
    assert await _count_results(s, user_id=student, task_id=task_id) == 1


async def test_teacher_owner_still_allowed_on_inactive_task(client, graph):
    """Владелец попытки с расширенной ролью проходит: превью «как ученик», разбор сдачи."""
    ids, s = graph
    if ids.get("teacher_role_id", -1) < 0:
        pytest.skip("нет роли teacher в справочнике roles")
    teacher, task_id = ids["teacher"], ids["task_inactive"]
    attempt_id = await _open_attempt(client, teacher, ids["course"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=teacher, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": _ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"преподаватель на своей попытке обязан проходить: {resp.text}"
    )


async def test_partial_batch_writes_nothing(client, graph):
    """Гейт стоит per-item ДО записи: активное задание в одной пачке с выключенным
    не должно оставить половину результатов."""
    ids, s = graph
    student = ids["student"]
    active_id, inactive_id = ids["task_active"], ids["task_inactive"]
    attempt_id = await _open_attempt(client, student, ids["course"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=student, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={
                "items": [
                    {"task_id": inactive_id, "answer": _ANSWER},
                    {"task_id": active_id, "answer": _ANSWER},
                ]
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400, resp.text
    assert await _count_results(s, user_id=student, task_id=inactive_id) == 0
    assert await _count_results(s, user_id=student, task_id=active_id) == 0, (
        "отказ на первом элементе не должен оставлять частичный результат"
    )
