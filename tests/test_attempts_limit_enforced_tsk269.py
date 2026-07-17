"""
Форс лимита попыток на приёме ответа (tsk-269).

Находка ревью tsk-264: лимит жил только в ВЫДАЧЕ (compute_task_state → next-item
и state, me_service → syllabus). Интерфейс показывал «заблокировано», но
POST /attempts/{id}/answers ничего не проверял: клиент, зовущий API напрямую,
отвечал сколько угодно раз. Решение оператора — это дыра, сабмит обязан отдавать 409.

Тесты бьют по HTTP-эндпоинту, а не по сервису: дыра была именно в HTTP-слое,
и проверка сервиса её не поймала бы (compute_task_state и раньше честно
возвращал BLOCKED_LIMIT — его просто никто не спрашивал на приёме).

Покрывают:
- лимит исчерпан в своём корне → 409, task_results не растёт;
- тот же узел под другим корнем → 200 (tsk-264: попытки не пересекаются,
  иначе возвращается жалоба tsk-261 A7);
- попытка с пустым корнем (путь неизвестен) → 200, лимит не форсим;
- сдавший ученик (PASSED) не блокируется, даже когда попытки исчерпаны;
- пачка ответов одним запросом лимит не обходит;
- регресс: ответ в пределах лимита проходит.

Работают с dev-БД (Learn.public) на настоящем графе курсов и подчищают за собой.

Фикстура графа:
    root_a          root_b
        \\           /
         reused (переиспользуемый узел с SC-заданием)
"""
from __future__ import annotations

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

