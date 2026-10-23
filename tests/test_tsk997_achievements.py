"""tsk-997: общий движок условий достижений и учебные достижения за усилие.

Слои:

1. Чистый движок (без БД): реестр типов, битые условия не выдают награду.
2. Факты учёбы на настоящей БД: что считается «довёл до верного», «закрыл
   модуль», «спросил» — и что НЕ считается (ручная проставка, авто-заявка).
3. Тик фиксирует учебные достижения, экран удержания ими не засоряется.
4. Эндпоинты за рубильниками: выключено → ``enabled: false``; включено →
   свои достижения; преподаватель без связи с учеником → 403.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import achievements_engine as eng
from app.services import retention_service, student_achievements_service
from app.services.retention_achievements_cron_service import (
    retention_achievements_cron_tick,
)
from tests.test_tsk032_retention import (
    _enroll,
    _new_course,
    _new_task,
    _new_user,
    _submit,
)

UTC = timezone.utc


# ====================== Слой 1: чистый движок ======================


def _facts(**kw: int) -> dict[str, int]:
    f = eng.empty_learning_facts()
    f.update(kw)
    return f


def test_engine_threshold_and_progress():
    facts = _facts(fixed_after_error=3)
    assert eng.is_earned({"type": "fixed_after_error", "count": 1}, facts) is True
    assert eng.is_earned({"type": "fixed_after_error", "count": 5}, facts) is False
    assert eng.progress_of({"type": "fixed_after_error", "count": 5}, facts) == (3, 5)


def test_engine_broken_conditions_never_award():
    facts = _facts(help_asked=10)
    assert eng.is_earned({"type": "help_asked"}, facts) is False
    assert eng.is_earned({"type": "help_asked", "count": True}, facts) is False
    assert eng.is_earned({"type": "help_asked", "count": 0}, facts) is False
    assert eng.is_earned({"type": "leaderboard_top", "count": 1}, facts) is False
    assert eng.is_earned("не объект", facts) is False


def test_engine_missing_family_facts_is_not_earned():
    """Учебное условие при фактах только удержания — не «получено», не KeyError."""
    retention_only = {"weekly_streak": 9, "best_weekly_streak": 9, "items_total": 999}
    assert eng.is_earned({"type": "module_completed", "count": 1}, retention_only) is False
    assert eng.is_earned({"type": "weekly_streak", "weeks": 3}, retention_only) is True


def test_retention_wrappers_still_work():
    state = {"weekly_streak": 0, "best_weekly_streak": 4, "items_total": 0}
    assert retention_service._is_earned({"type": "weekly_streak", "weeks": 3}, state) is True


# ====================== Слой 2: факты учёбы в БД ======================


async def _student_with_course(db, name: str = "s") -> tuple[int, str, int]:
    sid, token = await _new_user(db, name=name)
    cid = await _new_course(db, "tsk997 курс")
    await _enroll(db, student_id=sid, course_id=cid)
    return sid, token, cid


@pytest.mark.asyncio
async def test_fixed_after_error_counts_only_real_fixes(db):
    sid, _, cid = await _student_with_course(db)
    t0 = datetime.now(UTC) - timedelta(days=2)
    fixed = await _new_task(db, course_id=cid, uid="fixed")
    first_ok = await _new_task(db, course_id=cid, uid="first-ok")
    manual = await _new_task(db, course_id=cid, uid="manual")
    cancelled = await _new_task(db, course_id=cid, uid="cancelled")

    await _submit(db, student_id=sid, task_id=fixed, course_id=cid, submitted_at=t0, is_correct=False)
    await _submit(db, student_id=sid, task_id=fixed, course_id=cid, submitted_at=t0 + timedelta(hours=1))
    # Верно с первого раза, а потом ошибся — это не «довёл до верного».
    await _submit(db, student_id=sid, task_id=first_ok, course_id=cid, submitted_at=t0)
    await _submit(db, student_id=sid, task_id=first_ok, course_id=cid,
                  submitted_at=t0 + timedelta(hours=1), is_correct=False)
    # Верный только ручной проставкой преподавателя — не в счёт.
    await _submit(db, student_id=sid, task_id=manual, course_id=cid, submitted_at=t0, is_correct=False)
    await _submit(db, student_id=sid, task_id=manual, course_id=cid,
                  submitted_at=t0 + timedelta(hours=1), source_system="manual_teacher")
    # Верная сдача в отменённой попытке — не в счёт.
    await _submit(db, student_id=sid, task_id=cancelled, course_id=cid, submitted_at=t0, is_correct=False)
    await _submit(db, student_id=sid, task_id=cancelled, course_id=cid,
                  submitted_at=t0 + timedelta(hours=1), cancelled=True)

    facts = (await eng.load_learning_facts(db, student_ids=[sid]))[sid]
    assert facts[eng.CONDITION_FIXED_AFTER_ERROR] == 1


@pytest.mark.asyncio
async def test_module_completed_needs_every_required_task(db):
    sid, _, root = await _student_with_course(db)
    module = await _new_course(db, "tsk997 модуль")
    await db.execute(
        text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"),
        {"c": module, "p": root},
    )
    await db.commit()
    a = await _new_task(db, course_id=module, uid="m-a")
    b = await _new_task(db, course_id=module, uid="m-b")
    opt = await _new_task(db, course_id=module, uid="m-opt")
    await db.execute(text("UPDATE tasks SET requirement_level = 'skippable' WHERE id = :t"), {"t": opt})
    await db.commit()
    t0 = datetime.now(UTC) - timedelta(hours=3)

    await _submit(db, student_id=sid, task_id=a, course_id=module, submitted_at=t0)
    facts = (await eng.load_learning_facts(db, student_ids=[sid]))[sid]
    assert facts[eng.CONDITION_MODULE_COMPLETED] == 0, "модуль закрыт без всех обязательных"

    await _submit(db, student_id=sid, task_id=b, course_id=module, submitted_at=t0)
    facts = (await eng.load_learning_facts(db, student_ids=[sid]))[sid]
    assert facts[eng.CONDITION_MODULE_COMPLETED] == 1, "необязательное задание не должно мешать"


@pytest.mark.asyncio
async def test_help_asked_ignores_auto_created(db):
    sid, _, cid = await _student_with_course(db)
    await db.execute(
        text(
            "INSERT INTO help_requests (student_id, course_id, request_type, auto_created) "
            "VALUES (:s, :c, 'blocked_limit', true)"
        ),
        {"s": sid, "c": cid},
    )
    await db.commit()
    facts = (await eng.load_learning_facts(db, student_ids=[sid]))[sid]
    assert facts[eng.CONDITION_HELP_ASKED] == 0

    await db.execute(
        text("INSERT INTO help_requests (student_id, course_id, message) VALUES (:s, :c, 'не понял')"),
        {"s": sid, "c": cid},
    )
    await db.commit()
    facts = (await eng.load_learning_facts(db, student_ids=[sid]))[sid]
    assert facts[eng.CONDITION_HELP_ASKED] == 1


# ====================== Слой 3: тик и экран удержания ======================


@pytest.mark.asyncio
async def test_tick_awards_learning_and_retention_screen_stays_clean(db, db_session_factory):
    sid, _, cid = await _student_with_course(db)
    task = await _new_task(db, course_id=cid, uid="tick")
    t0 = datetime.now(UTC) - timedelta(hours=5)
    await _submit(db, student_id=sid, task_id=task, course_id=cid, submitted_at=t0, is_correct=False)
    await _submit(db, student_id=sid, task_id=task, course_id=cid, submitted_at=t0 + timedelta(hours=1))

    await retention_achievements_cron_tick(session_factory=db_session_factory)
    names = set(
        (
            await db.execute(
                text(
                    "SELECT a.name FROM user_achievements ua JOIN achievements a "
                    "ON a.id = ua.achievement_id WHERE ua.user_id = :u"
                ),
                {"u": sid},
            )
        ).scalars()
    )
    assert "Довёл до верного" in names

    retention = await retention_service.get_retention(db, student_id=sid)
    assert all(a["name"] != "Довёл до верного" for a in retention["achievements"])
    summary = await retention_service.get_retention_summary(db, student_id=sid)
    assert summary["achievements_earned"] == len(names) - 1, "родителю подмешалось учебное"

    data = await student_achievements_service.get_student_achievements(db, student_id=sid)
    earned = [e for e in data["earned"] if e["name"] == "Довёл до верного"]
    assert earned and earned[0]["earned_at"] is not None
    nxt = {n["name"]: (n["current"], n["target"]) for n in data["next"]}
    assert nxt.get("Довёл до верного: 5 заданий") == (1, 5)
    assert "Довёл до верного: 20 заданий" not in nxt, "показываем только ближайшую ступень"


# ====================== Слой 4: эндпоинты за рубильниками ======================


@pytest.mark.asyncio
async def test_student_endpoint_off_by_default(db, client, monkeypatch):
    monkeypatch.delenv("ACHIEVEMENTS_STUDENT_UI_ENABLED", raising=False)
    _sid, token, _ = await _student_with_course(db)
    assert (await client.get("/api/v1/me/achievements")).status_code == 401
    resp = await client.get("/api/v1/me/achievements", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": False, "earned": [], "next": []}


@pytest.mark.asyncio
async def test_student_endpoint_on_shows_own_only(db, client, monkeypatch):
    monkeypatch.setenv("ACHIEVEMENTS_STUDENT_UI_ENABLED", "true")
    sid, token, cid = await _student_with_course(db)
    await db.execute(
        text("INSERT INTO help_requests (student_id, course_id, message) VALUES (:s, :c, 'помогите')"),
        {"s": sid, "c": cid},
    )
    await db.commit()
    resp = await client.get("/api/v1/me/achievements", headers={"Authorization": f"Bearer {token}"})
    body = resp.json()
    assert body["enabled"] is True
    assert any(e["name"] == "Спросил, когда застрял" for e in body["earned"])
    for key in ("rank", "peers", "leaderboard", "reward_points"):
        assert key not in body
        assert all(key not in e for e in body["earned"])


@pytest.mark.asyncio
async def test_teacher_endpoint_gate_and_acl(db, client, monkeypatch):
    sid, _, _ = await _student_with_course(db, name="st")
    teacher_id, teacher_token = await _new_user(db, name="teacher")
    role_id = (await db.execute(text("SELECT id FROM roles WHERE name = 'teacher' LIMIT 1"))).scalar()
    await db.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
        {"u": teacher_id, "r": role_id},
    )
    await db.commit()
    url = f"/api/v1/teacher/students/{sid}/achievements"
    hdr = {"Authorization": f"Bearer {teacher_token}"}

    monkeypatch.delenv("ACHIEVEMENTS_TEACHER_UI_ENABLED", raising=False)
    assert (await client.get(url, headers=hdr)).json()["enabled"] is False

    monkeypatch.setenv("ACHIEVEMENTS_TEACHER_UI_ENABLED", "true")
    assert (await client.get(url, headers=hdr)).status_code == 403, "чужой ученик"

    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
        {"s": sid, "t": teacher_id},
    )
    await db.commit()
    resp = await client.get(url, headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True
