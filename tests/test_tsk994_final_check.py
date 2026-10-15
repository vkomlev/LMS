"""tsk-994 (П5): итоговый срез после курса.

Решение оператора Р4: срез — только диагностика. Статус курса, зависимости и
выпуск он не меняет.

Главные свойства, ради которых файл существует (критерии 1, 6 постановки):
- срез недоступен до COMPLETED и при выключенном рубильнике (по умолчанию) —
  409 `final_check_locked`;
- набор детерминирован, равномерен по главам и сохраняется: повторное открытие
  даёт тот же состав;
- ответ среза (`source_system='final_check'`) НЕ меняет статус задания
  (`compute_task_state` и batch), статус курса, счёт и лимит попыток, пробелы,
  П4 `has_own_attempt` и планировщик П3;
- итог: доля верных, главы с ошибками; ошибки при включённом П3 — в повторение.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import entitlements_service as ent
from app.services import final_check_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService
from app.services.learning_gaps_service import (
    FINAL_CHECK_SOURCE,
    learning_path_results_filter,
    own_activity_results_filter,
    real_student_results_filter,
)
from app.services.me_service import get_syllabus_states
from app.services.task_sampling import deterministic_seed

# asyncio_mode = auto (pytest.ini): асинхронные тесты метки не требуют.

_TAG = "tsk994"
_WRONG = {"type": "SC", "response": {"selected_option_ids": ["a"]}}
_RIGHT = {"type": "SC", "response": {"selected_option_ids": ["b"]}}


# ───────────────────────────── хелперы ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _gates_off(monkeypatch):
    """Подписной гейт выключен — он проверяется отдельно."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")


def _rnd() -> int:
    return random.randint(10**8, 10**10)


async def _user(db, name: str, role: str | None = None) -> tuple[int, str]:
    """Пользователь с живой сессией (bearer-токен) и, при надобности, ролью."""
    u = Users(
        email=f"{_TAG}-{name}-{_rnd()}@example.com",
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


async def _course(db, title: str, *, final: dict | None = None, retrieval: bool | None = None) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, final_check_config, retrieval_config) "
            "VALUES (:t, 'self_guided', CAST(:fc AS jsonb), CAST(:rc AS jsonb)) RETURNING id"
        ),
        {
            "t": f"{_TAG}-{title}-{_rnd()}",
            "fc": None if final is None else json.dumps(final),
            "rc": None if retrieval is None else json.dumps({"enabled": retrieval}),
        },
    )
    return res.scalar_one()


async def _child(db, parent_id: int, title: str) -> int:
    cid = await _course(db, title)
    await db.execute(
        text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"),
        {"c": cid, "p": parent_id},
    )
    return cid


