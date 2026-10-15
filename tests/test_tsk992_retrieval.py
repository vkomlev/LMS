"""tsk-992 (П3): повторение по следам ошибок.

Решение оператора Р2: на повтор ставятся ТОЛЬКО задания «сначала завалил,
потом сдал»; интервалы 3 → 7 → 21 день; незачёт на повторе — откат к 3 дням.

Главные свойства, ради которых файл существует (критерий 5 постановки):
- неверный повтор НЕ делает пройденное задание FAILED в линейном пути
  (`compute_task_state` считает по последнему результату);
- повтор НЕ тратит `attempts_used` и лимит попыток;
- `'retrieval'` НЕ попадает в «реальные сдачи» датчика пробелов;
- при выключенном рубильнике (по умолчанию) ничего не планируется и не
  выдаётся.

Планировщик проверяется через настоящий приём ответа
(`POST /attempts/{id}/answers`) — он встроен именно туда.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.schemas.solution_rules import SolutionRules
from app.services import entitlements_service as ent
from app.services import retrieval_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService
from app.services.learning_gaps_service import (
    RETRIEVAL_SOURCE,
    real_student_results_filter,
)
from app.services.me_service import get_syllabus_states

pytestmark = pytest.mark.asyncio

_TAG = "tsk992"
_WRONG = {"type": "SC", "response": {"selected_option_ids": ["a"]}}
_RIGHT = {"type": "SC", "response": {"selected_option_ids": ["b"]}}


# ───────────────────────────── хелперы ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _gates_off(monkeypatch):
    """Подписной гейт выключен — он проверяется отдельно."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")


