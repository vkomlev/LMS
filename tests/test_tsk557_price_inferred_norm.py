"""tsk-557 (норматив занятий для учеников без расписания: вывод частоты из
ручной цены, продолжение tsk-556).

Проверяем на НАСТОЯЩЕЙ БД, по образцу test_tsk494_student_dashboard.py /
test_tsk505_marketer_pricing.py.

Покрывает decomposition-тесты задачи:
- точное совпадение ручной цены со ступенью сетки → частота выведена
  (`norm_source == "inferred_from_price"`, `not_conducted` считается);
- скидка/наценка (цена мимо сетки) → `unknown`, а не ближайшая ступень;
- расхождение расписание≠цена → расписание первично (`norm_source ==
  "schedule"`), расхождение только помечено флагом `discrepancy`;
- две ступени одной группы с одинаковой ценой → `unknown` (сетка сама
  неоднозначна, угадывать нельзя);
- конфликт МЕЖДУ тарифными группами одного ученика → `unknown`;
- поля видны только персоналу (`can_edit_progress`); родителю — `None`.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk557"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _new_group(db, name: str, tariffs: list[tuple[str, int, str | None, str | None]]) -> int:
    group_id = (
        await db.execute(
            text("INSERT INTO pricing_group (name) VALUES (:n) RETURNING id"),
            {"n": f"{_TAG}-{name}-{random.randint(10**6, 10**7)}"},
        )
    ).scalar()
    for idx, (tname, price, kind, value) in enumerate(tariffs):
        await db.execute(
            text(
                "INSERT INTO pricing_tariff "
                "(group_id, name, price_minor, match_kind, match_value, sort_order) "
                "VALUES (:g, :n, :p, :k, :v, :s)"
            ),
            {"g": group_id, "n": tname, "p": price, "k": kind, "v": value, "s": idx},
        )
    await db.commit()
    return group_id


async def _set_override(db, *, student_id: int, group_id: int, price_minor: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_price_override (student_id, group_id, price_minor) "
            "VALUES (:s, :g, :p)"
        ),
        {"s": student_id, "g": group_id, "p": price_minor},
    )
    await db.commit()


async def _create_slot(db, *, teacher_id: int, student_id: int, weekday: int) -> int:
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
                "VALUES (:t, :wd, '10:00', 60) RETURNING id"
            ),
            {"t": teacher_id, "wd": weekday},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
            "VALUES (:s, :u, true)"
        ),
        {"s": slot_id, "u": student_id},
    )
    await db.commit()
    return int(slot_id)


def _dt_params(period_from: datetime, period_to: datetime) -> dict[str, str]:
    return {"from": period_from.isoformat(), "to": period_to.isoformat()}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _get_attendance(client, *, student_id: int, token: str, period_from, period_to) -> dict:
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["attendance"]


# ============================== Вывод частоты ==============================


@pytest.mark.asyncio
async def test_exact_price_match_infers_frequency_without_schedule(db, client):
    """Денис Ильин (прод, 4501): расписания нет, ручная цена точно совпадает
    со ступенью «2 раза в неделю» — норматив выводится из цены."""
    _, admin_token = await _new_user(db, role="admin", name="admin-exact")
    student_id, _ = await _new_user(db, role="student", name="stud-exact")
    group_id = await _new_group(
        db, "exact", [("2 раза", 550000, "attendance_frequency", "2"),
                      ("1 раз", 275000, "attendance_frequency", "1")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=550000)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=14)
    period_to = now + timedelta(days=7)
    a = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=period_from, period_to=period_to,
    )
    assert a["norm_source"] == "inferred_from_price"
    assert a["discrepancy"] is False
    # 2/нед * 14 прошедших дней / 7 = 4 обещанных занятия, заведено 0.
    assert a["not_conducted"] == 4


@pytest.mark.asyncio
async def test_monthly_charge_manual_minor_does_not_participate(db, client):
    """`student_monthly_charge.manual_minor` — разовая правка суммы ОДНОГО
    месяца, не бессрочная договорённость о частоте. Вывод частоты смотрит
    ТОЛЬКО на `student_price_override.price_minor`: если бы `manual_minor`
    участвовал, он бы здесь резолвился в частоту "1" (275000), а override
    резолвится в "2" (550000) — при игнорировании manual_minor конфликта нет
    вовсе, частота выводится однозначно."""
    _, admin_token = await _new_user(db, role="admin", name="admin-manual")
    student_id, _ = await _new_user(db, role="student", name="stud-manual")
    group_id = await _new_group(
        db, "manual", [("2 раза", 550000, "attendance_frequency", "2"),
                       ("1 раз", 275000, "attendance_frequency", "1")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=550000)
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "(student_id, group_id, period, calculated_minor, manual_minor, "
            " expected_lessons, break_lessons) "
            "VALUES (:s, :g, date_trunc('month', now()), 550000, 275000, 0, 0)"
        ),
        {"s": student_id, "g": group_id},
    )
    await db.commit()

    now = datetime.now(UTC)
    a = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=now - timedelta(days=14), period_to=now + timedelta(days=7),
    )
    assert a["norm_source"] == "inferred_from_price"
    assert a["not_conducted"] == 4  # частота 2, не 1 — manual_minor игнорируется


@pytest.mark.asyncio
async def test_discount_price_off_grid_is_unknown_not_nearest(db, client):
    """Скидка — цена мимо сетки: `unknown`, а не подстановка ближайшей ступени."""
    _, admin_token = await _new_user(db, role="admin", name="admin-discount")
    student_id, _ = await _new_user(db, role="student", name="stud-discount")
    group_id = await _new_group(
        db, "discount", [("2 раза", 550000, "attendance_frequency", "2"),
                          ("1 раз", 275000, "attendance_frequency", "1")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=500000)

    now = datetime.now(UTC)
    a = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=now - timedelta(days=7), period_to=now,
    )
    assert a["norm_source"] == "unknown"
    assert a["not_conducted"] is None
    assert a["discrepancy"] is False


@pytest.mark.asyncio
async def test_schedule_price_discrepancy_counts_by_schedule(db, client):
    """Юлия Сесюк (прод, 4521): 1 активный слот, а цена соответствует
    ступени «2 раза в неделю». Норматив считается по расписанию (проверяемый
    факт), расхождение только помечено флагом."""
    teacher_id, _ = await _new_user(db, role="teacher", name="teach-disc")
    _, admin_token = await _new_user(db, role="admin", name="admin-disc")
    student_id, _ = await _new_user(db, role="student", name="stud-disc")
    group_id = await _new_group(
        db, "disc", [("2 раза", 550000, "attendance_frequency", "2"),
                     ("1 раз", 275000, "attendance_frequency", "1")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=550000)

    now = datetime.now(UTC)
    await _create_slot(db, teacher_id=teacher_id, student_id=student_id, weekday=now.weekday())

    a = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=now - timedelta(days=7), period_to=now + timedelta(days=7),
    )
    assert a["norm_source"] == "schedule"
    assert a["discrepancy"] is True
    assert a["not_conducted"] is None
    assert a["planned"] == a["attended"] + a["missed"] + a["upcoming"]


@pytest.mark.asyncio
async def test_two_tariffs_same_price_in_one_group_is_unknown(db, client):
    """Сетка сама неоднозначна (две ступени с одинаковой ценой) — угадывать
    между ними нельзя, исход `unknown`."""
    _, admin_token = await _new_user(db, role="admin", name="admin-ambig")
    student_id, _ = await _new_user(db, role="student", name="stud-ambig")
    group_id = await _new_group(
        db, "ambig", [("2 раза", 550000, "attendance_frequency", "2"),
                      ("почти 2", 550000, "attendance_frequency", "3")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=550000)

    now = datetime.now(UTC)
    a = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=now - timedelta(days=7), period_to=now,
    )
    assert a["norm_source"] == "unknown"
    assert a["not_conducted"] is None


@pytest.mark.asyncio
async def test_conflicting_frequencies_across_groups_is_unknown(db, client):
    """Две тарифные группы одного ученика выводят РАЗНЫЕ частоты — календарь
    не знает о курсах/группах, частота одна на всего ученика, выбор
    победителя был бы догадкой."""
    _, admin_token = await _new_user(db, role="admin", name="admin-conflict")
    student_id, _ = await _new_user(db, role="student", name="stud-conflict")
    group_a = await _new_group(
        db, "conflict-a", [("2 раза", 550000, "attendance_frequency", "2"),
                           ("1 раз", 275000, "attendance_frequency", "1")]
    )
    group_b = await _new_group(
        db, "conflict-b", [("1 раз", 300000, "attendance_frequency", "1"),
                           ("2 раза", 600000, "attendance_frequency", "2")]
    )
    await _set_override(db, student_id=student_id, group_id=group_a, price_minor=550000)  # → 2
    await _set_override(db, student_id=student_id, group_id=group_b, price_minor=300000)  # → 1

    now = datetime.now(UTC)
    a = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=now - timedelta(days=7), period_to=now,
    )
    assert a["norm_source"] == "unknown"
    assert a["not_conducted"] is None


# ============================== Видимость только персоналу ==============================


@pytest.mark.asyncio
async def test_norm_diagnostics_hidden_from_parent(db, client):
    """Родителю норматив из цены не показывается вовсе (решение оператора,
    tsk-556: «не проведено» — не о ребёнке). Персонал видит поля, родитель —
    `None`, остальные метрики посещения не меняются."""
    admin_id, admin_token = await _new_user(db, role="admin", name="admin-gate")
    parent_id, parent_token = await _new_user(db, role=None, name="parent-gate")
    student_id, _ = await _new_user(db, role="student", name="stud-gate")
    group_id = await _new_group(
        db, "gate", [("2 раза", 550000, "attendance_frequency", "2")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=550000)

    link_resp = await client.post(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers=_auth(admin_token),
    )
    assert link_resp.status_code == 204, link_resp.text

    now = datetime.now(UTC)
    period_from, period_to = now - timedelta(days=14), now + timedelta(days=7)

    staff_view = await _get_attendance(
        client, student_id=student_id, token=admin_token,
        period_from=period_from, period_to=period_to,
    )
    assert staff_view["norm_source"] == "inferred_from_price"
    assert staff_view["not_conducted"] == 4

    parent_view = await _get_attendance(
        client, student_id=student_id, token=parent_token,
        period_from=period_from, period_to=period_to,
    )
    assert parent_view["norm_source"] is None
    assert parent_view["not_conducted"] is None
    assert parent_view["discrepancy"] is None
    # Норматив из цены скрыт, но остальные метрики посещения — те же данные.
    assert parent_view["planned"] == staff_view["planned"]
    assert parent_view["attended"] == staff_view["attended"]
    assert parent_view["missed"] == staff_view["missed"]
    assert parent_view["upcoming"] == staff_view["upcoming"]