async def _task(db, course_id: int, *, max_attempts: int | None = None, title: str = "2+2") -> int:
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id, is_active, max_attempts) "
            "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true, :ma) "
            "RETURNING id"
        ),
        {
            "e": f"{_TAG}-{_rnd()}",
            "c": json.dumps(
                {
                    "type": "SC",
                    "title": f"Задание {title}",
                    "stem": "2+2?",
                    "options": [{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
                }
            ),
            "r": json.dumps({"max_score": 1, "correct_options": ["b"]}),
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


async def _answer(client, token: str, student_id: int, root_id: int, task_id: int, answer: dict):
    """Обычная сдача ответа через учебный путь SPW."""
    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/start-or-get-attempt",
        headers=_bearer(token),
        json={"student_id": student_id, "root_course_id": root_id, "source_system": "spw_web"},
    )
    assert resp.status_code == 200, resp.text
    attempt_id = resp.json()["attempt_id"]
    resp = await client.post(
        f"/api/v1/attempts/{attempt_id}/answers",
        headers=_bearer(token),
        json={"items": [{"task_id": task_id, "answer": answer}]},
    )
    return resp


class _Setup:
    """Корень с двумя главами по `per_chapter` заданий; ученик записан на корень."""

    def __init__(self, student_id, token, root_id, chapters, tasks):
        self.student_id = student_id
        self.token = token
        self.root_id = root_id
        self.chapters: list[int] = chapters
        self.tasks: dict[int, list[int]] = tasks

    @property
    def all_tasks(self) -> list[int]:
        return [t for ch in self.chapters for t in self.tasks[ch]]


async def _setup(
    db,
    *,
    final: dict | None = None,
    retrieval: bool | None = None,
    per_chapter: int = 3,
    max_attempts: int | None = None,
) -> _Setup:
    student_id, token = await _user(db, "s")
    root_id = await _course(db, "root", final=final, retrieval=retrieval)
    chapters = [await _child(db, root_id, "ch1"), await _child(db, root_id, "ch2")]
    tasks = {
        ch: [await _task(db, ch, max_attempts=max_attempts, title=f"{n}") for n in range(per_chapter)]
        for ch in chapters
    }
    await db.commit()
    await _enroll(db, student_id, root_id)
    return _Setup(student_id, token, root_id, chapters, tasks)


async def _pass_all(client, s: _Setup) -> None:
    for tid in s.all_tasks:
        resp = await _answer(client, s.token, s.student_id, s.root_id, tid, _RIGHT)
        assert resp.status_code == 200, resp.text


async def _status(client, s: _Setup) -> dict:
    resp = await client.get(
        f"/api/v1/learning/courses/{s.root_id}/final-check",
        headers=_bearer(s.token),
        params={"student_id": s.student_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _start(client, s: _Setup):
    return await client.post(
        f"/api/v1/learning/courses/{s.root_id}/final-check/start",
        headers=_bearer(s.token),
        json={"student_id": s.student_id},
    )


async def _fc_answer(client, s: _Setup, task_id: int, answer: dict):
    return await client.post(
        f"/api/v1/learning/courses/{s.root_id}/final-check/{task_id}/answer",
        headers=_bearer(s.token),
        json={"student_id": s.student_id, "answer": answer},
    )


_ON = {"enabled": True, "size": 4}


# ───────────────── 1. рубильник курса ───────────────────────────────────────


async def test_config_default_off_and_methodist_toggles(db, client) -> None:
    """По умолчанию выключено (size 10); методист включает; ученику закрыто."""
    _, methodist_token = await _user(db, "m", role="methodist")
    _, student_token = await _user(db, "s")
    course_id = await _course(db, "cfg")
    await db.commit()

    url = f"/api/v1/courses/{course_id}/final-check"
    resp = await client.get(url, headers=_bearer(methodist_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": False, "size": 10}

    resp = await client.put(url, headers=_bearer(methodist_token), json={"enabled": True, "size": 6})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True, "size": 6}
    stored = (
        await db.execute(text("SELECT final_check_config FROM courses WHERE id = :c"), {"c": course_id})
    ).scalar_one()
    assert stored == {"enabled": True, "size": 6}

    resp = await client.put(url, headers=_bearer(methodist_token), json={"enabled": True, "size": 0})
    assert resp.status_code == 422
    resp = await client.put(url, headers=_bearer(student_token), json={"enabled": False})
    assert resp.status_code == 403, resp.text
    resp = await client.get("/api/v1/courses/999999999/final-check", headers=_bearer(methodist_token))
    assert resp.status_code == 404


def test_parse_config_only_explicit_true() -> None:
    parse = final_check_service.parse_config
    assert parse(None) == (False, 10)
    assert parse({}) == (False, 10)
    assert parse({"enabled": "true"}) == (False, 10)
    assert parse({"enabled": True}) == (True, 10)
    assert parse({"enabled": True, "size": 5}) == (True, 5)
    assert parse({"enabled": True, "size": 0}) == (True, 10)
    assert parse({"enabled": True, "size": True}) == (True, 10)
    assert parse({"enabled": True, "size": "5"}) == (True, 10)


# ───────────────── 2. выборка ───────────────────────────────────────────────


def test_select_is_deterministic_and_even_by_chapters() -> None:
    pools = [(1, list(range(100, 110))), (2, list(range(200, 205))), (3, [300])]
    picked = final_check_service.select_task_ids(pools, size=7, student_id=5, course_id=9)
    assert picked == final_check_service.select_task_ids(
        list(reversed(pools))[::-1], size=7, student_id=5, course_id=9
    )
    assert len(picked) == 7 and len(set(picked)) == 7
    by_ch = {ch: [t for t in picked if t in ids] for ch, ids in pools}
    # Глава 3 даёт единственное задание, остаток делится поровну между 1 и 2.
    assert [len(by_ch[1]), len(by_ch[2]), len(by_ch[3])] == [3, 3, 1]
    # Порядок показа — по главам в порядке программы.
    assert picked == by_ch[1] + by_ch[2] + by_ch[3]


def test_select_takes_all_when_pool_small_and_handles_empty() -> None:
    pools = [(1, [10, 11]), (2, []), (3, [30])]
    picked = final_check_service.select_task_ids(pools, size=10, student_id=1, course_id=1)
    assert sorted(picked) == [10, 11, 30]
    assert final_check_service.select_task_ids([(1, [])], size=5, student_id=1, course_id=1) == []
    assert final_check_service.select_task_ids(pools, size=0, student_id=1, course_id=1) == []


def test_select_more_chapters_than_size_one_per_chapter() -> None:
    pools = [(c, [c * 10, c * 10 + 1]) for c in range(1, 9)]
    picked = final_check_service.select_task_ids(pools, size=3, student_id=7, course_id=3)
    chapters = {t // 10 for t in picked}
    assert len(picked) == 3 and len(chapters) == 3, "по одному заданию на разные главы"
    assert picked == final_check_service.select_task_ids(pools, size=3, student_id=7, course_id=3)


def test_seed_salt_keeps_old_seed() -> None:
    """Соль «final» даёт свой сид; без соли сид прежний (выборка tsk-314 цела)."""
    import hashlib

    old = int(hashlib.sha256(b"5:9").hexdigest()[:16], 16)
    assert deterministic_seed(5, 9) == old
    assert deterministic_seed(5, 9, salt="final") != old


# ───────────────── 3. гейт доступа ──────────────────────────────────────────


async def test_disabled_by_default_status_and_start_locked(db, client) -> None:
    s = await _setup(db)
    await _pass_all(client, s)
    body = await _status(client, s)
    assert body["status"] == "disabled" and body["run"] is None
    resp = await _start(client, s)
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"]["code"] == "final_check_locked"


async def test_locked_until_course_completed(db, client) -> None:
    s = await _setup(db, final=_ON)
    first = s.all_tasks[0]
    assert (await _answer(client, s.token, s.student_id, s.root_id, first, _RIGHT)).status_code == 200
    assert (await _status(client, s))["status"] == "locked"
    resp = await _start(client, s)
    assert resp.status_code == 409 and "final_check_locked" in resp.text
    # Ответ на задание до старта и до завершения курса — тоже 409.
    resp = await _fc_answer(client, s, first, _RIGHT)
    assert resp.status_code == 409 and "final_check_locked" in resp.text
    exists = (
        await db.execute(
            text("SELECT COUNT(*) FROM final_check_runs WHERE student_id = :s"), {"s": s.student_id}
        )
    ).scalar_one()
    assert exists == 0


async def test_foreign_student_forbidden(db, client) -> None:
    s = await _setup(db, final=_ON)
    _, other_token = await _user(db, "o")
    resp = await client.get(
        f"/api/v1/learning/courses/{s.root_id}/final-check",
        headers=_bearer(other_token),
        params={"student_id": s.student_id},
    )
    assert resp.status_code == 403
    resp = await client.post(
        f"/api/v1/learning/courses/{s.root_id}/final-check/start",
        headers=_bearer(other_token),
        json={"student_id": s.student_id},
    )
    assert resp.status_code == 403


async def test_alumni_can_see_but_not_take(db, client) -> None:
    """Выпускник: работа в курсе закрыта (tsk-673) — срез тоже; итог виден."""
    s = await _setup(db, final=_ON)
    await _pass_all(client, s)
    # Подписку при входе могла завести самопочинка учётки — меняем тариф у неё.
    await db.execute(
        text("DELETE FROM student_subscription WHERE student_id = :sid"), {"sid": s.student_id}
    )
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id) "
            "SELECT :sid, id FROM subscription_plan WHERE code = 'alumni'"
        ),
        {"sid": s.student_id},
    )
    await db.commit()
    # Карточку «Пройти срез» выпускнику не показываем — статус `closed`.
    assert (await _status(client, s))["status"] == "closed"
    resp = await _start(client, s)
    assert resp.status_code == 403 and "course_work_closed" in resp.text


# ───────────────── 4. состав среза ──────────────────────────────────────────


async def test_start_selects_passed_tasks_evenly_and_persists(db, client) -> None:
    s = await _setup(db, final=_ON)
    await _pass_all(client, s)
    assert (await _status(client, s))["status"] == "ready"

    resp = await _start(client, s)
    assert resp.status_code == 200, resp.text
    run = resp.json()
    ids = [i["task_id"] for i in run["items"]]
    assert len(ids) == 4 and len(set(ids)) == 4
    per_ch = {ch: sum(i["chapter_id"] == ch for i in run["items"]) for ch in s.chapters}
    assert per_ch == {s.chapters[0]: 2, s.chapters[1]: 2}
    assert all(i["answered"] is False and i["is_correct"] is None for i in run["items"])
    assert run["result"] is None and run["finished_at"] is None

    # Повторное открытие — тот же набор; в БД одна строка.
    again = (await _start(client, s)).json()
    assert [i["task_id"] for i in again["items"]] == ids
    stored = (
        await db.execute(
            text("SELECT task_ids FROM final_check_runs WHERE student_id = :s AND course_id = :c"),
            {"s": s.student_id, "c": s.root_id},
        )
    ).scalars().all()
    assert stored == [ids]
    status = await _status(client, s)
    assert status["status"] == "in_progress"
    assert [i["task_id"] for i in status["run"]["items"]] == ids


async def test_manual_credit_not_selected(db, client) -> None:
    """Ручной зачёт преподавателя — не своё решение: в срез не берётся."""
    s = await _setup(db, final={"enabled": True, "size": 10}, per_chapter=2)
    manual_task = s.all_tasks[0]
    for tid in s.all_tasks[1:]:
        assert (await _answer(client, s.token, s.student_id, s.root_id, tid, _RIGHT)).status_code == 200
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :r, 'manual_teacher') RETURNING id"
            ),
            {"u": s.student_id, "c": s.chapters[0], "r": s.root_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, source_system) "
            "VALUES (:u, :t, :a, 1, 1, true, now(), now(), 0, 'manual_teacher')"
        ),
        {"u": s.student_id, "t": manual_task, "a": attempt_id},
    )
    await db.commit()
    resp = await _start(client, s)
    assert resp.status_code == 200, resp.text
    ids = {i["task_id"] for i in resp.json()["items"]}
    assert ids == set(s.all_tasks[1:])


