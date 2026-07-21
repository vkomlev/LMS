"""
Автозакрытие устаревших заявок blocked_limit при переходе задания в PASSED
(tsk-339).

Находка живого прогона tsk-335/336: список открытых заявок «Лимит попыток»
на проде разошёлся с экраном прогресса ученика — 9 заявок оказались открыты
по заданиям, которые ученик уже решил сам (последний ответ `is_correct=true`),
без единого выданного лимита учителем. Причина — в LMS backend не было
механизма закрытия `help_requests(request_type='blocked_limit')` при
естественном переходе задания в PASSED (только явная выдача лимита учителем,
tsk-335, закрывает заявку).

Реалистичный сценарий разрешения без учителя — **не** «сдал сверх лимита в том
же курсе» (это сервер и так отбивает 409, tsk-269), а переиспользуемый узел
дерева под НЕСКОЛЬКИМИ курсами (tsk-264): лимит исчерпан в корне A → заявка
создалась, но в корне B у того же узла свежий бюджет попыток, и там ученик
решает задачу сам. Фикстура — тот же граф root_a/root_b/reused, что и
`test_attempts_limit_enforced_tsk269.py`.

Покрывают:
- решил задание сам в ДРУГОМ корне после блокировки в исходном → открытая
  заявка закрывается системно (`closed_by IS NULL`), с понятным
  `resolution_comment`;
- заявка была УЖЕ закрыта учителем — повторное закрытие идемпотентно (не падает,
  не перезаписывает `closed_by` реального учителя);
- неверный ответ в том же корне (409 по лимиту) — заявка остаётся открытой;
- нет открытой заявки на это задание → PASSED проходит штатно, без ошибок
  (регресс: новый шаг не ломает обычный приём ответа);
- заявка `manual_help` (не `blocked_limit`) по тому же заданию не закрывается
  автозакрытием — это не её механизм (закрывает учитель вручную).

Работает с dev-БД (Learn.public) на настоящем графе курсов, подчищает за собой.
Модуль в `SELF_MANAGED_CONNECTION_MODULES` (conftest.py) — свой движок,
несовместим с общей откатываемой транзакцией (tsk-333).
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
from app.services.attempts_service import AttemptsService
from app.services.help_requests_service import get_or_create_blocked_limit_help_request
from app.services.learning_engine_service import DEFAULT_MAX_ATTEMPTS

pytestmark = pytest.mark.asyncio

_settings = Settings()
_attempts = AttemptsService()

_WRONG_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["a"]}}
_RIGHT_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["b"]}}


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def graph():
    """Переиспользуемый узел под двумя корнями (как tsk269) + ученик/учитель."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:

            async def new_course(title: str) -> int:
                return (
                    await s.execute(
                        text(
                            "INSERT INTO courses (title, access_level) "
                            "VALUES (:t, 'self_guided') RETURNING id"
                        ),
                        {"t": title},
                    )
                ).scalar()

            ids["root_a"] = await new_course("tsk339 корень A")
            ids["root_b"] = await new_course("tsk339 корень B")
            ids["reused"] = await new_course("tsk339 переиспользуемый узел")

            for parent in ("root_a", "root_b"):
                await s.execute(
                    text(
                        "INSERT INTO course_parents (course_id, parent_course_id) "
                        "VALUES (:c, :p)"
                    ),
                    {"c": ids["reused"], "p": ids[parent]},
                )

            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            assert difficulty_id is not None, "нет difficulties — граф не собрать"

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
                        "cid": ids["reused"],
                        "did": difficulty_id,
                        "uid": f"tsk339-{uuid.uuid4().hex[:12]}",
                        "ma": DEFAULT_MAX_ATTEMPTS,
                    },
                )
            ).scalar()

            ids["user"] = (
                await s.execute(
                    text(
                        "INSERT INTO users (full_name) VALUES "
                        "('tsk339 тестовый ученик') RETURNING id"
                    )
                )
            ).scalar()
            ids["teacher"] = (
                await s.execute(
                    text(
                        "INSERT INTO users (full_name) VALUES "
                        "('tsk339 тестовый учитель') RETURNING id"
                    )
                )
            ).scalar()
            for r in ("root_a", "root_b"):
                await s.execute(
                    text(
                        "INSERT INTO user_courses (user_id, course_id, is_active) "
                        "VALUES (:u, :c, true)"
                    ),
                    {"u": ids["user"], "c": ids[r]},
                )
            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            uid = ids.get("user", -1)
            await s.execute(text("DELETE FROM help_requests WHERE student_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM task_results WHERE user_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM attempts WHERE user_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": uid})
            if "task" in ids:
                await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
            await s.execute(
                text("DELETE FROM users WHERE id = ANY(:u)"),
                {"u": [ids[k] for k in ("user", "teacher") if k in ids]},
            )
            if "reused" in ids:
                await s.execute(
                    text("DELETE FROM course_parents WHERE course_id = :c"),
                    {"c": ids["reused"]},
                )
            await s.execute(
                text("DELETE FROM courses WHERE id = ANY(:c)"),
                {"c": [ids[k] for k in ("root_a", "root_b", "reused") if k in ids]},
            )
            await s.commit()
            await engine.dispose()


async def _burn_to_blocked(s: AsyncSession, ids: dict[str, int], root_key: str = "root_a") -> None:
    """Исчерпать лимит попыток неверными ответами в заданном корне (мимо HTTP — setup)."""
    attempt = await _attempts.create_attempt(
        s, user_id=ids["user"], course_id=ids["reused"],
        root_course_id=ids[root_key], source_system="test_tsk339",
    )
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        await s.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, "
                "max_score, is_correct, submitted_at) VALUES "
                "(:u, :t, :a, 0, 1, false, now())"
            ),
            {"u": ids["user"], "t": ids["task"], "a": attempt.id},
        )
    await s.execute(text("UPDATE attempts SET finished_at = now() WHERE id = :a"), {"a": attempt.id})
    await s.commit()


