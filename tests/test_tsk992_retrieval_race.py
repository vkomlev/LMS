"""tsk-992 (ревью, MINOR 1): две одновременные сдачи одного повтора.

Модель дефекта. `submit_review` брал строку расписания `FOR UPDATE`, но потом
заводил курсовую попытку через `AttemptsService`, а тот фиксировал транзакцию —
замок снимался до записи стадии. Вторая вкладка проходила проверку срока со
старым `due_at`, и оба ответа записывались (потерянное обновление стадии).

Здесь проверяется, что при одновременных сдачах принимается ровно одна, а
остальные получают 409 `retrieval_not_due`; результат повтора ровно один.

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
from app.services import retrieval_service
from app.utils.exceptions import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.no_tx_isolation]

_settings = Settings()

#: Больше одновременных сдач — шире окно гонки.
CONCURRENCY = 4


@pytest.fixture(autouse=True)
def _gates_off(monkeypatch):
    """Подписной гейт выключен — он проверяется отдельно."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")


@pytest_asyncio.fixture(scope="function")
async def due_review():
    """Ученик, курс с включённым повтором, SC-задание и строка расписания,
    срок которой уже подошёл. Уборка своя."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    tag = uuid.uuid4().hex[:12]
    async with AsyncSession(engine, expire_on_commit=False) as s:
        student_id = (
            await s.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk992 гонка повтора', :e, true) RETURNING id"
                ),
                {"e": f"tsk992-race-{tag}@example.test"},
            )
        ).scalar_one()
        course_id = (
            await s.execute(
                text(
                    "INSERT INTO courses (title, access_level, retrieval_config) "
                    "VALUES (:t, 'self_guided', CAST(:cfg AS jsonb)) RETURNING id"
                ),
                {"t": f"tsk992-race-{tag}", "cfg": json.dumps({"enabled": True})},
            )
        ).scalar_one()
        difficulty_id = (
            await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()
        task_id = (
            await s.execute(
                text(
                    "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
                    "course_id, difficulty_id, is_active) "
                    "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true) "
                    "RETURNING id"
                ),
                {
                    "e": f"tsk992-race-{tag}",
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
        await s.execute(
            text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
            {"u": student_id, "c": course_id},
        )
        await s.execute(
            text(
                "INSERT INTO task_review_schedule (student_id, task_id, course_id, "
                "interval_stage, due_at) VALUES (:u, :t, :c, 0, now() - interval '1 hour')"
            ),
            {"u": student_id, "t": task_id, "c": course_id},
        )
        await s.commit()

    try:
        yield engine, student_id, course_id, task_id
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(
                text("DELETE FROM task_review_schedule WHERE student_id = :u"), {"u": student_id}
            )
            await s.execute(text("DELETE FROM task_results WHERE user_id = :u"), {"u": student_id})
            await s.execute(text("DELETE FROM attempts WHERE user_id = :u"), {"u": student_id})
            await s.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": student_id})
            await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": task_id})
            await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": student_id})
            await s.commit()
        await engine.dispose()


async def test_concurrent_reviews_accept_exactly_one(due_review) -> None:
    """Одновременные сдачи одного повтора: принимается одна, остальные — 409."""
    engine, student_id, course_id, task_id = due_review
    answer = StudentAnswer.model_validate(
        {"type": "SC", "response": {"selected_option_ids": ["b"]}}
    )

    async def _one() -> object:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                return await retrieval_service.submit_review(
                    s, student_id=student_id, task_id=task_id,
                    course_id=course_id, answer=answer,
                )
            except DomainError as exc:
                await s.rollback()
                return exc

    outcomes = await asyncio.gather(*(_one() for _ in range(CONCURRENCY)))
    accepted = [o for o in outcomes if isinstance(o, retrieval_service.ReviewOutcome)]
    refused = [o for o in outcomes if isinstance(o, DomainError)]
    assert len(accepted) == 1, outcomes
    assert len(refused) == CONCURRENCY - 1
    assert all(e.payload.get("code") == "retrieval_not_due" for e in refused), [
        e.payload for e in refused
    ]

    async with AsyncSession(engine) as s:
        results = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM task_results "
                    "WHERE user_id = :u AND source_system = 'retrieval'"
                ),
                {"u": student_id},
            )
        ).scalar_one()
        stage = (
            await s.execute(
                text("SELECT interval_stage FROM task_review_schedule WHERE student_id = :u"),
                {"u": student_id},
            )
        ).scalar_one()
        attempts = (
            await s.execute(
                text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"), {"u": student_id}
            )
        ).scalar_one()
    assert results == 1
    assert stage == 1
    # Отказанные сдачи откатились целиком — лишних пустых попыток нет.
    assert attempts == 1


async def test_failed_result_write_leaves_no_attempt(due_review, monkeypatch) -> None:
    """Запись результата упала после заведения попытки — откатывается всё:
    ни пустой попытки, ни сдвига стадии (раньше попытка успевала зафиксироваться)."""
    from app.services.task_results_service import TaskResultsService

    engine, student_id, course_id, task_id = due_review

    async def _boom(self, **kwargs):
        raise RuntimeError("запись результата упала")

    monkeypatch.setattr(TaskResultsService, "create_from_check_result", _boom)
    answer = StudentAnswer.model_validate(
        {"type": "SC", "response": {"selected_option_ids": ["b"]}}
    )
    async with AsyncSession(engine, expire_on_commit=False) as s:
        with pytest.raises(RuntimeError):
            await retrieval_service.submit_review(
                s, student_id=student_id, task_id=task_id, course_id=course_id, answer=answer,
            )
        await s.rollback()

    async with AsyncSession(engine) as s:
        attempts = (
            await s.execute(
                text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"), {"u": student_id}
            )
        ).scalar_one()
        stage = (
            await s.execute(
                text("SELECT interval_stage FROM task_review_schedule WHERE student_id = :u"),
                {"u": student_id},
            )
        ).scalar_one()
    assert attempts == 0
    assert stage == 0
