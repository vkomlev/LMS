"""tsk-511/512/513 — помесячные начисления, автопересчёт, перерывы.

Проверяем не «эндпоинт отвечает 200», а денежные инварианты: доля перерыва,
неприкосновенность закрытого месяца, перенос расхождения вперёд без задваивания,
устойчивость ручной цены к смене расписания.
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest
from sqlalchemy import text

from app.services import charge_service
from tests.test_tsk505_marketer_pricing import (
    _auth,
    _enroll,
    _new_course,
    _new_group,
    _new_user,
    _price_course,
)

pytestmark = pytest.mark.asyncio

#: Месяц с понедельниками ровно 4 раза — чтобы доли считались предсказуемо.
PERIOD = date(2026, 9, 1)


async def _slot_on(db, *, student_id: int, teacher_id: int, weekday: int) -> int:
    """Посадить ученика в недельный слот на конкретный день недели (0 = пн)."""
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot "
                "(teacher_id, weekday, start_time, duration_minutes, timezone, is_active) "
                "VALUES (:t, :w, '10:00', 60, 'Europe/Moscow', true) RETURNING id"
            ),
            {"t": teacher_id, "w": weekday},
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


async def _setup(db, tag: str, *, weekdays: tuple[int, ...] = (0,), price: int = 550000):
    """Ученик на платном курсе с тарифом «единственный вариант» и расписанием."""
    marketer_id, token = await _new_user(db, role="marketer", name=f"m-{tag}")
    teacher_id, _ = await _new_user(db, role="teacher", name=f"t-{tag}")
    student_id, _ = await _new_user(db, role="student", name=f"s-{tag}")
    group_id = await _new_group(db, tag, [("Общий", price, None, None)])
    course_id = await _new_course(db, tag)
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    for wd in weekdays:
        await _slot_on(db, student_id=student_id, teacher_id=teacher_id, weekday=wd)
    return {
        "token": token,
        "student_id": student_id,
        "teacher_id": teacher_id,
        "group_id": group_id,
        "course_id": course_id,
    }


async def _charge(client, token: str, student_id: int, period: date = PERIOD) -> dict | None:
    resp = await client.get(
        f"/api/v1/marketer/charges?period={period.isoformat()}", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return next((c for c in resp.json() if c["student_id"] == student_id), None)


async def test_month_base_comes_from_permanent_schedule(db, client):
    """База месяца берётся из расписания, а не из сгенерированных занятий.

    Занятия на проде существуют лишь на три недели вперёд — считай мы по ним,
    сумма зависела бы от того, когда крутили генератор.
    """
    env = await _setup(db, "base", weekdays=(0,))
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PERIOD
    )
    # В сентябре 2026 понедельников ровно 4, занятий не сгенерировано ни одного.
    assert counts.expected == 4
    assert counts.on_break == 0

    generated = (
        await db.execute(
            text(
                "SELECT count(*) FROM lesson_occurrence "
                "WHERE scheduled_at >= CAST(:p AS date) "
                "  AND scheduled_at < CAST(:p AS date) + INTERVAL '1 month'"
            ),
            {"p": PERIOD},
        )
    ).scalar()
    assert generated == 0, "проверка бессмысленна, если занятия вдруг сгенерированы"


async def test_break_reduces_amount_by_missed_lessons(db, client):
    """Перерыв на 2 недели при 4 занятиях в месяце срезает половину."""
    env = await _setup(db, "half", weekdays=(0,), price=550000)
    await charge_service.recalculate_month(db, period=PERIOD)

    before = await _charge(client, env["token"], env["student_id"])
    assert before["total_minor"] == 550000
    assert before["expected_lessons"] == 4

    resp = await client.post(
        "/api/v1/methodist/breaks",
        json={
            "student_id": env["student_id"],
            "starts_on": "2026-09-07",
            "ends_on": "2026-09-20",
            "note": "поездка",
        },
        headers=_auth((await _new_user(db, role="methodist", name="me-half"))[1]),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["paused_lessons"] == 0  # занятий не сгенерировано — гасить нечего

    after = await _charge(client, env["token"], env["student_id"])
    assert after["break_lessons"] == 2
    assert after["total_minor"] == 275000, "две недели из четырёх — половина цены"


async def test_schedule_change_recalculates_open_month(db, client):
    """Смена расписания 1 → 2 занятия меняет сумму открытого месяца."""
    env = await _setup(db, "sched", weekdays=(0,), price=550000)
    await charge_service.recalculate_month(db, period=PERIOD)
    assert (await _charge(client, env["token"], env["student_id"]))["expected_lessons"] == 4

    await _slot_on(
        db, student_id=env["student_id"], teacher_id=env["teacher_id"], weekday=2
    )
    await charge_service.recalculate_month(db, period=PERIOD)

    after = await _charge(client, env["token"], env["student_id"])
    assert after["expected_lessons"] == 9, "4 понедельника + 5 сред в сентябре 2026"


async def test_closed_month_is_frozen_and_delta_carries_forward(db, client):
    """Закрытый месяц не переписывается, расхождение уходит в следующий."""
    env = await _setup(db, "closed", weekdays=(0,), price=550000)
    await charge_service.recalculate_month(db, period=PERIOD)

    close = await client.post(
        "/api/v1/marketer/charges/close",
        json={"period": PERIOD.isoformat()},
        headers=_auth(env["token"]),
    )
    assert close.status_code == 200, close.text

    methodist_token = (await _new_user(db, role="methodist", name="me-closed"))[1]
    resp = await client.post(
        "/api/v1/methodist/breaks",
        json={
            "student_id": env["student_id"],
            "starts_on": "2026-09-07",
            "ends_on": "2026-09-20",
        },
        headers=_auth(methodist_token),
    )
    assert resp.status_code == 201, resp.text

    frozen = await _charge(client, env["token"], env["student_id"])
    assert frozen["status"] == "closed"
    assert frozen["total_minor"] == 550000, "закрытый месяц не переписывают задним числом"

    nxt = await _charge(client, env["token"], env["student_id"], date(2026, 10, 1))
    assert nxt is not None, "перенос должен был создать следующий месяц"
    assert nxt["adjustments_minor"] == -275000
    assert "Перенос за 09.2026" in (nxt["adjustment_details"] or "")


async def test_carry_forward_does_not_double_on_repeated_recalc(db, client):
    """Повторный пересчёт закрытого месяца не задваивает перенос."""
    env = await _setup(db, "nodouble", weekdays=(0,), price=550000)
    await charge_service.recalculate_month(db, period=PERIOD)
    await client.post(
        "/api/v1/marketer/charges/close",
        json={"period": PERIOD.isoformat()},
        headers=_auth(env["token"]),
    )
    methodist_token = (await _new_user(db, role="methodist", name="me-nodouble"))[1]
    await client.post(
        "/api/v1/methodist/breaks",
        json={
            "student_id": env["student_id"],
            "starts_on": "2026-09-07",
            "ends_on": "2026-09-20",
        },
        headers=_auth(methodist_token),
    )

    for _ in range(3):
        await charge_service.recalculate_month(db, period=PERIOD)

    rows = (
        await db.execute(
            text(
                "SELECT count(*) AS n, coalesce(sum(amount_minor), 0) AS total "
                "  FROM charge_adjustment "
                " WHERE student_id = :s AND source = 'carry_forward'"
            ),
            {"s": env["student_id"]},
        )
    ).one()
    assert rows.n == 1, "перенос из одного месяца должен быть ровно один"
    assert rows.total == -275000


async def test_manual_price_survives_schedule_change(db, client):
    """Ручная цена держится при смене расписания и не пропорционируется."""
    env = await _setup(db, "manual", weekdays=(0,), price=550000)
    resp = await client.put(
        "/api/v1/marketer/price-overrides",
        json={
            "student_id": env["student_id"],
            "group_id": env["group_id"],
            "price_minor": 300000,
            "note": "договорённость",
        },
        headers=_auth(env["token"]),
    )
    assert resp.status_code == 200, resp.text

    await charge_service.recalculate_month(db, period=PERIOD)
    before = await _charge(client, env["token"], env["student_id"])
    assert before["total_minor"] == 300000
    assert before["has_price_override"] is True

    await _slot_on(
        db, student_id=env["student_id"], teacher_id=env["teacher_id"], weekday=2
    )
    await charge_service.recalculate_month(db, period=PERIOD)

    after = await _charge(client, env["token"], env["student_id"])
    assert after["total_minor"] == 300000, "ручная цена не сбрасывается расписанием"


async def test_manual_price_is_not_prorated_by_break(db, client):
    """Договорённость с человеком не уезжает от перерыва сама собой."""
    env = await _setup(db, "manbreak", weekdays=(0,), price=550000)
    await client.put(
        "/api/v1/marketer/price-overrides",
        json={
            "student_id": env["student_id"],
            "group_id": env["group_id"],
            "price_minor": 300000,
        },
        headers=_auth(env["token"]),
    )
    methodist_token = (await _new_user(db, role="methodist", name="me-manbreak"))[1]
    await client.post(
        "/api/v1/methodist/breaks",
        json={
            "student_id": env["student_id"],
            "starts_on": "2026-09-01",
            "ends_on": "2026-09-30",
        },
        headers=_auth(methodist_token),
    )
    await charge_service.recalculate_month(db, period=PERIOD)

    row = await _charge(client, env["token"], env["student_id"])
    assert row["total_minor"] == 300000


async def test_break_pauses_only_scheduled_and_restores_on_delete(db, client):
    """Перерыв гасит только запланированные занятия; снятие возвращает их."""
    env = await _setup(db, "pause", weekdays=(0,))
    teacher_id = env["teacher_id"]
    student_id = env["student_id"]

    made: dict[str, int] = {}
    for tag, day, status in (
        ("будущее", date(2026, 9, 7), "scheduled"),
        ("отмечено", date(2026, 9, 14), "confirmed"),
        ("вне перерыва", date(2026, 9, 28), "scheduled"),
    ):
        occ_id = (
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                    "VALUES (:t, :at, 60) RETURNING id"
                ),
                {"t": teacher_id, "at": datetime(day.year, day.month, day.day, 10, tzinfo=timezone(timedelta(hours=3)))},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
                "VALUES (:o, :s, :st)"
            ),
            {"o": occ_id, "s": student_id, "st": status},
        )
        made[tag] = int(occ_id)
    await db.commit()

    methodist_token = (await _new_user(db, role="methodist", name="me-pause"))[1]
    created = await client.post(
        "/api/v1/methodist/breaks",
        json={
            "student_id": student_id,
            "starts_on": "2026-09-07",
            "ends_on": "2026-09-20",
        },
        headers=_auth(methodist_token),
    )
    assert created.status_code == 201, created.text

    async def status_of(occ_id: int) -> str:
        return (
            await db.execute(
                text(
                    "SELECT status FROM lesson_occurrence_participant "
                    "WHERE occurrence_id = :o AND student_id = :s"
                ),
                {"o": occ_id, "s": student_id},
            )
        ).scalar()

    assert await status_of(made["будущее"]) == "on_break"
    assert await status_of(made["отмечено"]) == "confirmed", "факт не переписываем"
    assert await status_of(made["вне перерыва"]) == "scheduled"

    gone = await client.delete(
        f"/api/v1/methodist/breaks/{created.json()['id']}",
        headers=_auth(methodist_token),
    )
    assert gone.status_code == 204
    assert await status_of(made["будущее"]) == "scheduled", "снятие возвращает занятие"


async def test_overlapping_breaks_do_not_unpause_each_other(db, client):
    """Снятие одного перерыва не открывает дни, закрытые вторым."""
    env = await _setup(db, "overlap", weekdays=(0,))
    student_id = env["student_id"]
    occ_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                "VALUES (:t, :at, 60) RETURNING id"
            ),
            {
                "t": env["teacher_id"],
                "at": datetime(2026, 9, 14, 10, tzinfo=timezone(timedelta(hours=3))),
            },
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :s, 'scheduled')"
        ),
        {"o": occ_id, "s": student_id},
    )
    await db.commit()

    token = (await _new_user(db, role="methodist", name="me-overlap"))[1]
    first = await client.post(
        "/api/v1/methodist/breaks",
        json={"student_id": student_id, "starts_on": "2026-09-07", "ends_on": "2026-09-20"},
        headers=_auth(token),
    )
    second = await client.post(
        "/api/v1/methodist/breaks",
        json={"student_id": student_id, "starts_on": "2026-09-10", "ends_on": "2026-09-30"},
        headers=_auth(token),
    )
    assert first.status_code == 201 and second.status_code == 201

    await client.delete(
        f"/api/v1/methodist/breaks/{first.json()['id']}", headers=_auth(token)
    )
    still = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :o AND student_id = :s"
            ),
            {"o": occ_id, "s": student_id},
        )
    ).scalar()
    assert still == "on_break", "второй перерыв всё ещё закрывает этот день"


async def test_break_across_month_boundary_recalculates_both(db, client):
    """Перерыв поперёк границы месяцев пересчитывает оба месяца."""
    env = await _setup(db, "cross", weekdays=(0,), price=550000)
    await charge_service.recalculate_month(db, period=date(2026, 9, 1))
    await charge_service.recalculate_month(db, period=date(2026, 10, 1))

    token = (await _new_user(db, role="methodist", name="me-cross"))[1]
    resp = await client.post(
        "/api/v1/methodist/breaks",
        json={"student_id": env["student_id"], "starts_on": "2026-09-21", "ends_on": "2026-10-11"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text

    sep = await _charge(client, env["token"], env["student_id"], date(2026, 9, 1))
    oct_ = await _charge(client, env["token"], env["student_id"], date(2026, 10, 1))
    assert sep["break_lessons"] > 0
    assert oct_["break_lessons"] > 0, "соседний месяц тоже обязан пересчитаться"


async def test_manual_amount_rejected_on_closed_month(db, client):
    """Закрытый месяц руками не правится — иначе «заморожено» ничего не значит."""
    env = await _setup(db, "frozen", weekdays=(0,))
    await charge_service.recalculate_month(db, period=PERIOD)
    row = await _charge(client, env["token"], env["student_id"])

    await client.post(
        "/api/v1/marketer/charges/close",
        json={"period": PERIOD.isoformat()},
        headers=_auth(env["token"]),
    )
    resp = await client.put(
        f"/api/v1/marketer/charges/{row['id']}/manual",
        json={"amount_minor": 1},
        headers=_auth(env["token"]),
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/api/v1/marketer/charges", "/api/v1/marketer/price-overrides"],
)
@pytest.mark.parametrize("role", ["teacher", "student", "methodist", None])
async def test_charges_gate_rejects_other_roles(db, client, path, role):
    _, token = await _new_user(db, role=role, name=f"gate-{role}-{path[-6:]}")
    resp = await client.get(path, headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["teacher", "student", "marketer", None])
async def test_breaks_gate_rejects_other_roles(db, client, role):
    _, token = await _new_user(db, role=role, name=f"bgate-{role}")
    resp = await client.get("/api/v1/methodist/breaks", headers=_auth(token))
    assert resp.status_code == 403


async def test_price_override_only_for_students(db, client):
    """Ручная цена не становится способом узнать чужие имена перебором."""
    _, token = await _new_user(db, role="marketer", name="m-ovr-gate")
    victim_id, _ = await _new_user(db, role="teacher", name="victim-ovr")
    group_id = await _new_group(db, "ovrgate", [("Общий", 100000, None, None)])

    resp = await client.put(
        "/api/v1/marketer/price-overrides",
        json={"student_id": victim_id, "group_id": group_id, "price_minor": 1000},
        headers=_auth(token),
    )
    assert resp.status_code == 404


async def test_break_only_for_students(db, client):
    _, token = await _new_user(db, role="methodist", name="me-brk-gate")
    victim_id, _ = await _new_user(db, role="teacher", name="victim-brk")
    resp = await client.post(
        "/api/v1/methodist/breaks",
        json={"student_id": victim_id, "starts_on": "2026-09-01", "ends_on": "2026-09-02"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


async def test_break_end_before_start_is_rejected(db, client):
    _, token = await _new_user(db, role="methodist", name="me-badrange")
    student_id, _ = await _new_user(db, role="student", name="s-badrange")
    resp = await client.post(
        "/api/v1/methodist/breaks",
        json={"student_id": student_id, "starts_on": "2026-09-10", "ends_on": "2026-09-01"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_prorate_rounds_in_favour_of_student():
    """Округление вниз: направление должно быть предсказуемым, а не «как выйдет»."""
    counts = charge_service.ChargeCounts(expected=9, on_break=1)
    assert charge_service._prorate(550000, counts) == 550000 * 8 // 9

    # Расписания нет — делить не на что, доля не применяется.
    assert charge_service._prorate(550000, charge_service.ChargeCounts(0, 0)) == 550000
    # Перерыв длиннее месяца не уводит сумму в минус.
    assert charge_service._prorate(550000, charge_service.ChargeCounts(4, 9)) == 0


def test_next_month_crosses_year():
    assert charge_service.next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert charge_service.next_month(date(2026, 1, 1)) == date(2026, 2, 1)
