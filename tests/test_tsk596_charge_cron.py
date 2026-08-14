"""tsk-596: месяц начислений создаётся сам + страж «ходит, но не выставлен».

Проверяем не «функция вызвалась», а денежные инварианты суточного прохода:

- месяц заводится сам тому, у кого строки не было (иначе 1 сентября не
  выставилось бы ничего — ровно это и нашли на проде);
- повторный проход не задваивает строку и не меняет сумму;
- **закрытый месяц проход не переписывает** — расхождение уходит поправкой
  вперёд (durable-инвариант, `project_lms_monthly_charges`);
- ручная сумма месяца переживает проход;
- детектор находит ученика, который ходит и не выставлен, и называет причину;
- детектор НЕ считает расписанием привязку к слоту с `is_active = false`
  (на проде трое из пяти «невыставленных» оказались именно такими);
- сотрудники в находки не попадают (Виктор Комлев числится и teacher, и student);
- уведомление уходит один раз в сутки, а не с каждым проходом.

Тик по устройству идёт по ВСЕЙ базе, поэтому проверки смотрят на конкретного
ученика, а не на общие счётчики: в dev-БД лежат чужие строки.
"""
from __future__ import annotations

import random
from datetime import date

import pytest
from sqlalchemy import text

from app.services import charge_cron_service, charge_service
from tests.test_tsk505_marketer_pricing import (
    _enroll,
    _new_course,
    _new_group,
    _new_user,
    _price_course,
)

pytestmark = pytest.mark.asyncio

#: Сентябрь 2026 — месяц, в котором понедельников ровно 4, доли считаются
#: предсказуемо. Дата «сегодня» внутри него передаётся тику явно.
PERIOD = date(2026, 9, 1)
TODAY = date(2026, 9, 15)


async def _slot(db, *, student_id: int, teacher_id: int, weekday: int, link_active: bool = True) -> int:
    """Посадить ученика в недельный слот. `link_active` — состояние ПРИВЯЗКИ."""
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
            "VALUES (:s, :u, :a)"
        ),
        {"s": slot_id, "u": student_id, "a": link_active},
    )
    await db.commit()
    return int(slot_id)


async def _paying_student(db, tag: str, *, price: int = 550000, weekdays=(0,)) -> dict:
    """Ученик с платным курсом, тарифом «единственный вариант» и расписанием."""
    teacher_id, _ = await _new_user(db, role="teacher", name=f"t-{tag}")
    student_id, _ = await _new_user(db, role="student", name=f"s-{tag}")
    group_id = await _new_group(db, tag, [("Общий", price, None, None)])
    course_id = await _new_course(db, tag)
    await _price_course(db, course_id=course_id, group_id=group_id)
    await _enroll(db, student_id=student_id, course_id=course_id)
    for wd in weekdays:
        await _slot(db, student_id=student_id, teacher_id=teacher_id, weekday=wd)
    return {"student_id": student_id, "teacher_id": teacher_id, "group_id": group_id}


async def _charge_row(db, *, student_id: int, group_id: int, period: date = PERIOD):
    return (
        await db.execute(
            text(
                "SELECT id, calculated_minor, manual_minor, status "
                "FROM student_monthly_charge "
                "WHERE student_id = :s AND group_id = :g AND period = :p"
            ),
            {"s": student_id, "g": group_id, "p": period},
        )
    ).first()


async def _rows_count(db, *, student_id: int, period: date = PERIOD) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM student_monthly_charge "
                    "WHERE student_id = :s AND period = :p"
                ),
                {"s": student_id, "p": period},
            )
        ).scalar()
    )


# ------------------------------------------------------- автогенерация месяца


async def test_tick_creates_month_for_student_without_charge(db, db_session_factory):
    """Главный сценарий: строки месяца не было — проход её завёл.

    Именно этого не хватало на проде: без прохода 1 сентября у 42 учеников не
    появилось бы ничего, потому что месяц заводился только руками.
    """
    env = await _paying_student(db, f"auto{random.randint(10**6, 10**7)}")
    assert await _charge_row(db, **{k: env[k] for k in ("student_id", "group_id")}) is None

    summary = await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)

    assert summary["locked"] is True
    assert summary["period"] == PERIOD.isoformat()
    row = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])
    assert row is not None, "проход обязан завести месяц ученику с расписанием и тарифом"
    assert row.calculated_minor == 550000
    assert row.status == "open"