# ───────────────── 5. ответы не меняют учебный путь ─────────────────────────


async def test_wrong_answer_keeps_passed_limit_course_gaps_and_p4(db, client) -> None:
    """Критерий 6: неверный ответ среза не трогает статусы, лимит и пробелы.

    Лимит 2: провал + сдача исчерпывают его, так что любая утечка среза в
    счёт дала бы 3 из 2.
    """
    s = await _setup(db, final={"enabled": True, "size": 10}, per_chapter=1, max_attempts=2)
    target = s.all_tasks[0]
    assert (await _answer(client, s.token, s.student_id, s.root_id, target, _WRONG)).status_code == 200
    await _pass_all(client, s)
    engine = LearningEngineService()
    before = await engine.compute_task_state(db, s.student_id, target, s.root_id)
    assert (before.state, before.attempts_used) == ("PASSED", 2)
    course_before = await engine.compute_course_state(
        db, s.student_id, s.root_id, update_state_table=False
    )
    assert course_before.state == "COMPLETED"
    attempts_before = (
        await db.execute(text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"), {"u": s.student_id})
    ).scalar_one()

    async def _count(flt: str) -> int:
        return (
            await db.execute(
                text(f"SELECT COUNT(*) FROM task_results tr WHERE tr.user_id = :u AND {flt}"),
                {"u": s.student_id},
            )
        ).scalar_one()

    real_before = await _count(real_student_results_filter("tr"))

    assert (await _start(client, s)).status_code == 200
    resp = await _fc_answer(client, s, target, _WRONG)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_correct"] is False
    src = (
        await db.execute(
            text(
                "SELECT tr.source_system, a.root_course_id FROM task_results tr "
                "JOIN attempts a ON a.id = tr.attempt_id WHERE tr.id = :r"
            ),
            {"r": body["task_result_id"]},
        )
    ).fetchone()
    assert (src.source_system, src.root_course_id) == (FINAL_CHECK_SOURCE, s.root_id)

    after = await engine.compute_task_state(db, s.student_id, target, s.root_id)
    assert (after.state, after.attempts_used) == ("PASSED", 2)
    assert (await engine.compute_task_state(db, s.student_id, target)).state == "PASSED"
    batch = await engine.compute_task_states_batch(
        db, s.student_id, [target], root_course_id=s.root_id
    )
    assert (batch[target].state, batch[target].attempts_used) == ("PASSED", 2)
    course_after = await engine.compute_course_state(
        db, s.student_id, s.root_id, update_state_table=False
    )
    assert course_after == course_before

    syllabus = await get_syllabus_states(db, s.student_id, s.root_id)
    item = [i for i in syllabus["items"] if i.get("kind") == "task" and i.get("task_id") == target]
    assert item and item[0]["status"] == "passed" and item[0]["attempts_used"] == 2

    # Своей попытки срез не заводит; в пробелы не попадает; П4 цел.
    attempts_after = (
        await db.execute(text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"), {"u": s.student_id})
    ).scalar_one()
    assert attempts_after == attempts_before
    assert await _count(real_student_results_filter("tr")) == real_before
    assert await _count(learning_path_results_filter("tr")) == real_before
    assert await _count(own_activity_results_filter("tr")) == real_before + 1
    assert await engine.has_own_attempt(db, s.student_id, target, s.root_id) is True

    # Лимит не израсходован: обычная сдача после неверного среза принимается.
    resp = await _answer(client, s.token, s.student_id, s.root_id, target, _RIGHT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["check_result"]["is_correct"] is True


async def test_status_read_does_not_touch_course_state_cache(db, client) -> None:
    """GET среза только читает: кеш `student_course_state` не переписывается."""
    s = await _setup(db, final=_ON)
    await _pass_all(client, s)
    await db.execute(
        text("DELETE FROM student_course_state WHERE student_id = :s"), {"s": s.student_id}
    )
    await db.commit()
    assert (await _status(client, s))["status"] == "ready"
    rows = (
        await db.execute(
            text("SELECT COUNT(*) FROM student_course_state WHERE student_id = :s"),
            {"s": s.student_id},
        )
    ).scalar_one()
    assert rows == 0


# ───────────────── 6. итог ──────────────────────────────────────────────────


async def _run_with(client, s: _Setup, wrong: set[int]) -> dict:
    run = (await _start(client, s)).json()
    last = None
    for item in run["items"]:
        tid = item["task_id"]
        resp = await _fc_answer(client, s, tid, _WRONG if tid in wrong else _RIGHT)
        assert resp.status_code == 200, resp.text
        last = resp.json()
    assert last is not None
    return last["run"]


async def test_result_with_errors_lists_chapters_without_retrieval(db, client) -> None:
    s = await _setup(db, final=_ON)
    await _pass_all(client, s)
    run = (await _start(client, s)).json()
    wrong_task = next(i for i in run["items"] if i["chapter_id"] == s.chapters[1])["task_id"]
    final = await _run_with(client, s, {wrong_task})

    assert final["finished_at"] is not None
    result = final["result"]
    assert (result["total"], result["correct"]) == (4, 3)
    assert result["ratio"] == 0.75 and result["skill_retained"] is False
    assert [c["course_id"] for c in result["chapters_with_errors"]] == [s.chapters[1]]
    assert result["chapters_with_errors"][0]["task_ids"] == [wrong_task]
    assert result["scheduled_for_review"] == []  # П3 на курсе выключен
    rows = (
        await db.execute(
            text("SELECT COUNT(*) FROM task_review_schedule WHERE student_id = :s"),
            {"s": s.student_id},
        )
    ).scalar_one()
    assert rows == 0

    status = await _status(client, s)
    assert status["status"] == "finished" and status["run"]["result"] == result
    # Пройденный срез второй раз не пройти; курс остался COMPLETED (Р4).
    resp = await _fc_answer(client, s, wrong_task, _RIGHT)
    assert resp.status_code == 409 and "final_check_finished" in resp.text
    state = await LearningEngineService().compute_course_state(
        db, s.student_id, s.root_id, update_state_table=False
    )
    assert state.state == "COMPLETED"


async def test_all_correct_skill_retained(db, client) -> None:
    s = await _setup(db, final=_ON)
    await _pass_all(client, s)
    result = (await _run_with(client, s, set()))["result"]
    assert result["skill_retained"] is True and result["ratio"] == 1.0
    assert result["chapters_with_errors"] == []


def test_skill_retained_threshold_is_80_percent() -> None:
    layout = final_check_service.CourseLayout(
        tree_ids=[1], chapters=[final_check_service.Chapter(1, None, "Курс")], chapter_of={1: 1}
    )
    ids = list(range(10))

    def _result(n_right: int) -> dict:
        answers = {str(t): {"result_id": t, "is_correct": t < n_right} for t in ids}
        return final_check_service.build_result(ids, answers, layout, {t: 1 for t in ids}, [])

    assert _result(8)["skill_retained"] is True
    assert _result(7)["skill_retained"] is False


async def test_errors_go_to_retrieval_when_p3_enabled(db, client) -> None:
    s = await _setup(db, final=_ON, retrieval=True)
    await _pass_all(client, s)
    run = (await _start(client, s)).json()
    wrong_task = run["items"][0]["task_id"]
    # Задание уже «закреплено» повтором — ошибка в срезе открывает его снова.
    await db.execute(
        text(
            "INSERT INTO task_review_schedule (student_id, task_id, course_id, interval_stage, "
            "  due_at, completed_at) VALUES (:s, :t, :c, 2, now(), now())"
        ),
        {"s": s.student_id, "t": wrong_task, "c": s.root_id},
    )
    await db.commit()
    final = await _run_with(client, s, {wrong_task})
    assert final["result"]["scheduled_for_review"] == [wrong_task]
    row = (
        await db.execute(
            text(
                "SELECT interval_stage, due_at, completed_at, last_result_id "
                "FROM task_review_schedule WHERE student_id = :s AND task_id = :t"
            ),
            {"s": s.student_id, "t": wrong_task},
        )
    ).fetchone()
    assert row.interval_stage == 0 and row.completed_at is None
    assert row.due_at.tzinfo is not None
    assert abs(row.due_at - (datetime.now(timezone.utc) + timedelta(days=3))) < timedelta(minutes=5)
    src = (
        await db.execute(
            text("SELECT source_system FROM task_results WHERE id = :r"), {"r": row.last_result_id}
        )
    ).scalar_one()
    assert src == FINAL_CHECK_SOURCE


async def test_final_check_fail_does_not_trigger_p3_scheduler(db, client) -> None:
    """Ошибка среза — не «провал до сдачи» для планировщика П3."""
    s = await _setup(db, final=_ON, max_attempts=10)
    await _pass_all(client, s)
    run = (await _start(client, s)).json()
    wrong_task = run["items"][0]["task_id"]
    await _run_with(client, s, {wrong_task})
    # П3 включили ПОСЛЕ среза; обычная верная сдача не ставит задание на повтор.
    await db.execute(
        text("UPDATE courses SET retrieval_config = '{\"enabled\": true}' WHERE id = :c"),
        {"c": s.root_id},
    )
    await db.commit()
    resp = await _answer(client, s.token, s.student_id, s.root_id, wrong_task, _RIGHT)
    assert resp.status_code == 200, resp.text
    rows = (
        await db.execute(
            text("SELECT COUNT(*) FROM task_review_schedule WHERE student_id = :s"),
            {"s": s.student_id},
        )
    ).scalar_one()
    assert rows == 0


# ───────────────── 7. отказы ────────────────────────────────────────────────


async def test_answer_refusals(db, client) -> None:
    s = await _setup(db, final=_ON)
    await _pass_all(client, s)
    resp = await _fc_answer(client, s, s.all_tasks[0], _RIGHT)
    assert resp.status_code == 404 and "final_check_not_started" in resp.text

    run = (await _start(client, s)).json()
    in_run = [i["task_id"] for i in run["items"]]
    outside = next(t for t in s.all_tasks if t not in in_run)
    resp = await _fc_answer(client, s, outside, _RIGHT)
    assert resp.status_code == 404 and "final_check_task_not_in_run" in resp.text

    assert (await _fc_answer(client, s, in_run[0], _RIGHT)).status_code == 200
    resp = await _fc_answer(client, s, in_run[0], _WRONG)
    assert resp.status_code == 409 and "final_check_already_answered" in resp.text

    resp = await _fc_answer(client, s, in_run[1], {"type": "SA", "response": {"value": "4"}})
    assert resp.status_code == 400


async def test_task_no_longer_auto_checked_is_skipped(db, client) -> None:
    """Задание перестало проверяться автоматически — исключается, срез не виснет."""
    s = await _setup(db, final={"enabled": True, "size": 2}, per_chapter=1)
    await _pass_all(client, s)
    run = (await _start(client, s)).json()
    broken, ok = run["items"][0]["task_id"], run["items"][1]["task_id"]
    await db.execute(
        text(
            "UPDATE tasks SET solution_rules = solution_rules || "
            "'{\"manual_review_required\": true}'::jsonb, task_content = jsonb_set("
            "task_content, '{type}', '\"SA\"') WHERE id = :t"
        ),
        {"t": broken},
    )
    await db.commit()
    resp = await _fc_answer(client, s, broken, {"type": "SA", "response": {"value": "4"}})
    assert resp.status_code == 409 and "final_check_unsupported" in resp.text
    resp = await _fc_answer(client, s, ok, _RIGHT)
    assert resp.status_code == 200, resp.text
    result = resp.json()["run"]["result"]
    assert (result["total"], result["correct"], result["skill_retained"]) == (1, 1, True)
    skipped = next(i for i in resp.json()["run"]["items"] if i["task_id"] == broken)
    assert skipped["answered"] is True and skipped["is_correct"] is None


# ───────────────── 8. пометка в истории и ленте ─────────────────────────────


async def test_history_and_feed_mark_final_check(db, client) -> None:
    from app.services.teacher_activity_feed_service import _fetch_task_solved

    s = await _setup(db, final={"enabled": True, "size": 2}, per_chapter=1)
    await _pass_all(client, s)
    run = (await _start(client, s)).json()
    target = run["items"][0]["task_id"]
    resp = await _fc_answer(client, s, target, _WRONG)
    assert resp.status_code == 200, resp.text
    fc_result_id = resp.json()["task_result_id"]

    resp = await client.get(f"/api/v1/me/tasks/{target}/history", headers=_bearer(s.token))
    assert resp.status_code == 200, resp.text
    rows = [(a["attempt_no"], a["is_final_check"]) for a in resp.json()["attempts"]]
    assert rows == [(1, False), (1, True)]

    resp = await client.get("/api/v1/me/history", headers=_bearer(s.token), params={"filter": "failed"})
    assert fc_result_id not in [i["task_result_id"] for i in resp.json()]
    resp = await client.get("/api/v1/me/history", headers=_bearer(s.token), params={"filter": "all"})
    marked = {i["task_result_id"]: i["is_final_check"] for i in resp.json()}
    assert marked[fc_result_id] is True

    events = await _fetch_task_solved(db, teacher_id=0, elevated=True, limit=50, before=None)
    mine = [e["summary"] for e in events if e["student_id"] == s.student_id]
    assert sum("проверил в итоговом срезе" in m for m in mine) == 1
    assert sum("решил задание" in m for m in mine) == 2

    # GET попытки: ответ среза не входит в результаты и суммы.
    attempt_id = (
        await db.execute(
            text("SELECT attempt_id FROM task_results WHERE id = :r"), {"r": fc_result_id}
        )
    ).scalar_one()
    resp = await client.get(f"/api/v1/attempts/{attempt_id}", headers=_bearer(s.token))
    assert resp.status_code == 200, resp.text
    assert fc_result_id not in [r.get("task_result_id", r.get("id")) for r in resp.json()["results"]]
    assert resp.json()["total_score"] == resp.json()["total_max_score"]


async def test_deleted_task_is_skipped_on_reopen(db, client) -> None:
    """Задание удалили из базы посреди среза — повторное открытие его исключает."""
    s = await _setup(db, final={"enabled": True, "size": 2}, per_chapter=1)
    await _pass_all(client, s)
    run = (await _start(client, s)).json()
    gone, ok = run["items"][0]["task_id"], run["items"][1]["task_id"]
    assert (await _fc_answer(client, s, ok, _RIGHT)).status_code == 200
    await db.execute(text("DELETE FROM task_results WHERE task_id = :t"), {"t": gone})
    await db.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": gone})
    await db.commit()
    # Неотвеченных видимых заданий нет, итога ещё нет — клиент открывает срез снова.
    status = await _status(client, s)
    assert status["status"] == "in_progress" and status["run"]["result"] is None
    resp = await _start(client, s)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert (result["total"], result["correct"], result["skill_retained"]) == (1, 1, True)
    assert (await _status(client, s))["status"] == "finished"
