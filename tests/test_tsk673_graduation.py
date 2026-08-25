"""tsk-673 — перевод на тариф «Выпускник» как одно событие.

До этой задачи перевод менял только строку подписки, и двое выпускников из пяти
на боевых данных 25.08.2026 продолжали числиться в слотах расписания.

Главный тест здесь — не «слоты сняты», а **`test_debt_survives_recalculation`**
в паре с `test_plain_detach_erases_the_debt`. Вторая половина пары показывает
ловушку: снятие с расписания лишает открытый месяц основания для расчёта, и
пересчёт удаляет строку вместе с долгом. Первая — что механизм выпуска этого не
допускает. Порознь ни один из них ничего не доказывает.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import charge_service, graduation_service, subscription_service
from app.utils.exceptions import DomainError
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import PERIOD, _setup

pytestmark = pytest.mark.asyncio


async def _charge_row(db, student_id: int) -> dict | None:
    row = (
        await db.execute(
            text(
                "SELECT status, calculated_minor, manual_minor "
                "  FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": PERIOD},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def _pay(db, *, student_id: int, group_id: int, amount_minor: int, status: str) -> None:
    """Приложить чек (`pending`) или подтверждённый платёж за месяц.

    У подтверждённого обязана быть отметка о разборе — это держит CHECK
    `ck_student_payment_reviewed_has_timestamp`, а не дисциплина кода.
    """
    await db.execute(
        text(
            "INSERT INTO student_payment "
            "  (student_id, group_id, period, amount_minor, method, status, "
            "   paid_on, purpose, reviewed_at) "
            "VALUES (:s, :g, :p, :a, 'manual', :st, CURRENT_DATE, 'monthly', "
            "        CASE WHEN :st = 'confirmed' THEN now() END)"
        ),
        {
            "s": student_id,
            "g": group_id,
            "p": PERIOD,
            "a": amount_minor,
            "st": status,
        },
    )
    await db.commit()


async def _make_marketer(db, tag: str) -> int:
    marketer_id, _ = await _marketer_token(db, tag)
    return marketer_id


async def _marketer_token(db, tag: str) -> tuple[int, str]:
    """Маркетолог с живой сессией — гейт экрана пускает только человека."""
    return await _new_user(db, role="marketer", name=f"mk-{tag}")


async def _occurrence(
    db, *, slot_id: int, teacher_id: int, student_id: int, days: int, status: str
) -> int:
    """Занятие через `days` суток от сейчас с участником в заданном статусе."""
    when = datetime.now(timezone.utc) + timedelta(days=days)
    occurrence_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence "
                "  (slot_id, teacher_id, scheduled_at, duration_minutes) "
                "VALUES (:sl, :t, :w, 60) RETURNING id"
            ),
            {"sl": slot_id, "t": teacher_id, "w": when},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant "
            "  (occurrence_id, student_id, status) VALUES (:o, :s, :st)"
        ),
        {"o": occurrence_id, "s": student_id, "st": status},
    )
    await db.commit()
    return int(occurrence_id)


async def _slot_of(db, student_id: int) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT slot_id FROM lesson_slot_student "
                    " WHERE student_id = :s ORDER BY id LIMIT 1"
                ),
                {"s": student_id},
            )
        ).scalar_one()
    )


async def _active_slots(db, student_id: int) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM lesson_slot_student lss "
                    "  JOIN lesson_slot ls ON ls.id = lss.slot_id "
                    " WHERE lss.student_id = :s AND lss.is_active AND ls.is_active"
                ),
                {"s": student_id},
            )
        ).scalar_one()
    )


# ───────────────────────────── свод оплаты ──────────────────────────────────


async def test_settlement_counts_open_month_not_only_overdue(db) -> None:
    """Незакрытый ТЕКУЩИЙ месяц — уже долг выпускника, хотя он не просрочен.

    Правило школы «должником становишься в следующем месяце» здесь не годится:
    человек уходит, и следующего счёта ему никто не выставит.
    """
    env = await _setup(db, "t673-open", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )

    debt = await graduation_service.settlement(db, env["student_id"])
    assert debt.charged_minor == 550000
    assert debt.paid_minor == 0
    assert debt.due_minor == 550000, "остаток открытого месяца обязан попасть в свод"
    assert debt.has_debt is True


async def test_settlement_pending_receipt_clears_the_debt(db) -> None:
    """Приложенный чек долг гасит: человек своё сделал, дальше очередь наша."""
    env = await _setup(db, "t673-pending", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    await _pay(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        amount_minor=550000,
        status="pending",
    )

    debt = await graduation_service.settlement(db, env["student_id"])
    assert debt.pending_minor == 550000
    assert debt.due_minor == 0, "чек, покрывающий остаток, снимает признак долга"
    assert debt.has_debt is False


async def test_settlement_partial_receipt_does_not_clear_the_debt(db) -> None:
    """Чек на рубль не гасит долг: гасит только тот, что покрывает остаток."""
    env = await _setup(db, "t673-partial", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    await _pay(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        amount_minor=100,
        status="pending",
    )

    debt = await graduation_service.settlement(db, env["student_id"])
    assert debt.due_minor == 550000
    assert debt.has_debt is True


async def test_settlement_ignores_closed_months(db) -> None:
    """Закрытый месяц в свод не идёт: его сумма уже зафиксирована."""
    env = await _setup(db, "t673-closed", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    await db.execute(
        text(
            "UPDATE student_monthly_charge SET status = 'closed', closed_at = now() "
            " WHERE student_id = :s"
        ),
        {"s": env["student_id"]},
    )
    await db.commit()

    debt = await graduation_service.settlement(db, env["student_id"])
    assert debt.lines == []
    assert debt.due_minor == 0


# ─────────────────────────── снятие с расписания ────────────────────────────


async def test_graduation_detaches_slots_and_future_lessons(db) -> None:
    """Слоты гаснут, будущие «назначено» и «на перерыве» снимаются.

    `on_break` обязателен: `break_service` по окончании перерыва переводит такие
    строки обратно в `scheduled`, не глядя на погашенную привязку к слоту, —
    выпускник вернулся бы в расписание сам (прод, ученик 4500).
    """
    env = await _setup(db, "t673-detach", weekdays=(0,))
    slot_id = await _slot_of(db, env["student_id"])
    await _occurrence(
        db, slot_id=slot_id, teacher_id=env["teacher_id"],
        student_id=env["student_id"], days=3, status="scheduled",
    )
    await _occurrence(
        db, slot_id=slot_id, teacher_id=env["teacher_id"],
        student_id=env["student_id"], days=5, status="on_break",
    )

    plan = await graduation_service.schedule_plan(db, env["student_id"])
    assert len(plan.slots) == 1
    assert plan.future_lessons == 2, "предпросмотр обязан считать оба статуса"

    result = await graduation_service.apply(
        db, env["student_id"], changed_by=None
    )
    await db.commit()

    assert result.detached_slots == 1
    assert result.detached_lessons == 2
    assert await _active_slots(db, env["student_id"]) == 0
    left = (
        await db.execute(
            text(
                "SELECT count(*) FROM lesson_occurrence_participant "
                " WHERE student_id = :s"
            ),
            {"s": env["student_id"]},
        )
    ).scalar_one()
    assert left == 0


async def test_graduation_keeps_what_student_decided_and_the_past(db) -> None:
    """Отметки самого ученика и прошедшие занятия — история, её не трогаем."""
    env = await _setup(db, "t673-keep", weekdays=(0,))
    slot_id = await _slot_of(db, env["student_id"])
    await _occurrence(
        db, slot_id=slot_id, teacher_id=env["teacher_id"],
        student_id=env["student_id"], days=4, status="confirmed",
    )
    await _occurrence(
        db, slot_id=slot_id, teacher_id=env["teacher_id"],
        student_id=env["student_id"], days=-7, status="scheduled",
    )

    plan = await graduation_service.schedule_plan(db, env["student_id"])
    assert plan.future_lessons == 0
    assert plan.kept_lessons == 1, "подтверждённое ученику оставляем"

    result = await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    assert result.detached_lessons == 0
    left = (
        await db.execute(
            text(
                "SELECT count(*) FROM lesson_occurrence_participant "
                " WHERE student_id = :s"
            ),
            {"s": env["student_id"]},
        )
    ).scalar_one()
    assert left == 2, "и подтверждённое будущее, и прошедшее занятие на месте"


# ──────────────────── долг переживает пересчёт (ядро задачи) ────────────────


async def test_plain_detach_erases_the_debt(db) -> None:
    """Ловушка, ради которой всё и написано: обычное снятие стирает долг.

    Ученика снимают с расписания штатным путём — и открытый месяц исчезает
    вместе с суммой, которую человек остался должен. Именно так на проде
    пропали начисления трёх выпускников (4499, 4521, 4523).
    """
    from app.services import lesson_calendar_service

    env = await _setup(db, "t673-trap", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    assert (await _charge_row(db, env["student_id"]))["calculated_minor"] == 550000

    await subscription_service.change_plan(
        db, env["student_id"], "alumni", reason="tsk-673 тест ловушки"
    )
    await db.commit()
    await lesson_calendar_service.remove_slot_participant(
        db, await _slot_of(db, env["student_id"]), env["student_id"]
    )

    assert await _charge_row(db, env["student_id"]) is None, (
        "если строка уцелела, ловушки больше нет и парный тест ничего не доказывает"
    )


async def test_debt_survives_recalculation(db) -> None:
    """Выпуск замораживает долг: пересчёт больше не может его стереть."""
    env = await _setup(db, "t673-freeze", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    before = await _charge_row(db, env["student_id"])
    assert before["calculated_minor"] == 550000

    await subscription_service.change_plan(
        db, env["student_id"], "alumni", reason="tsk-673 выпуск"
    )
    result = await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    assert result.frozen_charges == 1
    assert result.settlement.due_minor == 550000

    await charge_service.recalculate_open_months_for_student(
        db, student_id=env["student_id"]
    )
    await charge_service.recalculate_month(db, period=PERIOD)

    after = await _charge_row(db, env["student_id"])
    assert after is not None, "долг ушедшего обязан пережить пересчёт"
    assert after["status"] == "closed"
    assert after["calculated_minor"] == before["calculated_minor"], (
        "заморозка переставляет статус и НЕ трогает сумму"
    )


async def test_graduation_without_debt_leaves_money_alone(db) -> None:
    """Оплачено — месяц не закрываем и никого не тревожим."""
    env = await _setup(db, "t673-paid", weekdays=(0,))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    await _pay(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        amount_minor=550000,
        status="confirmed",
    )

    result = await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    assert result.settlement.due_minor == 0
    assert result.frozen_charges == 0
    assert result.escalated_to == []
    assert (await _charge_row(db, env["student_id"]))["status"] == "open"


# ─────────────────────────────── эскалация ──────────────────────────────────


async def test_debt_escalates_to_marketer(db) -> None:
    """Долг уходит маркетологу записью в кабинет — с суммой и месяцем."""
    env = await _setup(db, "t673-esc", weekdays=(0,))
    marketer_id = await _make_marketer(db, "t673-esc")
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )

    result = await graduation_service.apply(
        db, env["student_id"], changed_by=marketer_id
    )
    await db.commit()

    assert marketer_id in result.escalated_to
    note = (
        await db.execute(
            text(
                "SELECT title, content, payload FROM notifications "
                " WHERE user_id = :u AND kind = :k ORDER BY id DESC LIMIT 1"
            ),
            {"u": marketer_id, "k": graduation_service.ESCALATION_KIND},
        )
    ).mappings().first()
    assert note is not None, "сигнал обязан дойти, а не остаться в логе"
    assert note["payload"]["due_minor"] == 550000
    assert "5500 ₽" in note["content"], "сумма в тексте, а не только в поле payload"


# ──────────────────────── гейт работы в курсе ───────────────────────────────


async def test_alumni_cannot_work_in_course(db) -> None:
    """У выпускника сдача закрыта — с машинным признаком в теле отказа."""
    env = await _setup(db, "t673-gate", weekdays=(0,))
    await subscription_service.change_plan(
        db, env["student_id"], "alumni", reason="tsk-673 гейт"
    )
    await db.commit()

    with pytest.raises(DomainError) as exc:
        await graduation_service.assert_course_work_allowed(db, env["student_id"])
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == graduation_service.COURSE_WORK_CLOSED_CODE


async def test_learning_plans_still_allow_work(db) -> None:
    """Тариф с занятиями сдачу не закрывает — гейт молчит."""
    env = await _setup(db, "t673-base", weekdays=(0,))
    await subscription_service.change_plan(
        db, env["student_id"], "base", reason="tsk-673 контроль"
    )
    await db.commit()

    await graduation_service.assert_course_work_allowed(db, env["student_id"])


async def test_student_without_plan_is_not_blocked(db) -> None:
    """Тарифа нет — это «ещё не размечен», а не «выпускник».

    Обратная трактовка закрыла бы задания всем, кому тариф не успели поставить.
    """
    env = await _setup(db, "t673-noplan", weekdays=(0,))
    await graduation_service.assert_course_work_allowed(db, env["student_id"])


async def test_self_study_plan_is_not_confused_with_alumni(db) -> None:
    """У Self занятий нет, но сдавать он может: признак не выводится из `lessons`."""
    env = await _setup(db, "t673-self", weekdays=(0,))
    await subscription_service.change_plan(
        db, env["student_id"], "self", reason="tsk-673 контроль"
    )
    await db.commit()

    await graduation_service.assert_course_work_allowed(db, env["student_id"])


async def test_endpoint_preview_shows_what_will_happen(db, client) -> None:
    """Предпросмотр отвечает по HTTP и несёт и расписание, и свод.

    Отдельно от сервисных проверок: свод собирается из вложенных объектов, и
    «сервис посчитал» не то же самое, что «клиент это получил» — ровно тот
    зазор, из-за которого 200 в логе уживается со сломанным экраном (tsk-309).
    """
    env = await _setup(db, "t673-http-prev", weekdays=(0,))
    _, token = await _marketer_token(db, "t673-prev")
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    slot_id = await _slot_of(db, env["student_id"])
    await _occurrence(
        db, slot_id=slot_id, teacher_id=env["teacher_id"],
        student_id=env["student_id"], days=3, status="scheduled",
    )

    resp = await client.get(
        f"/api/v1/subscriptions/students/{env['student_id']}/graduation-preview",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["student_id"] == env["student_id"]
    assert len(body["schedule"]["slots"]) == 1
    assert body["schedule"]["slots"][0]["label"], "подпись слота обязана дойти до экрана"
    assert body["schedule"]["future_lessons"] == 1
    assert body["settlement"]["due_minor"] == 550000
    assert len(body["settlement"]["lines"]) == 1


async def test_endpoint_graduation_runs_side_effects(db, client) -> None:
    """Перевод на «Выпускника» через API снимает с расписания и возвращает свод."""
    env = await _setup(db, "t673-http-run", weekdays=(0,))
    _, token = await _marketer_token(db, "t673-run")
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    slot_id = await _slot_of(db, env["student_id"])
    await _occurrence(
        db, slot_id=slot_id, teacher_id=env["teacher_id"],
        student_id=env["student_id"], days=3, status="scheduled",
    )

    resp = await client.post(
        f"/api/v1/subscriptions/students/{env['student_id']}",
        headers=_auth(token),
        json={"plan_code": "alumni", "reason": "закончил обучение"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current"]["plan_code"] == "alumni"

    grad = body["graduation"]
    assert grad is not None, "побочные действия обязаны быть видны в ответе"
    assert grad["detached_slots"] == 1
    assert grad["detached_lessons"] == 1
    assert grad["frozen_charges"] == 1
    assert grad["settlement"]["due_minor"] == 550000

    assert await _active_slots(db, env["student_id"]) == 0
    assert (await _charge_row(db, env["student_id"]))["status"] == "closed"


async def test_endpoint_other_plan_has_no_graduation_block(db, client) -> None:
    """Обычный перевод расписание не трогает и поля `graduation` не несёт."""
    env = await _setup(db, "t673-http-base", weekdays=(0,))
    _, token = await _marketer_token(db, "t673-base")

    resp = await client.post(
        f"/api/v1/subscriptions/students/{env['student_id']}",
        headers=_auth(token),
        json={"plan_code": "base", "reason": "перевод на базовый"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["graduation"] is None
    assert await _active_slots(db, env["student_id"]) == 1, "расписание не тронуто"


async def test_gate_reads_the_flag_not_the_plan_code(db) -> None:
    """Признак живёт в данных: снятый флаг у другого тарифа тоже закрывает сдачу.

    Так следующий тариф-архив не потребует релиза (урок tsk-610).
    """
    env = await _setup(db, "t673-flag", weekdays=(0,))
    await subscription_service.change_plan(
        db, env["student_id"], "self", reason="tsk-673 признак"
    )
    await db.execute(
        text("UPDATE subscription_plan SET course_work = false WHERE code = 'self'")
    )
    await db.commit()

    with pytest.raises(DomainError):
        await graduation_service.assert_course_work_allowed(db, env["student_id"])
