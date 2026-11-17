"""tsk-1536: дашборд методиста — вердикт «успевает/не успевает к экзамену».

Материализованная архитектура (два инцидента 504 17.11 запретили расчёт
tsk-815 в HTTP-запросе): фоновый тик `methodist_readiness_cron_service`
пересчитывает вердикты и пишет `methodist_readiness_cache`; дашборд только
читает её. Блок `program` (`kind`, `deadline`, `on_track`, `computed_at`)
получают ученики, записанные на программные курсы ЕГЭ/ОГЭ; прочим поле
остаётся None («—» на экране). Ученик программы без строки кэша получает
блок из None — «нет данных (расчёт ещё не прошёл)». Порог вердикта — как в
сводке преподавателя (student-verdict.ts, 10 минут).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.services import methodist_readiness_cron_service as readiness
from app.services import methodist_students_dashboard_service as svc
from tests.test_methodist_students_dashboard_tsk1516 import _period_dates
from tests.test_methodist_students_dashboard_tsk1535 import (
    _active_student_with_solved_task,
)
from tests.test_teacher_lesson_summary_tsk022_410 import _new_user
from tests.test_tsk741_homework import (
    _program_student,
    _settings_with_program,
    _submit,
)

UTC = timezone.utc


@pytest.mark.parametrize(
    ("target_min", "fact_min", "expected"),
    [
        (100, 91, True),   # разрыв 9 минут — внутри порога: идёт по плану
        (100, 90, False),  # ровно 10 минут — уже «отстаёт» (порог строгий, <)
        (100, 89, False),  # разрыв 11 минут — отстаёт
    ],
)
def test_on_track_verdict_minutes_threshold(target_min, fact_min, expected):
    """Порог 10 минут — как в сводке преподавателя (student-verdict.ts)."""
    plan = SimpleNamespace(
        target_minutes_per_week=target_min,
        fact_minutes_per_week=fact_min,
        target_per_week=None,
        fact_per_week=None,
    )
    assert readiness.on_track_verdict(plan) is expected


def test_on_track_verdict_piecewise_fallback():
    """Минут нет (вес не измерен) — штучная шкала: факт >= нормы."""
    plan = SimpleNamespace(
        target_minutes_per_week=None,
        fact_minutes_per_week=None,
        target_per_week=10,
        fact_per_week=9,
    )
    assert readiness.on_track_verdict(plan) is False
    plan.fact_per_week = 10
    assert readiness.on_track_verdict(plan) is True


def test_on_track_verdict_no_data():
    """Ни минутной, ни штучной пары — «нет данных»."""
    plan = SimpleNamespace(
        target_minutes_per_week=None,
        fact_minutes_per_week=None,
        target_per_week=None,
        fact_per_week=None,
    )
    assert readiness.on_track_verdict(plan) is None


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


async def _seed_cache(db, student_id: int, *, on_track) -> None:
    """Готовая строка кэша — как будто фоновый тик уже отработал."""
    await db.execute(
        text(
            "INSERT INTO methodist_readiness_cache ("
            "  student_id, kind, deadline, on_track, details, computed_at"
            ") VALUES ("
            "  :sid, 'ege', '2027-03-31', :on_track, '{}'::json, now()"
            ")"
        ),
        {"sid": student_id, "on_track": on_track},
    )


def _row(data: dict, sid: int) -> dict:
    for row in data["students"]:
        if row["student_id"] == sid:
            return row
    raise AssertionError(f"student {sid} not in dashboard")


@pytest.mark.asyncio
async def test_dashboard_reads_cached_verdict(db, monkeypatch):
    """Дашборд отдаёт вердикт из кэша, расчёта в запросе нет."""
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )
    await _seed_cache(db, student_id, on_track=False)

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    row = _row(data, student_id)
    assert row["program"] is not None
    assert row["program"]["kind"] == "ege"
    assert row["program"]["deadline"] == "2027-03-31"
    assert row["program"]["on_track"] is False
    assert row["program"]["computed_at"] is not None


@pytest.mark.asyncio
async def test_dashboard_pending_without_cache_row(db, monkeypatch):
    """Ученик на программе, но расчёт ещё не дошёл: блок есть, всё None.

    Фронт показывает «нет данных (расчёт ещё не прошёл)», а не «—» — это
    разные состояния.
    """
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    row = _row(data, student_id)
    assert row["program"] is not None
    assert row["program"]["on_track"] is None
    assert row["program"]["computed_at"] is None


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
async def test_cron_tick_computes_and_caches(db, db_session_factory, monkeypatch):
    """Фоновый тик считает вердикт (tsk-815) и кладёт в кэш; дашборд читает."""
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )

    summary = await readiness.readiness_cron_tick(
        session_factory=db_session_factory,
    )

    assert summary["locked"] is True
    assert summary["refreshed"] == 1
    row = (
        await db.execute(
            text(
                "SELECT kind, deadline, on_track, computed_at, details "
                "FROM methodist_readiness_cache WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).mappings().one()
    assert row["kind"] == "ege"
    # Одно решение за окно — темпа на норму не хватает (tsk-815).
    assert row["on_track"] is False
    assert row["details"]

    from_d, to_d = _period_dates(datetime.now(UTC))
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    assert _row(data, student_id)["program"]["on_track"] is False


@pytest.mark.asyncio
async def test_cron_tick_skipped_when_disabled(
    db, db_session_factory, monkeypatch,
):
    """Выключенный рубильник школы: тик выходит сразу, кэш не трогает."""
    await _program_student_with_recent_pace(db, monkeypatch, done_recent=1)
    monkeypatch.setattr(settings_store, "get_bool", lambda key: False)

    summary = await readiness.readiness_cron_tick(
        session_factory=db_session_factory,
    )

    assert summary["locked"] is False
    assert summary["refreshed"] == 0
    count = (
        await db.execute(text("SELECT COUNT(*) FROM methodist_readiness_cache"))
    ).scalar()
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_cron_tick_deletes_orphans(
    db, db_session_factory, monkeypatch,
):
    """Снялся с программных курсов — вердикт из кэша удаляется тиком."""
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )
    await _seed_cache(db, student_id, on_track=True)
    await db.execute(
        text("UPDATE user_courses SET is_active = false WHERE user_id = :sid"),
        {"sid": student_id},
    )

    summary = await readiness.readiness_cron_tick(
        session_factory=db_session_factory,
    )

    assert summary["deleted_orphans"] == 1
    row = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM methodist_readiness_cache "
                "WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).scalar()
    assert int(row or 0) == 0


@pytest.mark.asyncio
async def test_refresh_student_single_warmup(db, monkeypatch):
    """«Догреть» одного ученика: публичный refresh_student пишет строку."""
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=60,
    )

    ok = await readiness.refresh_student(db, student_id=student_id)
    await db.commit()

    assert ok is True
    on_track = (
        await db.execute(
            text(
                "SELECT on_track FROM methodist_readiness_cache "
                "WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).scalar()
    assert on_track is True


@pytest.mark.asyncio
async def test_endpoint_returns_program_block(client, db, monkeypatch):
    """API: блок program проходит response_model."""
    student_id = await _program_student_with_recent_pace(
        db, monkeypatch, done_recent=1,
    )
    await _seed_cache(db, student_id, on_track=False)
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
