"""tsk-1536: дашборд методиста — вердикт «успевает/не успевает к экзамену».

Блок `program` (`kind`, `deadline`, `on_track`) получают ученики, записанные
на программные курсы ЕГЭ/ОГЭ; прочим поле остаётся None («—» на экране).
Расчёт — тот же `homework_volume_service.compute`, что в tsk-815; порог
вердикта — как в сводке преподавателя (student-verdict.ts, 10 минут).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.services import methodist_students_dashboard_service as svc
from tests.test_methodist_students_dashboard_tsk1516 import _period_dates
from tests.test_methodist_students_dashboard_tsk1535 import (
    _active_student_with_solved_task,
)
from tests.test_tsk741_homework import (
    _program_student,
    _settings_with_program,
    _submit,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _clear_program_pace_cache():
    """Кэш вердиктов живёт между тестами в памяти процесса — сбрасываем."""
    svc._clear_program_pace_cache()
    yield
    svc._clear_program_pace_cache()


@pytest.mark.parametrize(
    ("target_min", "fact_min", "expected"),
    [
        (100, 91, True),   # разрыв 9 минут — внутри порога: идёт по плану
        (100, 90, False),  # ровно 10 минут — уже «отстаёт» (порог строгий, <)
        (100, 89, False),  # разрыв 11 минут — отстаёт
    ],
)
def test_program_on_track_minutes_threshold(target_min, fact_min, expected):
    """Порог 10 минут — как в сводке преподавателя (student-verdict.ts)."""
    plan = SimpleNamespace(
        target_minutes_per_week=target_min,
        fact_minutes_per_week=fact_min,
        target_per_week=None,
        fact_per_week=None,
    )
    assert svc._program_on_track(plan) is expected


def test_program_on_track_piecewise_fallback():
    """Минут нет (вес не измерен) — штучная шкала: факт >= нормы."""
    plan = SimpleNamespace(
        target_minutes_per_week=None,
        fact_minutes_per_week=None,
        target_per_week=10,
        fact_per_week=9,
    )
    assert svc._program_on_track(plan) is False
    plan.fact_per_week = 10
    assert svc._program_on_track(plan) is True


def test_program_on_track_no_data():
    """Ни минутной, ни штучной пары — «нет данных»."""
    plan = SimpleNamespace(
        target_minutes_per_week=None,
        fact_minutes_per_week=None,
        target_per_week=None,
        fact_per_week=None,
    )
    assert svc._program_on_track(plan) is None


async def _program_student_with_recent_pace(
    db, monkeypatch, *, done_recent: int, total: int = 100,
) -> int:
    """Ученик программы ЕГЭ: `done_recent` решений в окне темпа (последние дни)."""
    student_id, course_id = await _program_student(
        db, done_tasks=0, total_tasks=total, grade=11,
    )
    monkeypatch.setattr(
        settings_store, "get_str", _settings_with_program(course_id),
    )
    task_ids = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c ORDER BY order_position"),
            {"c": course_id},
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for task_id in task_ids[:done_recent]:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=2),
        )
    return student_id


def _row(data: dict, sid: int) -> dict:
    for row in data["students"]:
        if row["student_id"] == sid:
            return row
    raise AssertionError(f"student {sid} not in dashboard")


@pytest.mark.asyncio
async def test_program_student_without_pace_is_not_on_track(db, monkeypatch):
    """На программе, но темп ниже нормы — «не успевает» (это сказано, tsk-815).

    Одно решение за окно — активность для дашборда есть, а темпа на норму
    не хватает.
    """
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    row = _row(data, student_id)
    assert row["program"] is not None
    assert row["program"]["kind"] == "ege"
    assert row["program"]["deadline"] is not None
    assert row["program"]["on_track"] is False


@pytest.mark.asyncio
async def test_program_student_with_pace_is_on_track(db, monkeypatch):
    """Темп выше нормы — «успевает»."""
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=60,
    )

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    assert _row(data, student_id)["program"]["on_track"] is True


@pytest.mark.asyncio
async def test_student_outside_program_gets_no_block(db, monkeypatch):
    """Ученик вне программ подготовки: поле program = None (на экране «—»)."""
    student_id = await _active_student_with_solved_task(db, tag="a1536none")
    # Явно пустые списки программных курсов: тест не должен зависеть от
    # настроек тестового окружения.
    monkeypatch.setattr(settings_store, "get_str", lambda key: "")

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    assert _row(data, student_id)["program"] is None


@pytest.mark.asyncio
async def test_endpoint_returns_program_block(client, db, monkeypatch):
    """API: блок program проходит response_model."""
    from tests.test_methodist_students_dashboard_tsk1516 import _new_user

    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )
    _methodist_id, token = await _new_user(db, role="methodist", name="a1536m")

    now = datetime.now(UTC)
    from_d, to_d = _period_dates(now)
    resp = await client.get(
        "/api/v1/methodist/students-dashboard",
        params={"from": from_d, "to": to_d},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    row = _row(resp.json(), student_id)
    assert row["program"]["kind"] == "ege"
    assert row["program"]["on_track"] is False


@pytest.mark.asyncio
async def test_on_track_is_cached_within_ttl(db, monkeypatch):
    """Повторный прогон дашборда не пересчитывает вердикт (кэш, tsk-1536).

    Инцидент 17.11: без кэша ~100 программных учеников × ~0.5 с расчёта
    давали 504 на шлюзе. Считаем вызовы compute: второй прогон — 0 новых.
    """
    from app.services import homework_volume_service

    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )
    calls = {"n": 0}
    real_compute = homework_volume_service.compute

    async def _counting_compute(session, *, student_id, now=None):
        calls["n"] += 1
        return await real_compute(session, student_id=student_id, now=now)

    monkeypatch.setattr(
        homework_volume_service, "compute", _counting_compute,
    )

    from_d, to_d = _period_dates(datetime.now(UTC))
    await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    assert calls["n"] == 1
    # Кэш свежий (TTL 20 мин): вердикт из памяти, расчёт не дёргаем.
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    assert calls["n"] == 1
    assert _row(data, student_id)["program"]["on_track"] is False


@pytest.mark.asyncio
async def test_on_track_cache_expires_with_ttl(db, monkeypatch):
    """TTL = 20 минут: после истечения вердикт пересчитывается."""
    from app.services import homework_volume_service

    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )
    calls = {"n": 0}
    real_compute = homework_volume_service.compute

    async def _counting_compute(session, *, student_id, now=None):
        calls["n"] += 1
        return await real_compute(session, student_id=student_id, now=now)

    monkeypatch.setattr(
        homework_volume_service, "compute", _counting_compute,
    )
    monkeypatch.setattr(svc, "_PROGRAM_PACE_CACHE_TTL_SEC", 0)

    from_d, to_d = _period_dates(datetime.now(UTC))
    await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    assert calls["n"] == 2