async def test_tick_is_idempotent(db, db_session_factory):
    """Повторный проход не задваивает строку и не меняет сумму.

    Проход суточный: без этого свойства к концу месяца у ученика лежало бы
    тридцать начислений вместо одного.
    """
    env = await _paying_student(db, f"idem{random.randint(10**6, 10**7)}")

    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)
    first = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])
    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)
    second = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])

    assert await _rows_count(db, student_id=env["student_id"]) == 1
    assert second.id == first.id
    assert second.calculated_minor == first.calculated_minor


async def test_tick_does_not_rewrite_closed_month(db, db_session_factory):
    """Закрытый месяц проход не переписывает — расхождение уходит вперёд.

    Договорённость, уже названную человеку, задним числом не меняют. Это
    durable-инвариант денежного контура, и суточный проход обязан его держать
    так же, как ручной пересчёт.
    """
    env = await _paying_student(db, f"closed{random.randint(10**6, 10**7)}", weekdays=(0,))
    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)
    before = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])

    await db.execute(
        text(
            "UPDATE student_monthly_charge SET status = 'closed', closed_at = now() "
            "WHERE id = :id"
        ),
        {"id": before.id},
    )
    # Перерыв на весь месяц: расчёт после него даёт 0 вместо 550000.
    await db.execute(
        text(
            "INSERT INTO student_break (student_id, starts_on, ends_on, note) "
            "VALUES (:s, :a, :b, 'tsk-596 тест')"
        ),
        {"s": env["student_id"], "a": PERIOD, "b": date(2026, 9, 30)},
    )
    await db.commit()

    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)

    after = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])
    assert after.calculated_minor == before.calculated_minor, "закрытый месяц переписан"
    assert after.status == "closed"

    carried = (
        await db.execute(
            text(
                "SELECT amount_minor, source FROM charge_adjustment "
                "WHERE student_id = :s AND period = :p AND origin_period = :o"
            ),
            {"s": env["student_id"], "p": date(2026, 10, 1), "o": PERIOD},
        )
    ).first()
    assert carried is not None, "расхождение закрытого месяца должно уйти поправкой вперёд"
    assert carried.source == "carry_forward"
    assert carried.amount_minor == -550000


async def test_tick_keeps_manual_amount(db, db_session_factory):
    """Сумма, поставленная человеком, проход переживает.

    Пересчёт трогает только расчётную часть: иначе ежедневный проход стирал бы
    ручную договорённость каждую ночь.
    """
    env = await _paying_student(db, f"manual{random.randint(10**6, 10**7)}")
    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)
    row = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])
    await db.execute(
        text("UPDATE student_monthly_charge SET manual_minor = 123400 WHERE id = :id"),
        {"id": row.id},
    )
    await db.commit()

    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)

    after = await _charge_row(db, student_id=env["student_id"], group_id=env["group_id"])
    assert after.manual_minor == 123400


# ------------------------------------------------------------------- детектор


async def test_detector_finds_student_who_attends_without_charge(db):
    """Ученик с расписанием и без тарифной группы — находка с внятной причиной.

    Прод-случай Терехова (4510): ходит с 27.07, не привязан ни к одному курсу,
    поэтому для денег он невидим. Детектор для того и нужен, что пустая строка
    сама себя ничем не проявляет.
    """
    teacher_id, _ = await _new_user(db, role="teacher", name="t-detect")
    student_id, _ = await _new_user(db, role="student", name="s-detect")
    await _slot(db, student_id=student_id, teacher_id=teacher_id, weekday=1)

    found = await charge_cron_service.find_unbilled_active_students(db, period=PERIOD)

    mine = next((f for f in found if f["student_id"] == student_id), None)
    assert mine is not None, "ученик с активным слотом и без начисления обязан находиться"
    assert mine["active_slots"] == 1
    assert "нет ни подписки, ни платного курса" in mine["reason"]


async def test_detector_ignores_inactive_slot_link(db):
    """Отвязанный от слота ученик — не находка, хотя сам слот жив.

    Ровно эта разница увела разбор на проде: у троих из пяти «невыставленных»
    активен был слот, но не привязка к нему, — они из расписания уже выбыли.
    """
    teacher_id, _ = await _new_user(db, role="teacher", name="t-inactive")
    student_id, _ = await _new_user(db, role="student", name="s-inactive")
    await _slot(
        db, student_id=student_id, teacher_id=teacher_id, weekday=1, link_active=False
    )

    found = await charge_cron_service.find_unbilled_active_students(db, period=PERIOD)

    assert all(f["student_id"] != student_id for f in found)


