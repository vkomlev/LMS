"""
Гонка (TOCTOU) при форсе лимита попыток на приёме ответа (tsk-273).

Находка ревью tsk-269 (Risks/Follow-ups п.1), под нагрузкой НЕ воспроизводилась.
Этот пробник воспроизводит её честно, по HTTP, на dev-БД (Learn.public).

Модель дефекта. Гейт 2.3b в POST /attempts/{id}/answers читает счёт попыток
(compute_task_state → SELECT COUNT task_results) и ПОТОМ пишет task_result. Между
чтением и записью нет блокировки, а repos/base.py коммитит каждую запись отдельно
(READ COMMITTED: каждый запрос — своя транзакция и своё соединение NullPool).
Значит N одновременных ответов при «лимит-1» все прочитают одинаковый счёт, все
пройдут гейт и все запишутся: итог task_results > limit.

Ожидание при исправленной сериализации: сверх лимита не проходит НИ ОДИН лишний
ответ (count == limit), лишние отбиваются 409. До фикса — count > limit.

Граф намеренно простой (один корень, задание прямо в нём, ученик записан на
корень): путь однозначен, корень восстанавливается, в игре только гонка.
"""
from __future__ import annotations

import asyncio
import os
import sys
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
from app.services.learning_engine_service import DEFAULT_MAX_ATTEMPTS

pytestmark = pytest.mark.asyncio

_settings = Settings()
_attempts = AttemptsService()

# Параллельных ответов «сверх лимита-1». Больше запросов — шире окно гонки.
CONCURRENCY = 6

_WRONG_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["a"]}}


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def simple_graph():
    """Один корневой курс с заданием + ученик, записанный на курс. Уборка за собой."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            ids["root"] = (
                await s.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES ('tsk273 корень гонки', 'self_guided') RETURNING id"
                    )
                )
            ).scalar()

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
                        "cid": ids["root"],
                        "did": difficulty_id,
                        "uid": "tsk273-race",
                        "ma": DEFAULT_MAX_ATTEMPTS,
                    },
                )
            ).scalar()

            ids["user"] = (
                await s.execute(
                    text(
                        "INSERT INTO users (full_name) VALUES "
                        "('tsk273 тестовый ученик') RETURNING id"
                    )
                )
            ).scalar()
            await s.execute(
                text(
                    "INSERT INTO user_courses (user_id, course_id, is_active) "
                    "VALUES (:u, :c, true)"
                ),
                {"u": ids["user"], "c": ids["root"]},
            )
            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            uid = ids.get("user", -1)
            await s.execute(text("DELETE FROM task_results WHERE user_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM attempts WHERE user_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": uid})
            if "task" in ids:
                await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
            if "root" in ids:
                await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["root"]})
            await s.commit()
            await engine.dispose()


async def _burn(s: AsyncSession, ids: dict[str, int], root_course_id: int, count: int) -> None:
    """Сжечь `count` попыток по заданию в границах корня (мимо HTTP — setup)."""
    for _ in range(count):
        attempt = await _attempts.create_attempt(
            s,
            user_id=ids["user"],
            course_id=ids["root"],
            root_course_id=root_course_id,
            source_system="test_tsk273",
        )
        await s.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, "
                "max_score, is_correct, submitted_at) VALUES "
                "(:u, :t, :a, 0, 1, false, now())"
            ),
            {"u": ids["user"], "t": ids["task"], "a": attempt.id},
        )
        await s.execute(
            text("UPDATE attempts SET finished_at = now() WHERE id = :a"), {"a": attempt.id}
        )
    await s.commit()


async def _open_attempt(client, ids: dict[str, int], root_course_id: int) -> int:
    resp = await client.post(
        "/api/v1/attempts",
        json={
            "user_id": ids["user"],
            "course_id": ids["root"],
            "root_course_id": root_course_id,
            "source_system": "test_tsk273",
        },
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _count_results(s: AsyncSession, ids: dict[str, int]) -> int:
    return (
        await s.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": ids["user"], "t": ids["task"]},
        )
    ).scalar()


async def test_concurrent_answers_do_not_outrun_limit(client, simple_graph):
    """N одновременных ответов при «лимит-1» не должны записать больше лимита.

    До фикса (гейт читает счёт и пишет не под блокировкой) — воспроизводится
    перебор: несколько запросов проходят гейт по одному и тому же прочитанному
    счёту. Тест краснеет и печатает фактический разброс кодов и итог task_results.
    """
    ids, s = simple_graph

    # Оставляем ровно одну легальную попытку: 2 из 3 сожжены.
    await _burn(s, ids, ids["root"], DEFAULT_MAX_ATTEMPTS - 1)
    before = await _count_results(s, ids)
    assert before == DEFAULT_MAX_ATTEMPTS - 1

    # Каждому параллельному запросу — своя попытка (счёт лимита идёт по задаче,
    # а не по попытке, поэтому попыток может быть сколько угодно).
    attempt_ids = [
        await _open_attempt(client, ids, ids["root"]) for _ in range(CONCURRENCY)
    ]

    async def _submit(attempt_id: int):
        return await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
            headers=_headers(),
        )

    responses = await asyncio.gather(*(_submit(a) for a in attempt_ids))
    codes = sorted(r.status_code for r in responses)
    accepted = [r for r in responses if r.status_code == 200]

    # Итоговый счёт в БД — главный индикатор: он не может превысить лимит.
    total = await _count_results(s, ids)

    assert total <= DEFAULT_MAX_ATTEMPTS, (
        f"ГОНКА ВОСПРОИЗВЕДЕНА: task_results={total} при лимите {DEFAULT_MAX_ATTEMPTS}; "
        f"коды={codes}; принято 200={len(accepted)} (ожидалось ровно 1). "
        f"Гейт читает счёт и пишет не под блокировкой — N запросов прошли по одному "
        f"и тому же прочитанному счёту."
    )
    assert len(accepted) == 1, (
        f"сверх лимита обязан пройти ровно один ответ, прошло {len(accepted)}; коды={codes}"
    )