async def _user(db, name: str, role: str | None = None) -> tuple[int, str]:
    """Пользователь с живой сессией (bearer-токен) и, при надобности, ролью."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG} {name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role"
            ),
            {"u": u.id, "role": role},
        )
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _course(db, *, enabled: bool | None = None) -> int:
    config = None if enabled is None else json.dumps({"enabled": enabled})
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, retrieval_config) "
            "VALUES (:t, 'self_guided', CAST(:cfg AS jsonb)) RETURNING id"
        ),
        {"t": f"{_TAG}-course-{random.randint(10**8, 10**10)}", "cfg": config},
    )
    return res.scalar_one()


async def _task(
    db,
    course_id: int,
    *,
    max_attempts: int | None = None,
    content: dict | None = None,
    rules: dict | None = None,
) -> int:
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id, is_active, max_attempts) "
            "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true, :ma) "
            "RETURNING id"
        ),
        {
            "e": f"{_TAG}-{random.randint(10**8, 10**10)}",
            "c": json.dumps(
                content
                or {
                    "type": "SC",
                    "title": "Сколько будет 2+2",
                    "stem": "2+2?",
                    "options": [{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
                }
            ),
            "r": json.dumps(rules or {"max_score": 1, "correct_options": ["b"]}),
            "cid": course_id,
            "d": difficulty_id,
            "ma": max_attempts,
        },
    )
    return res.scalar_one()


async def _enroll(db, student_id: int, course_id: int) -> None:
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _setup(db, *, enabled: bool | None = True, max_attempts: int | None = None):
    """Ученик, записанный на корневой курс с одним SC-заданием."""
    student_id, token = await _user(db, "s")
    course_id = await _course(db, enabled=enabled)
    task_id = await _task(db, course_id, max_attempts=max_attempts)
    await _enroll(db, student_id, course_id)
    return student_id, token, course_id, task_id


async def _answer(client, token: str, student_id: int, course_id: int, task_id: int, answer: dict):
    """Обычная сдача ответа через учебный путь SPW."""
    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/start-or-get-attempt",
        headers=_bearer(token),
        json={"student_id": student_id, "root_course_id": course_id, "source_system": "spw_web"},
    )
    assert resp.status_code == 200, resp.text
    attempt_id = resp.json()["attempt_id"]
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        headers=_bearer(token),
        json={"items": [{"task_id": task_id, "answer": answer}]},
    )
    return resp


async def _schedule_row(db, student_id: int, task_id: int, course_id: int):
    return (
        await db.execute(
            text(
                "SELECT interval_stage, due_at, completed_at, last_result_id "
                "FROM task_review_schedule "
                "WHERE student_id = :s AND task_id = :t AND course_id = :c"
            ),
            {"s": student_id, "t": task_id, "c": course_id},
        )
    ).fetchone()


async def _make_due(db, student_id: int, task_id: int) -> None:
    await db.execute(
        text(
            "UPDATE task_review_schedule SET due_at = now() - interval '1 hour' "
            "WHERE student_id = :s AND task_id = :t"
        ),
        {"s": student_id, "t": task_id},
    )
    await db.commit()


async def _review(client, token: str, student_id: int, course_id: int, task_id: int, answer: dict):
    return await client.post(
        f"/api/v1/learning/courses/{course_id}/review/{task_id}/answer",
        headers=_bearer(token),
        json={"student_id": student_id, "answer": answer},
    )


def _close_to(value: datetime, expected: datetime, slack: timedelta = timedelta(minutes=5)) -> bool:
    assert value.tzinfo is not None, "срок обязан быть с часовым поясом"
    return abs(value - expected) <= slack


async def _fail_then_pass(client, token, student_id, course_id, task_id) -> None:
    assert (await _answer(client, token, student_id, course_id, task_id, _WRONG)).status_code == 200
    assert (await _answer(client, token, student_id, course_id, task_id, _RIGHT)).status_code == 200


# ───────────────── 1. рубильник курса ───────────────────────────────────────


async def test_retrieval_config_default_off_and_methodist_toggles(db, client) -> None:
    """По умолчанию выключено; методист включает; ученику настройка закрыта."""
    _, methodist_token = await _user(db, "m", role="methodist")
    student_id, student_token = await _user(db, "s")
    course_id = await _course(db)
    await db.commit()

    resp = await client.get(f"/api/v1/courses/{course_id}/retrieval", headers=_bearer(methodist_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": False}

    resp = await client.put(
        f"/api/v1/courses/{course_id}/retrieval",
        headers=_bearer(methodist_token),
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True}
    stored = (
        await db.execute(text("SELECT retrieval_config FROM courses WHERE id = :c"), {"c": course_id})
    ).scalar_one()
    assert stored == {"enabled": True}

    resp = await client.put(
        f"/api/v1/courses/{course_id}/retrieval",
        headers=_bearer(student_token),
        json={"enabled": False},
    )
    assert resp.status_code == 403, resp.text
    resp = await client.get("/api/v1/courses/999999999/retrieval", headers=_bearer(methodist_token))
    assert resp.status_code == 404


async def test_config_enabled_only_explicit_true() -> None:
    assert retrieval_service.config_enabled(None) is False
    assert retrieval_service.config_enabled({}) is False
    assert retrieval_service.config_enabled({"enabled": False}) is False
    assert retrieval_service.config_enabled({"enabled": "true"}) is False
    assert retrieval_service.config_enabled({"enabled": True}) is True


# ───────────────── 2. планировщик в приёме ответа ───────────────────────────


async def test_fail_then_pass_schedules_stage0_in_3_days(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)

    row = await _schedule_row(db, student_id, task_id, course_id)
    assert row is not None
    assert row.interval_stage == 0
    assert row.completed_at is None
    assert _close_to(row.due_at, datetime.now(timezone.utc) + timedelta(days=3))
    source = (
        await db.execute(
            text("SELECT source_system FROM task_results WHERE id = :r"), {"r": row.last_result_id}
        )
    ).scalar_one()
    assert source == "spw_web"


async def test_pass_first_try_not_scheduled(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    assert (await _answer(client, token, student_id, course_id, task_id, _RIGHT)).status_code == 200
    assert await _schedule_row(db, student_id, task_id, course_id) is None


async def test_only_failures_not_scheduled(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    assert (await _answer(client, token, student_id, course_id, task_id, _WRONG)).status_code == 200
    assert await _schedule_row(db, student_id, task_id, course_id) is None


@pytest.mark.parametrize("enabled", [None, False])
async def test_switch_off_by_default_nothing_scheduled(db, client, enabled) -> None:
    """Рубильник по умолчанию (NULL) и явное `false` — расписания нет."""
    student_id, token, course_id, task_id = await _setup(db, enabled=enabled)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    assert await _schedule_row(db, student_id, task_id, course_id) is None


async def test_manual_teacher_pass_not_scheduled(db) -> None:
    """Ручной зачёт преподавателя планировщик не запускает."""
    student_id, _, course_id, task_id = await _setup(db)

    async def _row(source: str, score: int) -> int:
        attempt_id = (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                    "VALUES (:u, :c, :c, :src) RETURNING id"
                ),
                {"u": student_id, "c": course_id, "src": source},
            )
        ).scalar_one()
        return (
            await db.execute(
                text(
                    "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
                    "  is_correct, submitted_at, received_at, count_retry, source_system) "
                    "VALUES (:u, :t, :a, :sc, 1, :ok, now(), now(), 0, :src) RETURNING id"
                ),
                {"u": student_id, "t": task_id, "a": attempt_id, "sc": score,
                 "ok": score > 0, "src": source},
            )
        ).scalar_one()

    await _row("spw_web", 0)
    manual_id = await _row("manual_teacher", 1)
    created = await retrieval_service.schedule_after_pass(
        db, student_id=student_id, task_id=task_id,
        root_course_id=course_id, task_result_id=manual_id,
    )
    assert created is False
    assert await _schedule_row(db, student_id, task_id, course_id) is None


async def test_scheduler_failure_does_not_break_submit(db, client, monkeypatch) -> None:
    """Планировщик нестрогий: исключение — в лог, сдача принимается."""
    student_id, token, course_id, task_id = await _setup(db)

    async def _boom(*args, **kwargs):
        raise RuntimeError("сбой планировщика")

    monkeypatch.setattr(retrieval_service, "schedule_after_pass", _boom)
    assert (await _answer(client, token, student_id, course_id, task_id, _WRONG)).status_code == 200
    resp = await _answer(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["check_result"]["is_correct"] is True
    state = await LearningEngineService().compute_task_state(db, student_id, task_id, course_id)
    assert state.state == "PASSED"


async def test_repeat_fail_pass_does_not_reset_schedule(db, client) -> None:
    """Строка ставится один раз: повторная пара «провал → сдача» её не трогает."""
    student_id, token, course_id, task_id = await _setup(db, max_attempts=10)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await db.execute(
        text(
            "UPDATE task_review_schedule SET interval_stage = 1 "
            "WHERE student_id = :s AND task_id = :t"
        ),
        {"s": student_id, "t": task_id},
    )
    await db.commit()
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    row = await _schedule_row(db, student_id, task_id, course_id)
    assert row.interval_stage == 1


def test_task_supports_review_excludes_non_auto_tasks() -> None:
    def rules(**kw) -> SolutionRules:
        return SolutionRules.model_validate({"max_score": 1, **kw})

    assert retrieval_service.task_supports_review({"type": "SC"}, rules()) is True
    assert retrieval_service.task_supports_review({"type": "SC_Qw"}, rules()) is False
    assert retrieval_service.task_supports_review({"type": "TA"}, rules()) is False
    assert retrieval_service.task_supports_review(
        {"type": "SC"}, rules(requires_attachment=True)
    ) is False
    # Короткий ответ без эталона — сверять нечем.
    assert retrieval_service.task_supports_review({"type": "SA"}, rules()) is False
    with_ref = rules(short_answer={"accepted_answers": [{"value": "101", "score": 1}]})
    assert retrieval_service.task_supports_review({"type": "SA"}, with_ref) is True
    manual = rules(
        short_answer={"accepted_answers": [{"value": "101", "score": 1}]}, manual_review_required=True
    )
    assert retrieval_service.task_supports_review({"type": "SA"}, manual) is False


# ───────────────── 3. выдача «пора повторить» ───────────────────────────────


async def test_review_due_lists_only_due_items(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    url = f"/api/v1/learning/courses/{course_id}/review-due?student_id={student_id}"

    resp = await client.get(url, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"course_id": course_id, "items": []}  # срок через 3 дня

    await _make_due(db, student_id, task_id)
    items = (await client.get(url, headers=_bearer(token))).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["task_id"] == task_id
    assert item["title"] == "Сколько будет 2+2"
    assert item["interval_stage"] == 0
    assert item["task_type"] == "SC"
    assert item["days_since_solved"] == 0


async def test_review_due_empty_when_switched_off(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    await db.execute(
        text("UPDATE courses SET retrieval_config = '{\"enabled\": false}' WHERE id = :c"),
        {"c": course_id},
    )
    await db.commit()
    resp = await client.get(
        f"/api/v1/learning/courses/{course_id}/review-due?student_id={student_id}",
        headers=_bearer(token),
    )
    assert resp.json()["items"] == []


async def test_review_due_foreign_student_forbidden(db, client) -> None:
    student_id, _, course_id, _ = await _setup(db)
    _, other_token = await _user(db, "o")
    resp = await client.get(
        f"/api/v1/learning/courses/{course_id}/review-due?student_id={student_id}",
        headers=_bearer(other_token),
    )
    assert resp.status_code == 403


# ───────────────── 4. сдача повтора ─────────────────────────────────────────


async def test_wrong_review_keeps_passed_limit_and_gaps(db, client) -> None:
    """Критерий 5: неверный повтор не делает задание FAILED и не тратит лимит.

    Лимит 2: провал + сдача исчерпывают его до конца, так что любая утечка
    повтора в счёт дала бы 3 из 2.
    """
    student_id, token, course_id, task_id = await _setup(db, max_attempts=2)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    engine = LearningEngineService()
    before = await engine.compute_task_state(db, student_id, task_id, course_id)
    assert (before.state, before.attempts_used) == ("PASSED", 2)
    course_before = await engine.compute_course_state(
        db, student_id, course_id, update_state_table=False
    )

    await _make_due(db, student_id, task_id)
    resp = await _review(client, token, student_id, course_id, task_id, _WRONG)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_correct"] is False
    assert body["interval_stage"] == 0
    assert body["completed"] is False
    assert _close_to(
        datetime.fromisoformat(body["next_due_at"]),
        datetime.now(timezone.utc) + timedelta(days=3),
    )

    # Результат записан как повтор, в существующую курсовую попытку.
    tr = (
        await db.execute(
            text(
                "SELECT tr.source_system, tr.is_correct, a.root_course_id "
                "FROM task_results tr JOIN attempts a ON a.id = tr.attempt_id "
                "WHERE tr.id = :r"
            ),
            {"r": body["task_result_id"]},
        )
    ).fetchone()
    assert (tr.source_system, tr.is_correct, tr.root_course_id) == (RETRIEVAL_SOURCE, False, course_id)
    attempts_count = (
        await db.execute(text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"), {"u": student_id})
    ).scalar_one()
    assert attempts_count == 1, "повтор не заводит своей попытки"

    # Линейный путь: статус и счёт попыток — как до повтора.
    after = await engine.compute_task_state(db, student_id, task_id, course_id)
    assert after.state == "PASSED"
    assert after.attempts_used == 2
    assert (await engine.compute_task_state(db, student_id, task_id)).state == "PASSED"
    batch = await engine.compute_task_states_batch(
        db, student_id, [task_id], root_course_id=course_id
    )
    assert (batch[task_id].state, batch[task_id].attempts_used) == ("PASSED", 2)
    course_after = await engine.compute_course_state(
        db, student_id, course_id, update_state_table=False
    )
    assert course_after == course_before

    # Программа курса у ученика: задание пройдено, попыток 2.
    syllabus = await get_syllabus_states(db, student_id, course_id)
    task_items = [i for i in syllabus["items"] if i.get("kind") == "task" and i.get("task_id") == task_id]
    assert task_items, syllabus
    assert task_items[0]["status"] == "passed"
    assert task_items[0]["attempts_used"] == 2

    # Обычная сдача после неверного повтора не упирается в лимит.
    resp = await _answer(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 200, resp.text

    # Датчик пробелов: повтор — не «реальная сдача».
    real = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM task_results tr "
                f"WHERE tr.user_id = :u AND {real_student_results_filter('tr')}"
            ),
            {"u": student_id},
        )
    ).scalar_one()
    assert real == 3  # провал, сдача и сдача после повтора; повтор не в счёт

    # П4 не задет: своя сдача по заданию есть.
    assert await engine.has_own_attempt(db, student_id, task_id, course_id) is True


async def test_correct_reviews_walk_3_7_21_then_complete(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)

    for expected_stage, days in ((1, 7), (2, 21)):
        await _make_due(db, student_id, task_id)
        body = (await _review(client, token, student_id, course_id, task_id, _RIGHT)).json()
        assert body["is_correct"] is True
        assert body["interval_stage"] == expected_stage
        assert _close_to(
            datetime.fromisoformat(body["next_due_at"]),
            datetime.now(timezone.utc) + timedelta(days=days),
        )

    await _make_due(db, student_id, task_id)
    body = (await _review(client, token, student_id, course_id, task_id, _RIGHT)).json()
    assert body["completed"] is True
    assert body["next_due_at"] is None
    row = await _schedule_row(db, student_id, task_id, course_id)
    assert row.completed_at is not None

    # Закреплённое из выдачи пропадает, и повторно его не сдать.
    resp = await client.get(
        f"/api/v1/learning/courses/{course_id}/review-due?student_id={student_id}",
        headers=_bearer(token),
    )
    assert resp.json()["items"] == []
    resp = await _review(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 404
    assert resp.json()["payload"]["code"] == "retrieval_not_scheduled"


async def test_wrong_review_on_later_stage_rolls_back_to_3_days(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    assert (await _review(client, token, student_id, course_id, task_id, _RIGHT)).json()["interval_stage"] == 1
    await _make_due(db, student_id, task_id)
    body = (await _review(client, token, student_id, course_id, task_id, _WRONG)).json()
    assert body["interval_stage"] == 0
    assert _close_to(
        datetime.fromisoformat(body["next_due_at"]),
        datetime.now(timezone.utc) + timedelta(days=3),
    )


async def test_review_refusals(db, client) -> None:
    student_id, token, course_id, task_id = await _setup(db)

    # Не на повторении.
    resp = await _review(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 404
    assert resp.json()["payload"]["code"] == "retrieval_not_scheduled"

    # Срок не подошёл.
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    resp = await _review(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 409
    assert resp.json()["payload"]["code"] == "retrieval_not_due"

    # Чужой ученик.
    await _make_due(db, student_id, task_id)
    _, other_token = await _user(db, "o")
    resp = await _review(client, other_token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 403

    # Рубильник выключен.
    await db.execute(
        text("UPDATE courses SET retrieval_config = NULL WHERE id = :c"), {"c": course_id}
    )
    await db.commit()
    resp = await _review(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 409
    assert resp.json()["payload"]["code"] == "retrieval_disabled"

    # Ни один отказ не оставил результата.
    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND source_system = :src"),
            {"u": student_id, "src": RETRIEVAL_SOURCE},
        )
    ).scalar_one()
    assert count == 0


async def test_unsupported_task_hidden_from_due_list(db, client) -> None:
    """Задание, ставшее ручной проверкой после постановки, из выдачи пропадает:
    сдать его нельзя (409 `retrieval_unsupported`), и висеть вечно ему незачем."""
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    await db.execute(
        text(
            "UPDATE tasks SET task_content = CAST(:c AS jsonb), "
            "solution_rules = CAST(:r AS jsonb) WHERE id = :t"
        ),
        {
            "c": json.dumps({"type": "TA", "stem": "Объясни своими словами"}),
            "r": json.dumps({"max_score": 1}),
            "t": task_id,
        },
    )
    await db.commit()
    resp = await client.get(
        f"/api/v1/learning/courses/{course_id}/review-due?student_id={student_id}",
        headers=_bearer(token),
    )
    assert resp.json()["items"] == []
    resp = await _review(
        client, token, student_id, course_id, task_id,
        {"type": "TA", "response": {"text": "ответ"}},
    )
    assert resp.status_code == 409
    assert resp.json()["payload"]["code"] == "retrieval_unsupported"


async def test_second_review_on_same_due_is_refused(db, client) -> None:
    """Повтор принимается один раз на срок: вторая сдача видит сдвинутый срок."""
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    assert (await _review(client, token, student_id, course_id, task_id, _WRONG)).status_code == 200
    resp = await _review(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 409
    assert resp.json()["payload"]["code"] == "retrieval_not_due"


# ───────────────── гейты сдачи и выдачи повтора (ревью, MINOR 2–4) ───────────


async def test_unenrolled_student_review_refused(db, client) -> None:
    """Снятый с курса ученик (возврат оплаты) повтор не сдаёт: строка расписания
    осталась, но ACL записи на курс (tsk-272) отказывает, результат не пишется."""
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    await db.execute(
        text("UPDATE user_courses SET is_active = false WHERE user_id = :u"),
        {"u": student_id},
    )
    await db.commit()

    resp = await _review(client, token, student_id, course_id, task_id, _RIGHT)
    assert resp.status_code == 403, resp.text
    row = await _schedule_row(db, student_id, task_id, course_id)
    assert row.interval_stage == 0
    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM task_results WHERE user_id = :u AND source_system = :src"),
            {"u": student_id, "src": RETRIEVAL_SOURCE},
        )
    ).scalar_one()
    assert count == 0


async def test_sa_com_review_requires_comment(db, client) -> None:
    """Гейт 2.3f (tsk-419) действует и на повторе: без комментария — 422, стадия
    не сдвигается; с комментарием повтор принимается."""
    student_id, token = await _user(db, "s")
    course_id = await _course(db, enabled=True)
    task_id = await _task(
        db,
        course_id,
        content={"type": "SA_COM", "title": "Ответ числом", "stem": "Сколько?"},
        rules={
            "max_score": 1,
            "short_answer": {
                "normalization": ["trim", "lower"],
                "accepted_answers": [{"value": "42", "score": 1}],
            },
        },
    )
    await _enroll(db, student_id, course_id)
    wrong = {"type": "SA_COM", "response": {"value": "1", "comment": "так решил"}}
    right = {"type": "SA_COM", "response": {"value": "42", "comment": "посчитал"}}
    assert (await _answer(client, token, student_id, course_id, task_id, wrong)).status_code == 200
    assert (await _answer(client, token, student_id, course_id, task_id, right)).status_code == 200
    assert await _schedule_row(db, student_id, task_id, course_id) is not None
    await _make_due(db, student_id, task_id)

    resp = await _review(
        client, token, student_id, course_id, task_id,
        {"type": "SA_COM", "response": {"value": "42"}},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["payload"]["code"] == "evidence_required"
    assert (await _schedule_row(db, student_id, task_id, course_id)).interval_stage == 0

    resp = await _review(client, token, student_id, course_id, task_id, right)
    assert resp.status_code == 200, resp.text
    assert resp.json()["interval_stage"] == 1


async def test_due_list_hides_task_no_longer_auto_checked(db, client) -> None:
    """Методист сделал вложение обязательным после постановки на повтор — задание
    пропадает из «пора повторить», а не висит там вечно с 409 на сдаче."""
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)

    async def _due_ids() -> list[int]:
        resp = await client.get(
            f"/api/v1/learning/courses/{course_id}/review-due",
            headers=_bearer(token), params={"student_id": student_id},
        )
        assert resp.status_code == 200, resp.text
        return [i["task_id"] for i in resp.json()["items"]]

    assert await _due_ids() == [task_id]
    await db.execute(
        text(
            "UPDATE tasks SET solution_rules = solution_rules || "
            "CAST(:patch AS jsonb) WHERE id = :t"
        ),
        {"t": task_id, "patch": json.dumps({"requires_attachment": True})},
    )
    await db.commit()
    assert await _due_ids() == []


async def test_review_due_checks_payment_gate(db, client, monkeypatch) -> None:
    """`GET review-due` закрывается просроченной оплатой, как соседние GET
    учебного пути; проверяется УЧЕНИК."""
    from app.services import payment_access_service
    from app.utils.exceptions import DomainError

    student_id, token, course_id, _ = await _setup(db)
    seen: list[int] = []

    async def _blocked(_db, sid: int) -> None:
        seen.append(sid)
        raise DomainError("оплата просрочена", status_code=403, payload={"code": "payment_overdue"})

    monkeypatch.setattr(payment_access_service, "assert_content_allowed", _blocked)
    resp = await client.get(
        f"/api/v1/learning/courses/{course_id}/review-due",
        headers=_bearer(token), params={"student_id": student_id},
    )
    assert resp.status_code == 403
    assert seen == [student_id]


# ───────── №5: отказ гейта — не «провал» (решение оператора 2026-10-15) ─────────


async def _sa_com_setup(db):
    """Ученик и SA_COM-задание с эталоном «42» на курсе с включённым повтором."""
    student_id, token = await _user(db, "s")
    course_id = await _course(db, enabled=True)
    task_id = await _task(
        db,
        course_id,
        content={"type": "SA_COM", "title": "Ответ числом", "stem": "Сколько?"},
        rules={
            "max_score": 1,
            "short_answer": {
                "normalization": ["trim", "lower"],
                "accepted_answers": [{"value": "42", "score": 1}],
            },
        },
    )
    await _enroll(db, student_id, course_id)
    return student_id, token, course_id, task_id


async def test_comment_gate_rejection_is_not_a_fail(db, client) -> None:
    """Верный ответ без комментария (гейт 2.3f → 0 баллов), затем с комментарием —
    задание НЕ встаёт на повтор: первым разом ученик не ошибся."""
    student_id, token, course_id, task_id = await _sa_com_setup(db)
    no_comment = {"type": "SA_COM", "response": {"value": "42"}}
    right = {"type": "SA_COM", "response": {"value": "42", "comment": "посчитал"}}
    resp = await _answer(client, token, student_id, course_id, task_id, no_comment)
    assert resp.status_code == 200, resp.text
    assert (await _answer(client, token, student_id, course_id, task_id, right)).status_code == 200
    assert await _schedule_row(db, student_id, task_id, course_id) is None


async def test_real_fail_after_gate_rejection_still_schedules(db, client) -> None:
    """Отказ гейта исключается точечно: настоящий неверный ответ рядом с ним
    по-прежнему ставит задание на повтор."""
    student_id, token, course_id, task_id = await _sa_com_setup(db)
    for answer in (
        {"type": "SA_COM", "response": {"value": "42"}},
        {"type": "SA_COM", "response": {"value": "1", "comment": "так решил"}},
        {"type": "SA_COM", "response": {"value": "42", "comment": "посчитал"}},
    ):
        assert (await _answer(client, token, student_id, course_id, task_id, answer)).status_code == 200
    assert await _schedule_row(db, student_id, task_id, course_id) is not None


async def test_time_limit_rejection_is_not_a_fail(db, client) -> None:
    """Ответ после истечения таймлимита (0 баллов от гейта 2.3c), потом верный в
    новой попытке — на повтор не ставится."""
    student_id, token, course_id, task_id = await _setup(db)
    await db.execute(text("UPDATE tasks SET time_limit_sec = 60 WHERE id = :t"), {"t": task_id})
    await db.commit()

    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/start-or-get-attempt",
        headers=_bearer(token),
        json={"student_id": student_id, "root_course_id": course_id, "source_system": "spw_web"},
    )
    attempt_id = resp.json()["attempt_id"]
    await db.execute(
        text("UPDATE attempts SET created_at = now() - interval '1 hour' WHERE id = :a"),
        {"a": attempt_id},
    )
    await db.commit()
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        headers=_bearer(token),
        json={"items": [{"task_id": task_id, "answer": _RIGHT}]},
    )
    assert resp.status_code == 200, resp.text
    late = (
        await db.execute(
            text("SELECT score FROM task_results WHERE attempt_id = :a"), {"a": attempt_id}
        )
    ).scalar_one()
    assert late == 0, "предпосылка: просроченный ответ получил 0 от гейта"

    assert (await _answer(client, token, student_id, course_id, task_id, _RIGHT)).status_code == 200
    assert await _schedule_row(db, student_id, task_id, course_id) is None


# ───────── №6: повтор не выдаётся за обычную сдачу ─────────


async def _fail_pass_wrong_review(db, client):
    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    resp = await _review(client, token, student_id, course_id, task_id, _WRONG)
    assert resp.status_code == 200, resp.text
    return student_id, token, course_id, task_id, resp.json()


async def test_task_history_marks_review_and_keeps_numbering(db, client) -> None:
    student_id, token, _, task_id, _ = await _fail_pass_wrong_review(db, client)
    resp = await client.get(f"/api/v1/me/tasks/{task_id}/history", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    rows = [(a["attempt_no"], a["is_retrieval"]) for a in resp.json()["attempts"]]
    assert rows == [(1, False), (2, False), (2, True)]


async def test_me_history_failed_filter_skips_review(db, client) -> None:
    student_id, token, _, task_id, review = await _fail_pass_wrong_review(db, client)

    async def _hist(flt: str) -> list[dict]:
        resp = await client.get(
            "/api/v1/me/history", headers=_bearer(token), params={"filter": flt}
        )
        assert resp.status_code == 200, resp.text
        return [i for i in resp.json() if i["task_id"] == task_id]

    failed = await _hist("failed")
    assert [i["task_result_id"] for i in failed] != [] and all(not i["is_retrieval"] for i in failed)
    assert review["task_result_id"] not in [i["task_result_id"] for i in failed]
    everything = await _hist("all")
    marked = {i["task_result_id"]: i["is_retrieval"] for i in everything}
    assert marked[review["task_result_id"]] is True


async def test_attempt_totals_exclude_review(db, client) -> None:
    _, token, _, task_id, review = await _fail_pass_wrong_review(db, client)
    resp = await client.get(f"/api/v1/attempts/{review['attempt_id']}", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["results"]) == 2  # неверная + верная сдача, без повтора
    assert body["total_score"] == 1 and body["total_max_score"] == 2


async def test_review_counts_as_activity_for_streak(db, client) -> None:
    """День, когда ученик только повторял, — активный день серии."""
    from app.services.me_service import get_streak

    student_id, *_ = await _fail_pass_wrong_review(db, client)
    await db.execute(
        text(
            "UPDATE task_results SET received_at = received_at - interval '10 days' "
            "WHERE user_id = :u AND source_system IS DISTINCT FROM :src"
        ),
        {"u": student_id, "src": RETRIEVAL_SOURCE},
    )
    await db.commit()
    assert (await get_streak(db, student_id))["today_active"] is True


async def test_teacher_feed_says_repeated(db, client) -> None:
    from app.services.teacher_activity_feed_service import _fetch_task_solved

    student_id, *_ = await _fail_pass_wrong_review(db, client)
    events = await _fetch_task_solved(db, teacher_id=0, elevated=True, limit=50, before=None)
    mine = [e for e in events if e["student_id"] == student_id]
    assert len(mine) == 3
    assert "повторил задание" in mine[0]["summary"] and "(неверно)" in mine[0]["summary"]
    assert all("решил задание" in e["summary"] for e in mine[1:])


async def test_lesson_summary_last_task_skips_review(db, client) -> None:
    from app.services.teacher_lesson_summary_service import _load_last_activity

    student_id, token, course_id, task_id = await _setup(db)
    await _fail_then_pass(client, token, student_id, course_id, task_id)
    await _make_due(db, student_id, task_id)
    assert (await _review(client, token, student_id, course_id, task_id, _RIGHT)).status_code == 200
    real_pass_at = (
        await db.execute(
            text(
                "SELECT max(submitted_at) FROM task_results "
                "WHERE user_id = :u AND is_correct AND source_system IS DISTINCT FROM :src"
            ),
            {"u": student_id, "src": RETRIEVAL_SOURCE},
        )
    ).scalar_one()
    last = await _load_last_activity(db, student_id=student_id)
    assert last is not None
    assert last["kind"] == "task" and last["timestamp"] == real_pass_at
