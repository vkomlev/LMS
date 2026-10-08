"""Регрессия tsk-1276: замок зависимости держит сервер, а не только «Продолжить».

Прод, 07.10: замок «88 требует 2106» стоял, движок отвечал blocked_dependency,
а ученик открывал задания курса 88 из программы курса и сдавал их (12 сдач), и
автоматика ДЗ выдала ему задания закрытого курса.

Граф теста: основной корень MAIN (глава CH с заданием), индивидуальный корень IND
(глава ICH с заданием), замок MAIN -> IND. Ученик LOCKED записан на оба, сосед
FREE — только на MAIN.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.core.config import Settings
from app.api.main import app
from app.api.deps import get_current_user
from app.auth.current_user import CurrentUser
from app.services import dependency_lock_service, homework_service

pytestmark = pytest.mark.asyncio

_settings = Settings()
_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["b"]}}


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def graph(db):
    """Граф с замком + два ученика поверх общей откатываемой транзакции теста.

    Своё соединение тут не годится: приложение в тестах пишет в ту же внешнюю
    транзакцию (conftest, tsk-333), и уборка из второго соединения ждала бы её
    блокировок вечно.
    """
    s = db
    ids: dict[str, int] = {}

    async def course(title: str, parent: int | None = None) -> int:
        cid = (
            await s.execute(
                text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
                {"t": title},
            )
        ).scalar()
        if parent is not None:
            await s.execute(
                text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"),
                {"c": cid, "p": parent},
            )
        return cid

    did = (await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()

    async def task(course_id: int) -> int:
        return (
            await s.execute(
                text(
                    "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
                    "external_uid, max_attempts) VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), "
                    ":c, :d, :u, 10) RETURNING id"
                ),
                {
                    "tc": '{"type":"SC","stem":"2+2?","options":[{"id":"a","text":"3"},{"id":"b","text":"4"}]}',
                    "sr": '{"max_score":1,"correct_options":["b"]}',
                    "c": course_id, "d": did, "u": f"tsk1276-{uuid.uuid4().hex[:12]}",
                },
            )
        ).scalar()

    ids["main"] = await course("tsk1276 основной")
    ids["ch"] = await course("tsk1276 глава", ids["main"])
    ids["ind"] = await course("tsk1276 повторение")
    ids["ich"] = await course("tsk1276 глава повторения", ids["ind"])
    ids["task_main"] = await task(ids["ch"])
    ids["task_ind"] = await task(ids["ich"])
    await s.execute(
        text("INSERT INTO course_dependencies (course_id, required_course_id, auto_assign) VALUES (:m, :i, false)"),
        {"m": ids["main"], "i": ids["ind"]},
    )
    for key in ("locked", "free"):
        ids[key] = (
            await s.execute(text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"), {"n": f"tsk1276 {key}"})
        ).scalar()
    for user, course_id in ((ids["locked"], ids["main"]), (ids["locked"], ids["ind"]), (ids["free"], ids["main"])):
        await s.execute(
            text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
            {"u": user, "c": course_id},
        )
    await s.commit()
    yield ids, s


async def _open_attempt(client, user_id: int, course_id: int) -> int:
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": user_id, "course_id": course_id, "source_system": "test_tsk1276"},
        headers=_service_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _answer_as(client, user_id: int | None, attempt_id: int, task_id: int):
    """Ответ от имени ученика (cookie) или сервисным ключом (user_id=None)."""
    body = {"items": [{"task_id": task_id, "answer": _ANSWER}]}
    if user_id is None:
        return await client.post(f"/api/v1/attempts/{attempt_id}/answers", json=body, headers=_service_headers())
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=user_id, is_service=False)
    try:
        return await client.post(f"/api/v1/attempts/{attempt_id}/answers", json=body)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_locked_student_cannot_submit_main_task(client, graph):
    """Сдача задания закрытого курса — 403 blocked_dependency с курсом-пререквизитом."""
    ids, _ = graph
    attempt = await _open_attempt(client, ids["locked"], ids["main"])
    resp = await _answer_as(client, ids["locked"], attempt, ids["task_main"])
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body.get("code") == "blocked_dependency" or body.get("payload", {}).get("code") == "blocked_dependency", body
    assert "tsk1276 повторение" in str(body)


async def test_service_key_cannot_bypass_lock(client, graph):
    """Бот сервисным ключом на попытке ученика тоже получает отказ (отсечка по ученику)."""
    ids, _ = graph
    attempt = await _open_attempt(client, ids["locked"], ids["main"])
    resp = await _answer_as(client, None, attempt, ids["task_main"])
    assert resp.status_code == 403, resp.text


async def test_individual_course_task_is_open(client, graph):
    """Задание самого курса повторения сдаётся."""
    ids, _ = graph
    attempt = await _open_attempt(client, ids["locked"], ids["ind"])
    resp = await _answer_as(client, ids["locked"], attempt, ids["task_ind"])
    assert resp.status_code == 200, resp.text


async def test_neighbour_without_individual_course_is_not_locked(client, graph):
    """Сосед по основному курсу без курса повторения не закрыт (точечность tsk-231 ф.6)."""
    ids, _ = graph
    attempt = await _open_attempt(client, ids["free"], ids["main"])
    resp = await _answer_as(client, ids["free"], attempt, ids["task_main"])
    assert resp.status_code == 200, resp.text


async def test_task_page_denied_for_locked_student(client, graph):
    """Открытие задания по прямому адресу — тот же отказ, что и при сдаче."""
    ids, _ = graph
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=ids["locked"], is_service=False)
    try:
        resp = await client.get(f"/api/v1/tasks/{ids['task_main']}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 403, resp.text


async def test_lock_lifts_when_individual_course_completed(client, graph):
    """Курс повторения пройден (верный ответ через API) — задание основного курса открыто."""
    ids, s = graph
    assert await dependency_lock_service.lock_for_course(s, ids["locked"], ids["ch"]) is not None
    attempt = await _open_attempt(client, ids["locked"], ids["ind"])
    resp = await _answer_as(client, ids["locked"], attempt, ids["task_ind"])
    assert resp.status_code == 200, resp.text
    s.info.pop("tsk1276_locks", None)  # кеш живёт в сессии; в приложении сессия на запрос
    assert await dependency_lock_service.lock_for_course(s, ids["locked"], ids["ch"]) is None
    attempt_main = await _open_attempt(client, ids["locked"], ids["main"])
    resp = await _answer_as(client, ids["locked"], attempt_main, ids["task_main"])
    assert resp.status_code == 200, resp.text


async def test_homework_picks_individual_course_not_locked_one(graph):
    """Подбор ДЗ: при замке задания берутся из курса повторения, а не из закрытого."""
    ids, s = graph
    items = await homework_service._next_items(s, student_id=ids["locked"], limit=5)
    task_ids = {i["item_id"] for i in items if i["kind"] == "task"}
    assert ids["task_ind"] in task_ids, items
    assert ids["task_main"] not in task_ids, items
    free_items = await homework_service._next_items(s, student_id=ids["free"], limit=5)
    assert ids["task_main"] in {i["item_id"] for i in free_items if i["kind"] == "task"}


async def test_chain_of_locks_homework_and_message_point_to_open_course(graph):
    """Цепочка «SECOND требует MAIN», «MAIN требует IND»: ДЗ и отказ ведут в IND, не в MAIN.

    Ревью tsk-1276: так устроен ученик 4569 (112 -> 88 -> 2106); первая версия
    возвращала в ДЗ закрытый курс 88 как «нужный для 112».
    """
    ids, s = graph
    second = (
        await s.execute(
            text("INSERT INTO courses (title, access_level) VALUES ('tsk1276 второй', 'self_guided') RETURNING id")
        )
    ).scalar()
    sch = (
        await s.execute(
            text("INSERT INTO courses (title, access_level) VALUES ('tsk1276 глава второго', 'self_guided') RETURNING id")
        )
    ).scalar()
    await s.execute(text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"), {"c": sch, "p": second})
    await s.execute(
        text("INSERT INTO course_dependencies (course_id, required_course_id, auto_assign) VALUES (:s, :m, true)"),
        {"s": second, "m": ids["main"]},
    )
    await s.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": ids["locked"], "c": second},
    )
    await s.commit()
    s.info.pop("tsk1276_locks", None)

    lock = await dependency_lock_service.lock_for_course(s, ids["locked"], sch)
    assert lock is not None and lock.required_course_id == ids["ind"], lock
    items = await homework_service._next_items(s, student_id=ids["locked"], limit=10)
    task_ids = {i["item_id"] for i in items if i["kind"] == "task"}
    assert ids["task_ind"] in task_ids, items
    assert ids["task_main"] not in task_ids, items


async def test_recommended_task_is_never_locked(client, graph):
    """Правило оператора 08.10: замок закрывает только обязательное ученику.

    Рекомендуемое задание закрытого курса открывается и сдаётся.
    """
    ids, s = graph
    await s.execute(
        text("UPDATE tasks SET requirement_level='recommended' WHERE id=:t"), {"t": ids["task_main"]}
    )
    await s.commit()
    attempt = await _open_attempt(client, ids["locked"], ids["main"])
    resp = await _answer_as(client, ids["locked"], attempt, ids["task_main"])
    assert resp.status_code == 200, resp.text
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=ids["locked"], is_service=False)
    try:
        page = await client.get(f"/api/v1/tasks/{ids['task_main']}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert page.status_code == 200, page.text


async def test_graced_task_is_never_locked(client, graph, monkeypatch):
    """Задание, ставшее ученику необязательным по tsk-692, тоже не закрывается."""
    ids, _ = graph
    from app.services import content_grace_service

    async def _graced(db, student_id, root_course_id):
        return content_grace_service.GracedItems(tasks=frozenset({ids["task_main"]}))

    monkeypatch.setattr(content_grace_service, "compute_graced_items", _graced)
    attempt = await _open_attempt(client, ids["locked"], ids["main"])
    resp = await _answer_as(client, ids["locked"], attempt, ids["task_main"])
    assert resp.status_code == 200, resp.text
