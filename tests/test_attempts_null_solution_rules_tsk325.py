# -*- coding: utf-8 -*-
"""
tsk-325 (F5): приём ответа не падает при solution_rules = JSON null.

Первопричина (аудит tsk-299): 1116 импортированных заданий ЕГЭ/Python хранят
`solution_rules = JSON null`. Прежний код в attempts.py делал
`SolutionRules.model_validate(task.solution_rules or {})` → `None or {}` = `{}`
→ валидация пустого объекта бросала ошибку на обязательном max_score → приём
ответа падал 500. Дефект латентный (0 попыток учеников на проде), но сработал бы
при первом же заходе.

Фикс: `CheckingService.build_solution_rules` деградирует пустое правило в
минимальный валидный SolutionRules (max_score из задачи). SA_COM без правил →
is_correct=None → существующий optimistic-manual (2.3d, tsk-210) уводит ответ в
ручную проверку. Приём не падает, ответ принимается.

Два уровня:
1. Юнит — build_solution_rules и документация первопричины (быстро, без БД).
2. Endpoint — POST /attempts/{id}/answers по заданию с JSON null отвечает 200,
   а не 500 (бьёт по HTTP-слою, где жил дефект). Работает с dev-БД (Learn.public),
   подчищает за собой.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError
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
from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService

_settings = Settings()
_cs = CheckingService()


# ==================== Юнит: build_solution_rules ====================


def test_empty_rules_model_validate_raises_this_is_the_root_cause():
    """Документирует первопричину: пустое правило (JSON null → {}) не валидируется —
    именно это роняло приём ответа 500 до фикса."""
    with pytest.raises(ValidationError):
        SolutionRules.model_validate({})


def test_build_solution_rules_degrades_null_to_valid_object():
    """JSON null → минимальный валидный SolutionRules с max_score из задачи."""
    sr = CheckingService.build_solution_rules(None, 5)
    assert sr.max_score == 5
    assert sr.short_answer is None
    assert sr.manual_review_required is False
    assert sr.requires_attachment is False


def test_build_solution_rules_fallback_when_max_score_missing():
    """max_score задачи отсутствует/некорректен → безопасный фолбэк 1 (модель требует >0)."""
    assert CheckingService.build_solution_rules(None, None).max_score == 1
    assert CheckingService.build_solution_rules(None, 0).max_score == 1
    assert CheckingService.build_solution_rules({}, None).max_score == 1


def test_build_solution_rules_keeps_real_rules_intact():
    """Непустое правило валидируется как раньше — деградация не трогает рабочие задания."""
    raw = {
        "max_score": 1,
        "short_answer": {"normalization": ["trim", "lower"],
                         "accepted_answers": [{"value": "17", "score": 1}]},
    }
    sr = CheckingService.build_solution_rules(raw, 1)
    assert sr.short_answer is not None
    assert sr.short_answer.accepted_answers[0].value == "17"


def test_degraded_sa_com_goes_to_manual_review():
    """SA_COM с деградированным правилом → is_correct=None (сверять нечем), не краш."""
    sr = CheckingService.build_solution_rules(None, 1)
    res = _cs.check_task(
        TaskContent.model_validate({"type": "SA_COM", "stem": "x"}),
        sr,
        StudentAnswer(type="SA_COM", response=StudentResponse(value="42")),
    )
    assert res.is_correct is None
    assert res.score == 0
    assert res.max_score == 1


# ==================== Endpoint: 200, а не 500 ====================
# asyncio_mode=auto (pytest.ini) — async-тесты определяются сами, без mark.

_SA_COM_ANSWER = {"type": "SA_COM", "response": {"value": "42"}}


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def null_rules_graph():
    """Курс + SA_COM-задание с solution_rules = JSON null + записанный ученик. Уборка."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            ids["course"] = (
                await s.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES ('tsk325 курс', 'self_guided') RETURNING id"
                    )
                )
            ).scalar()

            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            assert difficulty_id is not None, "нет difficulties — задание не собрать"

            # Ключевое: solution_rules = JSON null (ровно как у 1116 заданий на проде),
            # max_score задан на самой задаче.
            ids["task"] = (
                await s.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_attempts, max_score) VALUES "
                        "(CAST(:tc AS jsonb), CAST('null' AS jsonb), :cid, :did, :uid, :ma, :ms) "
                        "RETURNING id"
                    ),
                    {
                        "tc": '{"type":"SA_COM","stem":"Введите число"}',
                        "cid": ids["course"],
                        "did": difficulty_id,
                        "uid": "tsk325-null-task",
                        "ma": 3,
                        "ms": 1,
                    },
                )
            ).scalar()

            ids["enrolled"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk325 записан') RETURNING id")
                )
            ).scalar()
            await s.execute(
                text(
                    "INSERT INTO user_courses (user_id, course_id, is_active) "
                    "VALUES (:u, :c, true)"
                ),
                {"u": ids["enrolled"], "c": ids["course"]},
            )
            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            if "enrolled" in ids:
                await s.execute(
                    text("DELETE FROM task_results WHERE user_id = :u"), {"u": ids["enrolled"]}
                )
                await s.execute(
                    text("DELETE FROM attempts WHERE user_id = :u"), {"u": ids["enrolled"]}
                )
                await s.execute(
                    text("DELETE FROM user_courses WHERE user_id = :u"), {"u": ids["enrolled"]}
                )
            if "task" in ids:
                await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
            if "enrolled" in ids:
                await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["enrolled"]})
            if "course" in ids:
                await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
            await s.commit()
            await engine.dispose()


async def _open_attempt_service(client, user_id: int, course_id: int) -> int:
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": user_id, "course_id": course_id, "source_system": "test_tsk325"},
        headers=_service_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_null_rules_answer_does_not_500(client, null_rules_graph):
    """Регресс F5: ответ по заданию с solution_rules=null → 200, а не 500.

    До фикса эндпоинт падал на SolutionRules.model_validate({}) (обязательный
    max_score). После — SA_COM без правил уходит в ручную проверку.
    """
    ids, s = null_rules_graph
    enrolled = ids["enrolled"]
    attempt_id = await _open_attempt_service(client, enrolled, ids["course"])

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=enrolled, is_service=False
    )
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": ids["task"], "answer": _SA_COM_ANSWER}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"приём ответа по заданию без solution_rules не должен падать: {resp.text}"
    )
    body = resp.json()
    assert body["results"], "ожидался результат проверки"
    check = body["results"][0]["check_result"]
    # SA_COM без эталона → optimistic-manual (2.3d, tsk-210): не блокирует поток,
    # уходит в очередь ручной проверки. Главное — не 500.
    assert check["max_score"] == 1

    # Результат записан в task_results (ответ принят, не потерян).
    written = (
        await s.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": enrolled, "t": ids["task"]},
        )
    ).scalar()
    assert written == 1, "ответ должен быть записан в task_results"
