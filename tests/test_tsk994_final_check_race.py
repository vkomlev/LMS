"""tsk-994: одновременные ответы на одно задание итогового среза.

Тот же класс дефекта, что нашло ревью П3 (MINOR 1): замок строки обязан
держаться до записи ответа. Две вкладки с одним заданием среза не должны дать
два результата `final_check` и затереть друг другу `answers`.

Гонку нельзя проверить на одном соединении — нужны настоящие параллельные
транзакции и коммиты. Поэтому модуль работает вне общей откатываемой
транзакции и убирает за собой сам.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.core.config import Settings
from app.schemas.checking import StudentAnswer
from app.services import entitlements_service as ent
from app.services import final_check_service
from app.utils.exceptions import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.no_tx_isolation]

_settings = Settings()

#: Больше одновременных ответов — шире окно гонки.
CONCURRENCY = 4


@pytest.fixture(autouse=True)
def _gates_off(monkeypatch):
    """Подписной гейт выключен — он проверяется отдельно."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")


@pytest_asyncio.fixture(scope="function")
async def open_run():
    """Ученик прошёл курс из двух SC-заданий; срез из обоих открыт. Уборка своя."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    tag = uuid.uuid4().hex[:12]
    async with AsyncSession(engine, expire_on_commit=False) as s:
        student_id = (
            await s.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk994 гонка среза', :e, true) RETURNING id"
                ),
                {"e": f"tsk994-race-{tag}@example.test"},
            )
        ).scalar_one()
        course_id = (
            await s.execute(
                text(
                    "INSERT INTO courses (title, access_level, final_check_config) "
                    "VALUES (:t, 'self_guided', CAST(:cfg AS jsonb)) RETURNING id"
                ),
                {"t": f"tsk994-race-{tag}", "cfg": json.dumps({"enabled": True, "size": 2})},
            )
        ).scalar_one()
        difficulty_id = (
            await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()
        task_ids = []
        for n in range(2):
            task_ids.append(
                (
                    await s.execute(
                        text(
                            "INSERT INTO tasks (external_uid, max_score, task_content, "
                            "solution_rules, course_id, difficulty_id, is_active) "
                            "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true) "
                            "RETURNING id"
                        ),
                        {
                            "e": f"tsk994-race-{tag}-{n}",
                            "c": json.dumps({
                                "type": "SC", "stem": "2+2?",
                                "options": [{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
                            }),
                            "r": json.dumps({"max_score": 1, "correct_options": ["b"]}),
                            "cid": course_id,
                            "d": difficulty_id,
                        },
                    )
                ).scalar_one()
            )
        await s.execute(
            text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
            {"u": student_id, "c": course_id},
        )
        attempt_id = (
            await s.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                    "VALUES (:u, :c, :c, 'spw_web') RETURNING id"
                ),
                {"u": student_id, "c": course_id},
            )
        ).scalar_one()
        for tid in task_ids:
            await s.execute(
                text(
                    "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
                    "  is_correct, submitted_at, received_at, count_retry, source_system) "
                    "VALUES (:u, :t, :a, 1, 1, true, now(), now(), 0, 'spw_web')"
                ),
                {"u": student_id, "t": tid, "a": attempt_id},
            )
        await s.execute(
            text(
                "INSERT INTO final_check_runs (student_id, course_id, task_ids) "
                "VALUES (:u, :c, CAST(:ids AS jsonb))"
            ),
            {"u": student_id, "c": course_id, "ids": json.dumps(task_ids)},
        )
        await s.commit()

    try:
        yield engine, student_id, course_id, task_ids
    finally:
        async with AsyncSession(engine) as s:
            for sql in (
                "DELETE FROM final_check_runs WHERE student_id = :u",
                "DELETE FROM task_results WHERE user_id = :u",
                "DELETE FROM attempts WHERE user_id = :u",
                "DELETE FROM student_course_state WHERE student_id = :u",
                "DELETE FROM user_courses WHERE user_id = :u",
            ):
                await s.execute(text(sql), {"u": student_id})
            await s.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
            await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": student_id})
            await s.commit()
        await engine.dispose()


async def test_concurrent_answers_accept_exactly_one(open_run) -> None:
    """Одновременные ответы на одно задание среза: принимается один, остальные — 409."""
    engine, student_id, course_id, task_ids = open_run
    answer = StudentAnswer.model_validate(
        {"type": "SC", "response": {"selected_option_ids": ["a"]}}
    )

    async def _one() -> object:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                return await final_check_service.answer(
                    s, student_id=student_id, course_id=course_id,
                    task_id=task_ids[0], answer=answer,
                )
            except DomainError as exc:
                await s.rollback()
                return exc

    outcomes = await asyncio.gather(*(_one() for _ in range(CONCURRENCY)))
    accepted = [o for o in outcomes if isinstance(o, final_check_service.AnswerOutcome)]
    refused = [o for o in outcomes if isinstance(o, DomainError)]
    assert len(accepted) == 1, outcomes
    assert len(refused) == CONCURRENCY - 1
    assert all(e.payload.get("code") == "final_check_already_answered" for e in refused), [
        e.payload for e in refused
    ]

    async with AsyncSession(engine) as s:
        results = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM task_results "
                    "WHERE user_id = :u AND source_system = 'final_check'"
                ),
                {"u": student_id},
            )
        ).scalar_one()
        answers = (
            await s.execute(
                text("SELECT answers FROM final_check_runs WHERE student_id = :u"),
                {"u": student_id},
            )
        ).scalar_one()
        attempts = (
            await s.execute(
                text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"), {"u": student_id}
            )
        ).scalar_one()
    assert results == 1
    assert list(answers) == [str(task_ids[0])]
    # Отказанные ответы откатились целиком — лишних попыток нет.
    assert attempts == 1