# Неверный ответ: SC-задание ждёт "b", ученик шлёт "a" → score=0, попытка сгорает.
_WRONG_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["a"]}}
_RIGHT_ANSWER = {"type": "SC", "response": {"selected_option_ids": ["b"]}}


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest_asyncio.fixture(scope="function")
async def graph():
    """Переиспользуемый узел под двумя корнями + ученик. Полная уборка за собой."""
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

            ids["root_a"] = await new_course("tsk269 корень A")
            ids["root_b"] = await new_course("tsk269 корень B")
            ids["reused"] = await new_course("tsk269 переиспользуемый узел")

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
                        "uid": "tsk269-reused",
                        "ma": DEFAULT_MAX_ATTEMPTS,
                    },
                )
            ).scalar()

            ids["user"] = (
                await s.execute(
                    text(
                        "INSERT INTO users (full_name) VALUES "
                        "('tsk269 тестовый ученик') RETURNING id"
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
            await s.execute(text("DELETE FROM task_results WHERE user_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM attempts WHERE user_id = :u"), {"u": uid})
            await s.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": uid})
            if "task" in ids:
                await s.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
            if "reused" in ids:
                await s.execute(
                    text("DELETE FROM course_parents WHERE course_id = :c"),
                    {"c": ids["reused"]},
                )
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
            await s.execute(
                text("DELETE FROM courses WHERE id = ANY(:c)"),
                {"c": [ids[k] for k in ("root_a", "root_b", "reused") if k in ids]},
            )
            await s.commit()
            await engine.dispose()


async def _burn(s: AsyncSession, ids: dict[str, int], root_course_id: int | None, count: int) -> None:
    """Сжечь `count` попыток по заданию в границах корня (мимо HTTP — это setup)."""
    for _ in range(count):
        attempt = await _attempts.create_attempt(
            s,
            user_id=ids["user"],
            course_id=ids["reused"],
            root_course_id=root_course_id,
            source_system="test_tsk269",
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


async def _open_attempt(client, ids: dict[str, int], root_course_id: int | None) -> int:
    body: dict[str, object] = {
        "user_id": ids["user"],
        "course_id": ids["reused"],
        "source_system": "test_tsk269",
    }
    if root_course_id is not None:
        body["root_course_id"] = root_course_id
    resp = await client.post("/api/v1/attempts", json=body, headers=_headers())
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _count_results(s: AsyncSession, ids: dict[str, int]) -> int:
    return (
        await s.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND task_id = :t"),
            {"u": ids["user"], "t": ids["task"]},
        )
    ).scalar()


# ── сам форс ─────────────────────────────────────────────────────────────────


async def test_submit_rejected_when_limit_exhausted(client, graph):
    """Лимит исчерпан в своём корне → 409, и лишний task_result не пишется."""
    ids, s = graph
    await _burn(s, ids, ids["root_a"], DEFAULT_MAX_ATTEMPTS)
    before = await _count_results(s, ids)

    attempt_id = await _open_attempt(client, ids, ids["root_a"])
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 409, (
        f"лимит попыток обязан форситься сервером, а не только интерфейсом: {resp.text}"
    )
    assert "лимит" in resp.json()["detail"].lower()
    assert await _count_results(s, ids) == before, "ответ сверх лимита не должен записываться"


async def test_other_root_still_accepts(client, graph):
    """Тот же узел под другим корнем принимает ответ: попытки не пересекаются (tsk-264)."""
    ids, s = graph
    await _burn(s, ids, ids["root_a"], DEFAULT_MAX_ATTEMPTS)

    attempt_id = await _open_attempt(client, ids, ids["root_b"])
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, (
        "форс лимита не должен убивать переиспользуемый узел в новом курсе — "
        f"это ровно жалоба tsk-261 A7: {resp.text}"
    )


async def test_null_root_attempt_not_forced(client, graph):
    """Попытка с неизвестным путём лимит не расходует и не форсится (tsk-264)."""
    ids, s = graph
    await _burn(s, ids, None, DEFAULT_MAX_ATTEMPTS)

    attempt_id = await _open_attempt(client, ids, None)
    # Корень мог восстановиться сам, если узел лежит в одном активном курсе;
    # здесь узел под двумя корнями → путь неоднозначен → root_course_id = null.
    root = (
        await s.execute(
            text("SELECT root_course_id FROM attempts WHERE id = :a"), {"a": attempt_id}
        )
    ).scalar()
    assert root is None, "узел под двумя корнями — путь обязан остаться неизвестным"

    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, (
        "при неизвестном пути лимит пришлось бы считать по всем курсам сразу — "
        f"сторона ошибки выбрана в пользу ученика: {resp.text}"
    )


async def test_rootless_attempt_on_unambiguous_node_still_forced(client, graph):
    """Попытка без course_id по однозначному узлу всё равно форсится.

    Находка независимого ревью tsk-269: `course_id` в теле POST /attempts
    опционален, попытка без него создаётся с пустым корнем — и гейт, завязанный
    только на `attempt.root_course_id`, молча выключался. Клиент убирал ОДНО поле
    из запроса и отвечал бесконечно. Здесь узел лежит под одним активным курсом,
    значит корень восстанавливается однозначно и пропуск ничем не оправдан.
    """
    ids, s = graph
    # Оставляем узел под ОДНИМ активным корнем: путь перестаёт быть неоднозначным.
    await s.execute(
        text("UPDATE user_courses SET is_active = false WHERE user_id = :u AND course_id = :c"),
        {"u": ids["user"], "c": ids["root_b"]},
    )
    await s.commit()
    await _burn(s, ids, ids["root_a"], DEFAULT_MAX_ATTEMPTS)
    before = await _count_results(s, ids)

    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": ids["user"], "source_system": "test_tsk269"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    attempt_id = resp.json()["id"]
    assert resp.json()["root_course_id"] is None, "фикстура бессмысленна, если корень проставился"

    answer = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert answer.status_code == 409, (
        "попытка без course_id обходит лимит — гейт остаётся гейтом интерфейса: "
        f"{answer.text}"
    )
    assert await _count_results(s, ids) == before, "ответ сверх лимита не должен записываться"


async def test_ambiguous_path_with_exhausted_limit_asks_for_root(client, graph):
    """Неоднозначный путь + исчерпанный лимит в одном из корней → 400 «укажи курс».

    Находка Б2 независимого ревью (воспроизведена): на переиспользуемом узле путь
    неоднозначен → корня нет → лимит не форсился. Счёт по корню не рос, значит
    попытки были БЕСКОНЕЧНЫ, а прогресс (PASSED) корнем не фильтруется — перебором
    добывался зачёт в том самом корне, где ученик заблокирован.

    Решение оператора: гадать корень нельзя, поэтому спрашиваем его — но только
    когда лимит на кону.
    """
    ids, s = graph
    await _burn(s, ids, ids["root_a"], DEFAULT_MAX_ATTEMPTS)
    before = await _count_results(s, ids)

    attempt_id = await _open_attempt(client, ids, None)
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 400, (
        "неоднозначный путь при исчерпанном лимите даёт бесконечные попытки: "
        f"{resp.text}"
    )
    assert "root_course_id" in resp.json()["detail"]
    assert await _count_results(s, ids) == before, "ответ не должен записываться"


async def test_ambiguous_path_within_limit_still_accepted(client, graph):
    """Честный ученик на переиспользуемом узле 400 не видит: лимит не на кону.

    Цена решения оператора обязана падать только на подозрительный случай.
    """
    ids, s = graph
    await _burn(s, ids, ids["root_a"], DEFAULT_MAX_ATTEMPTS - 1)

    attempt_id = await _open_attempt(client, ids, None)
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, (
        f"у ученика есть попытки в обоих корнях — 400 он видеть не должен: {resp.text}"
    )


async def test_passed_student_not_blocked(client, graph):
    """Сдавший ученик не блокируется, даже когда попытки исчерпаны.

    BLOCKED_LIMIT в выдаче возвращается только когда лимит исчерпан И задание
    не сдано. Приём обязан вести себя так же — иначе выдача и приём разойдутся.
    """
    ids, s = graph
    attempt = await _attempts.create_attempt(
        s,
        user_id=ids["user"],
        course_id=ids["reused"],
        root_course_id=ids["root_a"],
        source_system="test_tsk269",
    )
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        await s.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, "
                "max_score, is_correct, submitted_at) VALUES "
                "(:u, :t, :a, 1, 1, true, now())"
            ),
            {"u": ids["user"], "t": ids["task"], "a": attempt.id},
        )
    await s.execute(
        text("UPDATE attempts SET finished_at = now() WHERE id = :a"), {"a": attempt.id}
    )
    await s.commit()

    attempt_id = await _open_attempt(client, ids, ids["root_a"])
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _RIGHT_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, (
        f"сдавшего ученика лимит блокировать не должен — как и в выдаче: {resp.text}"
    )


async def test_batch_cannot_outrun_limit(client, graph):
    """Пачка ответов одним запросом лимит не обходит: счёт идёт по каждому ответу."""
    ids, s = graph
    attempt_id = await _open_attempt(client, ids, ids["root_a"])
    items = [
        {"task_id": ids["task"], "answer": _WRONG_ANSWER}
        for _ in range(DEFAULT_MAX_ATTEMPTS + 2)
    ]
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": items},
        headers=_headers(),
    )
    assert resp.status_code == 409, (
        f"пачкой ответов лимит обходить нельзя: {resp.text}"
    )
    assert await _count_results(s, ids) == DEFAULT_MAX_ATTEMPTS, (
        "записаться должны ровно попытки в пределах лимита, остальные — отбиты"
    )


async def test_answer_within_limit_still_accepted(client, graph):
    """Регресс: ответ в пределах лимита проходит как раньше."""
    ids, s = graph
    await _burn(s, ids, ids["root_a"], DEFAULT_MAX_ATTEMPTS - 1)

    attempt_id = await _open_attempt(client, ids, ids["root_a"])
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        json={"items": [{"task_id": ids["task"], "answer": _WRONG_ANSWER}]},
        headers=_headers(),
    )
    assert resp.status_code == 200, f"последняя попытка обязана приниматься: {resp.text}"
