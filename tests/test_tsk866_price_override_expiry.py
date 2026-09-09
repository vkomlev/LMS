"""tsk-866 — персональная цена не переживает выпуск, и у неё есть срок.

**Что чинилось.** У двух выпускников 09.09.2026 сентябрь без единого занятия
начислился полностью — 2 750 ₽ и 5 500 ₽. Долю месяца обходили две независимые
ветки, и обе сработали: ручная цена не пропорционируется вовсе, а `_prorate`
при пустой сетке возвращает полную базу. Обе цены заводились как временная
фиксация одного месяца, условие снятия жило в примечании словами, и снять их
было некому.

**Главная пара тестов здесь — `test_leaver_without_lessons_owes_nothing` и
`test_leaver_who_studied_still_owes`.** Порознь ни один ничего не доказывает:
первый показывает, что месяц ушедшего без занятий не начисляется, второй — что
ушедший, который занимался, остаётся должником. Обнуление по признаку «нет
занятий по СЕТКЕ» прошло бы первый тест и провалило второй: у обоих учеников
сетки не было ни в августе, ни в сентябре, а занятия в августе были — шесть
против нуля. Это и есть невидимый недобор, который в tsk-756 чуть не стоил
16 500 ₽.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import charge_service, graduation_service
from tests.test_tsk511_charges_breaks import PERIOD, _setup

pytestmark = pytest.mark.asyncio

#: День внутри месяца расчёта — «ученик ушёл 10-го». Месяц фикстуры будущий,
#: поэтому уход в нём не задевает прошлое и не зависит от сегодняшней даты.
LEFT_ON = PERIOD + timedelta(days=9)


async def _detach_schedule(db, student_id: int) -> None:
    """Убрать ученика из сетки: занятия идут вне расписания.

    Ровно то состояние, в котором были оба пострадавших: слотов нет, счётчик
    занятий по сетке — ноль, и доля месяца делить не на что.
    """
    await db.execute(
        text("UPDATE lesson_slot_student SET is_active = false WHERE student_id = :s"),
        {"s": student_id},
    )
    await db.commit()


async def _lesson_on(db, *, student_id: int, teacher_id: int, day: date) -> None:
    """Фактически проведённое занятие в этот день — без слота и без расписания."""
    when = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
        hours=13
    )
    occurrence_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                "VALUES (:t, :w, 60) RETURNING id"
            ),
            {"t": teacher_id, "w": when},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :s, 'confirmed')"
        ),
        {"o": occurrence_id, "s": student_id},
    )
    await db.commit()


async def _override(db, env: dict, *, price_minor: int, ends_on: date | None = None) -> None:
    await charge_service.set_price_override(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        price_minor=price_minor,
        note="tsk-866: закрепление суммы перед уходом",
        created_by=None,
        ends_on=ends_on,
    )


async def _recalc(db, env: dict, *, left_on: date | None) -> int | None:
    return await charge_service.recalculate_student_group(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        left_on=left_on,
    )


# ─────────────────── главная пара: ушедший без занятий и с ними ──────────────


async def test_leaver_without_lessons_owes_nothing(db) -> None:
    """Месяц ушедшего, в котором не было ни одного занятия, не начисляется.

    Персональная цена здесь стоит намеренно: без неё дефект не воспроизводится
    целиком. Именно она обходит долю («договорённость не должна тихо уезжать»),
    и на проде это дало 2 750 ₽ за сентябрь, в котором человек не появлялся.
    """
    env = await _setup(db, "t866-quiet", weekdays=(0,))
    await _override(db, env, price_minor=275000)
    await _detach_schedule(db, env["student_id"])

    assert await _recalc(db, env, left_on=LEFT_ON) == 0


async def test_leaver_who_studied_still_owes(db) -> None:
    """Ушедший, который занимался, остаётся должником — на полную цену.

    Зеркало предыдущего теста и главная защита правки: сетки у него нет так же,
    как у молчаливого выпускника, — отличается только факт занятий. Обнуление по
    сетке прошло бы мимо шести проведённых занятий и списало бы долг молча.
    """
    env = await _setup(db, "t866-studied", weekdays=(0,))
    await _override(db, env, price_minor=550000)
    await _detach_schedule(db, env["student_id"])
    for offset in (0, 2, 7, 14, 21, 28):
        await _lesson_on(
            db,
            student_id=env["student_id"],
            teacher_id=env["teacher_id"],
            day=PERIOD + timedelta(days=offset),
        )

    assert await _recalc(db, env, left_on=LEFT_ON) == 550000


async def test_active_student_without_lessons_keeps_paying(db) -> None:
    """Действующий ученик с фиксированной ценой платит и в пустой месяц.

    Отклонённый вариант развилки Б («не начислять любой месяц без занятий»)
    сломал бы ровно это: человек, платящий фиксированно и пропустивший месяц,
    перестал бы платить молча. Правило действует ТОЛЬКО для ушедших.
    """
    env = await _setup(db, "t866-active", weekdays=(0,))
    await _override(db, env, price_minor=550000)
    await _detach_schedule(db, env["student_id"])

    assert await _recalc(db, env, left_on=None) == 550000


async def test_leaver_with_manual_price_pays_a_share_of_it(db) -> None:
    """У ушедшего ручная цена тоже делится по занятиям, а не берётся целиком.

    Пока человек учится, его цена доли не знает — это договорённость. С уходом
    договорённости больше нет: за месяц, из которого он ушёл в начале, платить
    как за полный не за что.
    """
    env = await _setup(db, "t866-share", weekdays=(0,))
    await _override(db, env, price_minor=400000)
    mondays = [
        PERIOD + timedelta(days=i)
        for i in range((charge_service.next_month(PERIOD) - PERIOD).days)
        if (PERIOD + timedelta(days=i)).weekday() == 0
    ]
    billable = len([d for d in mondays if d <= LEFT_ON])

    total = await _recalc(db, env, left_on=LEFT_ON)
    assert total == 400000 * billable // len(mondays)
    assert 0 < total < 400000, "доля ручной цены, а не полная и не ноль"


async def test_leaver_with_schedule_pays_for_lessons_before_leaving(db) -> None:
    """Ушедший среди месяца платит долю за занятия до ухода — как и раньше.

    Проверка, что новое правило не перебило вычет tsk-804: занятия были, значит
    месяц считается долей, а не обнуляется целиком.
    """
    env = await _setup(db, "t866-partial", weekdays=(0,))
    mondays = [
        PERIOD + timedelta(days=i)
        for i in range((charge_service.next_month(PERIOD) - PERIOD).days)
        if (PERIOD + timedelta(days=i)).weekday() == 0
    ]
    for day in mondays:
        if day <= LEFT_ON:
            await _lesson_on(
                db,
                student_id=env["student_id"],
                teacher_id=env["teacher_id"],
                day=day,
            )

    total = await _recalc(db, env, left_on=LEFT_ON)
    billable = len([d for d in mondays if d <= LEFT_ON])
    assert total == 550000 * billable // len(mondays)
    assert 0 < total < 550000, "доля, а не полный месяц и не ноль"


# ──────────────────────────── срок действия цены ─────────────────────────────


async def test_expired_override_is_not_applied(db) -> None:
    """Цена со сроком в прошлом месяце к этому месяцу не применяется."""
    env = await _setup(db, "t866-expired", weekdays=(0,))
    await _override(db, env, price_minor=100000, ends_on=PERIOD - timedelta(days=1))

    # Цена 1 000 ₽ истекла — считается тариф группы (5 500 ₽).
    assert await _recalc(db, env, left_on=None) == 550000


async def test_override_covers_the_month_it_was_closed_in(db) -> None:
    """Месяц, в котором цену закрыли, дорабатывает по ней целиком.

    Граница по первому числу, а не по последнему дню: закрытие цены среди месяца
    не должно переписывать сумму, уже названную человеку.
    """
    env = await _setup(db, "t866-boundary", weekdays=(0,))
    await _override(db, env, price_minor=100000, ends_on=PERIOD + timedelta(days=5))

    assert await _recalc(db, env, left_on=None) == 100000


async def test_open_ended_override_survives(db) -> None:
    """Бессрочная цена работает как прежде — у действующих учеников она законна."""
    env = await _setup(db, "t866-forever", weekdays=(0,))
    await _override(db, env, price_minor=100000, ends_on=None)

    assert await _recalc(db, env, left_on=None) == 100000


# ─────────────────────────── выпуск закрывает цену ───────────────────────────


async def _override_ends_on(db, student_id: int) -> date | None:
    return (
        await db.execute(
            text("SELECT ends_on FROM student_price_override WHERE student_id = :s"),
            {"s": student_id},
        )
    ).scalar_one()


async def test_graduation_closes_the_price_override(db) -> None:
    """Выпуск закрывает персональную цену днём ухода, а не оставляет её жить."""
    env = await _setup(db, "t866-grad", weekdays=(0,))
    await _override(db, env, price_minor=275000)
    today = date.today()

    result = await graduation_service.apply(
        db, env["student_id"], changed_by=None, today=today
    )
    await db.commit()

    assert result.closed_price_overrides == 1
    assert await _override_ends_on(db, env["student_id"]) == today


async def test_graduation_keeps_the_price_record_itself(db) -> None:
    """Запись цены остаётся: это след договорённости, а не производная величина.

    Вернут ученика к занятиям — цену продлят осознанно, а не оживят молча.
    """
    env = await _setup(db, "t866-trace", weekdays=(0,))
    await _override(db, env, price_minor=275000)

    await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    rows = await charge_service.list_overrides(db)
    mine = [r for r in rows if r["student_id"] == env["student_id"]]
    assert len(mine) == 1, "цена не удаляется, а закрывается сроком"
    assert mine[0]["price_minor"] == 275000


async def test_graduation_does_not_extend_an_earlier_end_date(db) -> None:
    """Цену, закрытую раньше руками, уход не продлевает."""
    env = await _setup(db, "t866-earlier", weekdays=(0,))
    closed_earlier = date.today() - timedelta(days=40)
    await _override(db, env, price_minor=275000, ends_on=closed_earlier)

    await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    assert await _override_ends_on(db, env["student_id"]) == closed_earlier