async def test_detector_skips_staff(db):
    """Преподаватель в находки не попадает, даже если числится и учеником.

    Виктор Комлев (id 2) на проде носит сразу пять ролей, включая `student`, и
    сидит в слотах как ведущий занятия. Без отсева он попадал бы в уведомление
    каждый день, и на третий день его перестали бы читать.
    """
    teacher_id, _ = await _new_user(db, role="teacher", name="t-staff")
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'student' ON CONFLICT DO NOTHING"
        ),
        {"u": teacher_id},
    )
    await db.commit()
    await _slot(db, student_id=teacher_id, teacher_id=teacher_id, weekday=3)

    found = await charge_cron_service.find_unbilled_active_students(db, period=PERIOD)

    assert all(f["student_id"] != teacher_id for f in found)


async def test_detector_silent_for_billing_exempt_plan(db):
    """Тариф «денег не берут осознанно» в находки не попадает (tsk-610).

    Пряхин (4498) на тарифе `test` попадал в предупреждение каждый день —
    законно, оператор решил денег не брать. В списке из двух строк одна была
    всегда ложной, и три дня подряд уведомление висело непрочитанным вместе с
    настоящим случаем. Предупреждение, которое не умеет молчать, не читают.
    """
    teacher_id, _ = await _new_user(db, role="teacher", name="t-exempt")
    student_id, _ = await _new_user(db, role="student", name="s-exempt")
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, pricing_group_id, CURRENT_DATE "
            "  FROM subscription_plan WHERE code = 'test'"
        ),
        {"s": student_id},
    )
    await db.commit()
    await _slot(db, student_id=student_id, teacher_id=teacher_id, weekday=1)

    found = await charge_cron_service.find_unbilled_active_students(db, period=PERIOD)

    assert all(f["student_id"] != student_id for f in found)


async def test_detector_still_finds_demo_student(db):
    """А `demo` с занятиями — по-прежнему находка, и это главный случай tsk-610.

    Соблазн «исключить все тарифы без группы» убил бы весь смысл стража:
    ученик на демо, который ходит на занятия, и есть та дыра, через которую
    человек проходит молча (прод, Грабовский 4560 — почти две недели).
    """
    teacher_id, _ = await _new_user(db, role="teacher", name="t-demo")
    student_id, _ = await _new_user(db, role="student", name="s-demo")
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, pricing_group_id, CURRENT_DATE "
            "  FROM subscription_plan WHERE code = 'demo'"
        ),
        {"s": student_id},
    )
    await db.commit()
    await _slot(db, student_id=student_id, teacher_id=teacher_id, weekday=1)

    found = await charge_cron_service.find_unbilled_active_students(db, period=PERIOD)

    mine = next((f for f in found if f["student_id"] == student_id), None)
    assert mine is not None, "ученик на demo с занятиями обязан находиться"
    assert "demo" in mine["reason"]


async def test_detector_silent_for_billed_student(db, db_session_factory):
    """Выставленный ученик находкой не считается — иначе шум перекроет сигнал."""
    env = await _paying_student(db, f"billed{random.randint(10**6, 10**7)}")
    await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)

    found = await charge_cron_service.find_unbilled_active_students(db, period=PERIOD)

    assert all(f["student_id"] != env["student_id"] for f in found)


async def test_tick_notifies_methodist_once_per_day(db, db_session_factory):
    """Находки уходят методисту, но повторный проход в пределах суток молчит.

    Проход ежедневный: без отсрочки один невыставленный ученик превратился бы
    в поток одинаковых уведомлений.
    """
    methodist_id, _ = await _new_user(db, role="methodist", name="m-notify")
    teacher_id, _ = await _new_user(db, role="teacher", name="t-notify")
    student_id, _ = await _new_user(db, role="student", name="s-notify")
    await _slot(db, student_id=student_id, teacher_id=teacher_id, weekday=2)

    first = await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)
    assert first["unbilled"] >= 1
    assert first["notified"] >= 1

    inbox = (
        await db.execute(
            text(
                "SELECT content FROM notifications "
                "WHERE user_id = :u AND kind = :k ORDER BY id DESC"
            ),
            {"u": methodist_id, "k": charge_cron_service.NOTIFICATION_KIND},
        )
    ).all()
    assert len(inbox) == 1, "методист должен получить ровно одно уведомление"

    second = await charge_cron_service.charge_cron_tick(db_session_factory, today=TODAY)
    assert second["notified"] == 0, "второй проход за сутки обязан молчать"
    still = int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM notifications WHERE user_id = :u AND kind = :k"
                ),
                {"u": methodist_id, "k": charge_cron_service.NOTIFICATION_KIND},
            )
        ).scalar()
    )
    assert still == 1
