"""tsk-756 — прошлое не переписывается новой сеткой, за небывшее не берут денег.

Все три дефекта пришли из одного места: расписание помнит только своё
сегодняшнее состояние, а счёт за месяц брал его как состояние того месяца.

* **Дефект 1.** Слот заводят 31 августа под осеннюю сетку — и он считается
  действовавшим весь август. Четверым новичкам, у которых занятий в августе не
  было ни одного, выставили по 611 ₽ за «оставшийся по сетке» день.
* **Дефект 2.** Цена августа взялась по числу слотов, которое у человека СЕЙЧАС:
  троим, поменявшим частоту, август вырос с 2 750/5 500 до 5 500/7 750. Им ушло
  письмо о долге, которого не было.
* **Дефект 3** (найден оператором при разборе). Та же смена сетки ПЕРЕСОЗДАЛА
  привязки, и день прихода сбросился: троим, ходившим с июля, счётчик показал
  «весь месяц до прихода», а расчёт дал бы 0, 0 и 423 ₽ вместо 5 500. Этот бил в
  другую сторону — недобор, и заметить его было нечем: суммы держала ручная
  цена, которая долю не применяет вовсе.

Отсюда же граница, которую легко потерять: **«не был на занятии» бывает двух
видов**. Занятие ДО прихода человека не оплачивается, а занятие, на которое он
не пришёл, — оплачивается. Правки, делающей долю одинаковой для обоих, здесь
быть не должно, и на это есть отдельная проверка.

Проверяем денежные инварианты, а не коды ответов.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import charge_service, pricing_service
from tests.test_tsk505_marketer_pricing import (
    _auth,
    _enroll,
    _new_course,
    _new_group,
    _new_user,
    _price_course,
)

pytestmark = pytest.mark.asyncio

#: «Сегодня» тестов. Фиксированное, иначе смысл «прошедшего месяца» уезжал бы
#: вместе с календарём: в первых числах и в конце месяца проверка означала бы
#: разное.
TODAY = date(2026, 9, 15)
PAST = date(2026, 8, 1)
CURRENT = date(2026, 9, 1)


async def _slot(
    db,
    *,
    student_id: int,
    teacher_id: int,
    weekday: int,
    active_from: date | None = None,
    active_until: date | None = None,
) -> int:
    """Слот с явными границами действия и посаженным в него учеником."""
    slot_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO lesson_slot "
                    "(teacher_id, weekday, start_time, duration_minutes, timezone, "
                    " is_active, active_from, active_until) "
                    "VALUES (:t, :w, '10:00', 60, 'Europe/Moscow', true, :af, :au) "
                    "RETURNING id"
                ),
                {"t": teacher_id, "w": weekday, "af": active_from, "au": active_until},
            )
        ).scalar()
    )
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active, created_at) "
            "VALUES (:s, :u, true, COALESCE(CAST(:af AS timestamptz), now()))"
        ),
        {"s": slot_id, "u": student_id, "af": active_from},
    )
    await db.commit()
    return slot_id


async def _occurrence(
    db,
    *,
    slot_id: int,
    teacher_id: int,
    student_id: int,
    day: date,
    status: str = "confirmed",
) -> None:
    """Фактическое занятие ученика в этот день.

    `status` — вид участия. `no_show` (прогул) от `confirmed` для денег НЕ
    отличается: человек не пришёл по своему выбору и платит (tsk-756).
    """
    occ_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence "
                    "       (slot_id, teacher_id, scheduled_at, duration_minutes) "
                    "VALUES (:s, :t, CAST(:d AS date) + TIME '10:00', 60) RETURNING id"
                ),
                {"s": slot_id, "t": teacher_id, "d": day},
            )
        ).scalar()
    )
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :u, :st)"
        ),
        {"o": occ_id, "u": student_id, "st": status},
    )
    await db.commit()


async def _setup(db, tag: str, *, tariffs=None, price: int = 550000):
    """Ученик на платном курсе. Тарифы — список (имя, цена, ось, значение)."""
    marketer_id, token = await _new_user(db, role="marketer", name=f"m-{tag}")
    teacher_id, _ = await _new_user(db, role="teacher", name=f"t-{tag}")
    student_id, _ = await _new_user(db, role="student", name=f"s-{tag}")
    group_id = await _new_group(db, tag, tariffs or [("Общий", price, None, None)])
    course_id = await _new_course(db, tag)
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    return {
        "token": token,
        "student_id": student_id,
        "teacher_id": teacher_id,
        "group_id": group_id,
    }


async def _charge_row(db, student_id: int, period: date) -> dict | None:
    row = (
        await db.execute(
            text(
                "SELECT calculated_minor, manual_minor, expected_lessons, "
                "       not_started_lessons, missing_lessons, frozen_total_minor "
                "  FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": period},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


# ── дефект 1: за занятие, которого не было, денег не берут ──────────────────


async def test_slot_created_today_does_not_reach_last_month(db):
    """Слот, заведённый под новую сетку, не действует задним числом.

    Ровно случай четверых новичков: привязка 31 августа, слот бессрочный — и
    август насчитал им занятие, которого не могло быть.
    """
    env = await _setup(db, "af")
    # Слот заведён 31 августа: в августе он действует ровно один день.
    await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,  # понедельник; 31.08.2026 — понедельник
        active_from=date(2026, 8, 31),
    )
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.expected == 1, "август должен увидеть только 31-е, а не все понедельники"


async def test_scheduled_day_without_a_lesson_is_not_billed(db):
    """Прошедший день по сетке, в который занятия не было, не оплачивается.

    Именно из этого дня и выросли 611 ₽: сетка говорила «занятие есть», а в
    базе за август не было ни одного `lesson_occurrence`.
    """
    env = await _setup(db, "miss")
    await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 8, 31),
    )
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.expected == 1
    assert counts.missing == 1, "занятия в этот день не было — платить не за что"
    assert counts.billable == 0


async def test_lesson_that_happened_is_billed(db):
    """Обратная сторона: занятие состоялось — вычета нет.

    Без этой проверки предыдущая прошла бы и на правиле «прошлое не считаем
    вовсе», которое обнулило бы счета всем.
    """
    env = await _setup(db, "held")
    slot_id = await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 8, 31),
    )
    await _occurrence(
        db,
        slot_id=slot_id,
        teacher_id=env["teacher_id"],
        student_id=env["student_id"],
        day=date(2026, 8, 31),
    )
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.missing == 0
    assert counts.billable == 1


async def test_future_day_without_a_lesson_is_still_billed(db):
    """Будущий день по сетке вычетом не считается.

    Генератор пишет календарь на две недели вперёд: «занятия ещё нет» там
    означает «его не создали», а не «его не будет». Считай мы иначе — сумма
    зависела бы от того, когда последний раз крутили генератор.
    """
    env = await _setup(db, "fut")
    next_month = charge_service.next_month(charge_service.month_start(date.today()))
    await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=next_month,
    )
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=next_month
    )
    assert counts.expected >= 4
    assert counts.missing == 0, "будущее с фактом не сверяется"


# ── дефект 2: прошлое не переписывается ────────────────────────────────────


async def test_automatic_recalc_leaves_past_month_alone(db):
    """Смена расписания не переписывает уже прошедший месяц.

    Спусковой крючок инцидента: осеннюю сетку применили 31 августа, и пересчёт
    прошёл по всем ОТКРЫТЫМ месяцам — включая август, который был ещё открыт.
    """
    env = await _setup(db, "past")
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "       (student_id, group_id, period, calculated_minor, expected_lessons) "
            "VALUES (:s, :g, :p, 275000, 4)"
        ),
        {"s": env["student_id"], "g": env["group_id"], "p": PAST},
    )
    await db.commit()

    # Сегодняшняя сетка — вдвое гуще той, что была в августе.
    for weekday in (0, 2):
        await _slot(
            db,
            student_id=env["student_id"],
            teacher_id=env["teacher_id"],
            weekday=weekday,
            active_from=CURRENT,
        )
    await charge_service.recalculate_open_months_for_student(
        db, student_id=env["student_id"]
    )

    row = await _charge_row(db, env["student_id"], PAST)
    assert row is not None
    assert row["calculated_minor"] == 275000, "август обязан остаться прежним"


async def test_explicit_recalculation_of_a_past_month_is_allowed(db):
    """Человек может пересчитать прошлое сам — запрет только для автоматики.

    Иначе ошибку в прошлом месяце нечем было бы исправить, кроме правки суммы
    руками у каждого ученика.
    """
    env = await _setup(db, "explicit")
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "       (student_id, group_id, period, calculated_minor, expected_lessons) "
            "VALUES (:s, :g, :p, 999999, 4)"
        ),
        {"s": env["student_id"], "g": env["group_id"], "p": PAST},
    )
    await db.commit()

    await charge_service.recalculate_month(db, period=PAST, allow_past=True)
    row = await _charge_row(db, env["student_id"], PAST)
    assert row is not None
    assert row["calculated_minor"] != 999999, "явная команда пересчёт делает"


async def test_price_uses_schedule_of_the_month_being_billed(db):
    """Цена месяца берётся по частоте ТОГО месяца, а не сегодняшней.

    Точное совпадение с прода: у троих август посчитался по числу слотов,
    которое появилось у них только в сентябрьской сетке.
    """
    env = await _setup(
        db,
        "freq",
        tariffs=[
            ("1 раз в неделю", 275000, "attendance_frequency", "1"),
            ("2 раза в неделю", 550000, "attendance_frequency", "2"),
        ],
    )
    # В августе — один слот, он же и закончился вместе с месяцем.
    await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 7, 1),
        active_until=date(2026, 8, 31),
    )
    # С сентября — два: человек стал ходить чаще.
    for weekday in (0, 2):
        await _slot(
            db,
            student_id=env["student_id"],
            teacher_id=env["teacher_id"],
            weekday=weekday,
            active_from=CURRENT,
        )

    august = await pricing_service.list_student_pricing(db, period=PAST)
    september = await pricing_service.list_student_pricing(db, period=CURRENT)

    def price_of(rows) -> int | None:
        row = next(r for r in rows if r.student_id == env["student_id"])
        return row.groups[0].price_minor

    assert price_of(august) == 275000, "август — по августовской частоте"
    assert price_of(september) == 550000, "сентябрь — по сентябрьской"


# ── сторож ─────────────────────────────────────────────────────────────────


async def test_watchdog_notices_a_shifted_past_month(db):
    """Сумма прошедшего месяца уехала — страж это видит.

    Сегодня сдвиг заметил человек, а не система: письма уже ушли.
    """
    env = await _setup(db, "watch")
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "       (student_id, group_id, period, calculated_minor, expected_lessons) "
            "VALUES (:s, :g, :p, 275000, 4)"
        ),
        {"s": env["student_id"], "g": env["group_id"], "p": PAST},
    )
    await db.commit()

    frozen = await charge_service.freeze_finished_months(db, today=TODAY)
    assert frozen >= 1
    assert not await charge_service.find_shifted_past_months(db, today=TODAY)

    # Сумма поехала мимо явных путей — ровно то, что сделала осенняя сетка.
    await db.execute(
        text(
            "UPDATE student_monthly_charge SET calculated_minor = 550000 "
            " WHERE student_id = :s AND period = :p"
        ),
        {"s": env["student_id"], "p": PAST},
    )
    await db.commit()

    shifted = await charge_service.find_shifted_past_months(db, today=TODAY)
    mine = [s for s in shifted if s["student_id"] == env["student_id"]]
    assert len(mine) == 1
    assert mine[0]["was_minor"] == 275000
    assert mine[0]["now_minor"] == 550000
    assert mine[0]["delta_minor"] == 275000


async def test_manual_correction_of_a_past_month_is_not_an_alarm(db):
    """Оператор поправил сумму прошлого месяца руками — это не сдвиг.

    Иначе страж ругался бы ровно на те правки, которыми чинят его же находки, и
    его перестали бы читать.
    """
    env = await _setup(db, "manual")
    charge_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO student_monthly_charge "
                    "       (student_id, group_id, period, calculated_minor, expected_lessons) "
                    "VALUES (:s, :g, :p, 550000, 8) RETURNING id"
                ),
                {"s": env["student_id"], "g": env["group_id"], "p": PAST},
            )
        ).scalar()
    )
    await db.commit()
    await charge_service.freeze_finished_months(db, today=TODAY)

    assert await charge_service.set_manual_amount(
        db, charge_id=charge_id, amount_minor=275000
    )
    shifted = await charge_service.find_shifted_past_months(db, today=TODAY)
    assert not [s for s in shifted if s["student_id"] == env["student_id"]]


# ── предпросмотр напоминания ───────────────────────────────────────────────


async def test_reminder_preview_explains_the_amount(db):
    """Перед отправкой видно, из чего сумма и были ли занятия вообще.

    «611 ₽ у человека без занятий» должно быть видно ДО письма, а не после.
    """
    from app.services import payment_reminder_service

    debtor = payment_reminder_service.OverdueDebtor(
        student_id=1,
        full_name="Тест",
        email="t@example.com",
        group_id=1,
        group_name="Базовый",
        period=PAST,
        due_minor=61111,
        reminded_recently=False,
        total_minor=61111,
        paid_minor=0,
        expected_lessons=9,
        not_started_lessons=8,
        missing_lessons=1,
        fact_lessons=0,
    )
    basis = debtor.basis
    assert "9 занятий по сетке" in basis
    assert "8 до прихода" in basis
    assert "1 не состоялось" in basis
    assert "фактически занятий 0" in basis


# ── «не был на занятии» бывает двух видов ──────────────────────────────────


async def test_a_missed_lesson_is_still_paid_for(db):
    """Прогул оплачивается: человек не пришёл сам, занятие для него было.

    Решение оператора 01.09 по Рахимжанову (4519): пропускал без уважительной
    причины и не предупреждал — начисление остаётся. Разводит два случая
    наличие строки участия: у прогула она есть (`no_show`), у занятия, которого
    для человека не было, — нет.
    """
    env = await _setup(db, "noshow")
    slot_id = await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 8, 31),
    )
    await _occurrence(
        db,
        slot_id=slot_id,
        teacher_id=env["teacher_id"],
        student_id=env["student_id"],
        day=date(2026, 8, 31),
        status="no_show",
    )
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.missing == 0, "прогул — не «занятия не было»"
    assert counts.billable == 1, "за прогул платят"


async def test_recreated_slot_link_does_not_reset_the_join_date(db):
    """Пересозданная привязка не превращает старого ученика в новичка.

    31.08 смена сетки пересоздала привязки, и день прихода сбросился на день
    смены: троим, ходившим с июля, счётчик показал «весь месяц до прихода», а
    расчёт дал бы 0, 0 и 423 ₽ вместо 5 500. Недобор не проявил себя ничем —
    суммы держала ручная цена, которая долю не применяет.
    """
    env = await _setup(db, "rejoin")
    # Занятие в начале августа — человек уже ходил.
    old_slot = await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 7, 1),
        active_until=date(2026, 8, 31),
    )
    await _occurrence(
        db,
        slot_id=old_slot,
        teacher_id=env["teacher_id"],
        student_id=env["student_id"],
        day=date(2026, 8, 3),
    )
    # А привязку к нынешнему слоту завели только в конце месяца.
    await db.execute(
        text(
            "UPDATE lesson_slot_student SET created_at = '2026-08-31 12:00+03' "
            " WHERE student_id = :u"
        ),
        {"u": env["student_id"]},
    )
    await db.commit()

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.not_started == 0, (
        "человек ходил с начала месяца — «до прихода» вычитать нечего"
    )


async def test_a_lesson_later_than_the_link_does_not_cut_the_month(db):
    """Занятие ПОЗЖЕ привязки месяц не срезает — прежнее правило tsk-630.

    Генератор заполняет календарь вперёд неравномерно: у человека расписание
    есть, а занятий ещё не создали. Без этой проверки защита от пересоздания
    привязки незаметно сломала бы обратный случай.
    """
    env = await _setup(db, "later")
    slot_id = await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 8, 1),
    )
    await db.execute(
        text(
            "UPDATE lesson_slot_student SET created_at = '2026-08-01 12:00+03' "
            " WHERE student_id = :u"
        ),
        {"u": env["student_id"]},
    )
    await db.commit()
    # Первое занятие только в конце месяца — раньше генератор не дошёл.
    await _occurrence(
        db,
        slot_id=slot_id,
        teacher_id=env["teacher_id"],
        student_id=env["student_id"],
        day=date(2026, 8, 31),
    )
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.not_started == 0, "приход — 1 августа, по привязке"


async def test_manual_price_is_not_prorated_by_any_deduction(db):
    """Ручная ЦЕНА не делится ни на один вычет — ни на прогулы, ни на приход.

    Проверено на боевых данных 01.09: у Рахимжанова (4519) ручная цена 5 500 ₽ и
    12 из 13 занятий помечены «до прихода». Пропорционируй мы ручную цену — он
    получил бы ~423 ₽, то есть верное начисление превратилось бы в неверное.
    Договорённость с человеком долей не режется; для доли есть расчётная цена.
    """
    env = await _setup(db, "override")
    await _slot(
        db,
        student_id=env["student_id"],
        teacher_id=env["teacher_id"],
        weekday=0,
        active_from=date(2026, 8, 1),
    )
    await charge_service.set_price_override(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        price_minor=550000,
        note="проверка: доля к ручной цене не применяется",
        created_by=None,
    )
    # Занятий в августе не было ни одного — расчётная цена ушла бы в ноль.
    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PAST
    )
    assert counts.billable == 0, "предпосылка теста: вычеты съедают весь месяц"

    await charge_service.recalculate_month(db, period=PAST, allow_past=True)
    row = await _charge_row(db, env["student_id"], PAST)
    assert row is not None
    assert row["calculated_minor"] == 550000, (
        "ручная цена обязана дойти до суммы целиком"
    )