async def _open_attempt(client, ids: dict[str, int], root_key: str) -> int:
    resp = await client.post(
        "/api/v1/attempts",
        json={
            "user_id": ids["user"], "course_id": ids["reused"],
            "root_course_id": ids[root_key], "source_system": "test_tsk339",
        },
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _help_request_row(s: AsyncSession, request_id: int) -> dict:
    row = (
        await s.execute(
            text(
                "SELECT status, closed_by, resolution_comment FROM help_requests WHERE id = :id"
            ),
            {"id": request_id},
        )
    ).fetchone()
    return {"status": row[0], "closed_by": row[1], "resolution_comment": row[2]}


async def test_passing_in_other_root_closes_blocked_limit_request(client, graph):
    """Заблокирован в корне A, решил задание сам в корне B → заявка закрывается.

    tsk-264: лимит попыток — per-root, свежий бюджет в root_b не пересекается
    с исчерпанным в root_a. Именно этот путь воспроизводит находку на проде
    (переиспользуемый узел под несколькими курсами).
    """
    ids, s = graph
    await _burn_to_blocked(s, ids, "root_a")
    request_id, created, _dedup = await get_or_create_blocked_limit_help_request(
        s, student_id=ids["user"], task_id=ids["task"], course_id=ids["reused"],
        attempts_used=DEFAULT_MAX_ATTEMPTS, attempts_limit_effective=DEFAULT_MAX_ATTEMPTS,
    )
    await s.commit()
    assert created, "заявка обязана быть создана для теста"

    attempt_id = await _open_attempt(client, ids, "root_b")
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _RIGHT_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    row = await _help_request_row(s, request_id)
    assert row["status"] == "closed", "заявка обязана закрыться сама — задание решено"
    assert row["closed_by"] is None, "системное закрытие — closed_by=NULL, не выдумываем автора"
    assert row["resolution_comment"] == "Задание решено учеником самостоятельно"


async def test_already_closed_by_teacher_is_idempotent(client, graph):
    """Заявку уже закрыл учитель — авто-закрытие не перезаписывает closed_by."""
    ids, s = graph
    await _burn_to_blocked(s, ids, "root_a")
    request_id, _created, _dedup = await get_or_create_blocked_limit_help_request(
        s, student_id=ids["user"], task_id=ids["task"], course_id=ids["reused"],
        attempts_used=DEFAULT_MAX_ATTEMPTS, attempts_limit_effective=DEFAULT_MAX_ATTEMPTS,
    )
    await s.execute(
        text(
            "UPDATE help_requests SET status='closed', closed_at=now(), "
            "closed_by=:t, resolution_comment='Продлил лимит вручную' WHERE id=:id"
        ),
        {"t": ids["teacher"], "id": request_id},
    )
    await s.commit()

    attempt_id = await _open_attempt(client, ids, "root_b")
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _RIGHT_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    row = await _help_request_row(s, request_id)
    assert row["status"] == "closed"
    assert row["closed_by"] == ids["teacher"], "закрытие учителем не должно затираться системным"
    assert row["resolution_comment"] == "Продлил лимит вручную"


async def test_wrong_answer_in_blocked_root_does_not_close_request(client, graph):
    """Неверный ответ в ТОМ ЖЕ (заблокированном) корне — 409, заявка остаётся открытой."""
    ids, s = graph
    await _burn_to_blocked(s, ids, "root_a")
    request_id, _created, _dedup = await get_or_create_blocked_limit_help_request(
        s, student_id=ids["user"], task_id=ids["task"], course_id=ids["reused"],
        attempts_used=DEFAULT_MAX_ATTEMPTS, attempts_limit_effective=DEFAULT_MAX_ATTEMPTS,
    )
    await s.commit()

    attempt_id = await _open_attempt(client, ids, "root_a")
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    # Лимит уже исчерпан в root_a — сервер отобьёт 409 (tsk-269), заявка не при делах.
    assert resp.status_code == 409, resp.text

    row = await _help_request_row(s, request_id)
    assert row["status"] == "open", "неудачная попытка не должна закрывать заявку"


async def test_passing_without_open_request_is_noop(client, graph):
    """PASSED без предварительной заявки — регресс: приём ответа не ломается."""
    ids, s = graph
    attempt_id = await _open_attempt(client, ids, "root_a")
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _RIGHT_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    count = (
        await s.execute(
            text("SELECT COUNT(*) FROM help_requests WHERE student_id = :u"), {"u": ids["user"]}
        )
    ).scalar()
    assert count == 0, "не должно появиться заявок там, где их не было"


async def test_manual_help_request_not_touched_by_autoclose(client, graph):
    """Заявка manual_help по тому же заданию — не тот механизм, не закрывается."""
    ids, s = graph
    request_id = (
        await s.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, request_type, "
                "auto_created, context_json, priority, created_at, updated_at) "
                "VALUES ('open', :s, :t, 'manual_help', false, '{}'::jsonb, 100, now(), now()) "
                "RETURNING id"
            ),
            {"s": ids["user"], "t": ids["task"]},
        )
    ).scalar_one()
    await s.commit()

    attempt_id = await _open_attempt(client, ids, "root_a")
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _RIGHT_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    row = await _help_request_row(s, request_id)
    assert row["status"] == "open", "manual_help закрывает учитель вручную, не автозакрытие"
