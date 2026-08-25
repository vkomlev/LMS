"""tsk-674 фаза 2: помощник вёрстки осеннего расписания.

Покрывает то, из-за чего вёрстка может выйти вредной, а не полезной:

- раскладка: потолок 10 соблюдается, до цели 5-6 люди кучкуются, а не
  размазываются по всем 33 часам;
- «зажатые» ученики (у кого выбор меньше, чем нужно занятий) встают первыми;
- список тех, кому не досталось ни одного желательного часа, — он и есть
  главный результат для разговора;
- разрывы внутри дня считаются, а короткий день разрывом не считается;
- подбор часов не берёт часы без спроса и не выходит за сетку;
- применение: переиспользует существующий слот, снимает лишних, показывает
  смену числа занятий (это деньги) и по умолчанию ничего не гасит;
- `dry_run` действительно ничего не меняет;
- гейт: ученику вёрстка недоступна.
"""
from __future__ import annotations

import random
from datetime import time

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.schemas.schedule_plan import (
    HARD_MAX,
    SchedulePlanApplyRequest,
    SchedulePlanApplySlot,
)
from app.services import schedule_plan_service
from app.services.auth.session_service import create_session
from app.services.schedule_plan_service import StudentPlanInput


def _student(sid: int, lessons: int, preferred, possible=()) -> StudentPlanInput:
    return StudentPlanInput(
        student_id=sid,
        lessons_per_week=lessons,
        preferred=[(w, time(hour=h)) for w, h in preferred],
        possible=[(w, time(hour=h)) for w, h in possible],
    )


# ============================== Раскладка ==============================


def test_assign_respects_hard_max():
    """Одиннадцатый человек в час не встаёт: потолок 10 — запрет оператора."""
    hour = (0, time(hour=12))
    spare = (1, time(hour=12))
    students = [_student(i, 1, [(0, 12), (1, 12)]) for i in range(1, 15)]
    result = schedule_plan_service.assign(students, [hour, spare])

    assert len(result.by_hour[hour]) <= HARD_MAX
    assert len(result.by_hour[spare]) == 14 - len(result.by_hour[hour])


def test_assign_groups_people_before_spreading():
    """Четверо с одинаковым выбором идут в один час, а не в четыре разных.

    Иначе 33 часа сетки кончатся на слотах по одному человеку, а цель — 5-6.
    """
    hours = [(0, time(hour=12)), (0, time(hour=13)), (0, time(hour=14))]
    students = [_student(i, 1, [(0, 12), (0, 13), (0, 14)]) for i in range(1, 5)]
    result = schedule_plan_service.assign(students, hours)

    filled = [h for h in hours if result.by_hour[h]]
    assert len(filled) == 1, "люди должны собраться в один слот"


def test_assign_places_constrained_student_first():
    """У кого выбор один-единственный — тот встаёт раньше тех, у кого выбор широкий."""
    tight_hour = (0, time(hour=12))
    students = [_student(i, 1, [(0, 12), (0, 13)]) for i in range(1, 11)]
    tight = _student(99, 1, [(0, 12)])
    result = schedule_plan_service.assign(students + [tight], [tight_hour, (0, time(hour=13))])

    assert 99 in [sid for sid, _ in result.by_hour[tight_hour]]


def test_assign_prefers_preferred_over_possible():
    """Возможный час берём только когда желательный не подошёл."""
    students = [_student(1, 1, [(0, 12)], [(0, 13)])]
    result = schedule_plan_service.assign(students, [(0, time(hour=12)), (0, time(hour=13))])

    assert result.by_student[1] == [((0, time(hour=12)), "preferred")]


def test_assign_falls_back_to_possible_when_preferred_is_full():
    """Желательный час забит десятью — одиннадцатый идёт в свой возможный."""
    pref = (0, time(hour=12))
    poss = (0, time(hour=13))
    crowd = [_student(i, 1, [(0, 12)]) for i in range(1, 11)]
    late = _student(50, 1, [(0, 12)], [(0, 13)])
    result = schedule_plan_service.assign(crowd + [late], [pref, poss])

    assert len(result.by_hour[pref]) == HARD_MAX
    assert result.by_student[50] == [(poss, "possible")]


# ============================== Цена решения ==============================


def _rows_from(students: list[StudentPlanInput]):
    from app.schemas.schedule_plan import PlanStudentRow

    return [
        PlanStudentRow(
            student_id=s.student_id,
            full_name=f"Ученик {s.student_id}",
            email=None,
            timezone=None,
            is_filled=True,
            lessons_per_week=s.lessons_per_week,
            preferred=[],
            possible=[],
            comment=None,
            current_hours=[],
            needs_move=False,
        )
        for s in students
    ]


def test_preview_lists_students_without_preferred_hour():
    """Главный список фазы 2: кому не досталось ни одного желательного часа."""
    lucky = _student(1, 1, [(0, 12)])
    unlucky = _student(2, 1, [(1, 18)], [(0, 12)])
    homeless = _student(3, 1, [(3, 15)])
    people = [lucky, unlucky, homeless]

    preview = schedule_plan_service.build_preview(
        _rows_from(people), people, [], [(0, time(hour=12))]
    )

    ids = {u.student_id for u in preview.without_preferred}
    assert ids == {2, 3}
    assert {u.student_id for u in preview.unplaced} == {3}
    assert preview.metrics.without_preferred == 2
    assert preview.metrics.fully_preferred == 1


def test_preview_marks_crowded_and_over_levels():
    """7-10 — «тесно», больше 10 раскладка не допускает вовсе."""
    people = [_student(i, 1, [(0, 12)]) for i in range(1, 9)]
    preview = schedule_plan_service.build_preview(
        _rows_from(people), people, [], [(0, time(hour=12))]
    )

    assert preview.slots[0].count == 8
    assert preview.slots[0].level == "crowded"
    assert preview.metrics.slots_over == 0


def test_gaps_only_inside_the_day():
    """Дырка между занятыми часами — разрыв; короткий день — не разрыв."""
    gaps = schedule_plan_service.find_gaps(
        [(0, time(hour=12)), (0, time(hour=14)), (1, time(hour=12)), (1, time(hour=13))]
    )

    assert len(gaps) == 1
    assert gaps[0].weekday == 0
    assert gaps[0].hours == [time(hour=13)]


def test_suggest_hours_takes_only_hours_people_asked_for():
    """Предложение не занимает часы, которых никто не просил."""
    people = [
        _student(1, 2, [(0, 12), (2, 17)]),
        _student(2, 1, [(0, 12)]),
        _student(3, 1, [(2, 17)]),
    ]
    hours = schedule_plan_service.suggest_hours(people)

    assert set(hours) == {(0, time(hour=12)), (2, time(hour=17))}


def test_suggest_hours_keeps_existing_slot_hour():
    """Час, в котором слот уже стоит, остаётся: переезжать зря не нужно."""
    people = [_student(1, 1, [(0, 12), (0, 13)])]
    hours = schedule_plan_service.suggest_hours(
        people, existing_hours=[(0, time(hour=13))], keep_existing=True
    )

    assert (0, time(hour=13)) in hours


def test_suggest_hours_ignores_hours_outside_grid():
    """11:00 осенью не существует — в предложение он не попадёт."""
    people = [_student(1, 1, [(0, 12)])]
    hours = schedule_plan_service.suggest_hours(
        people, existing_hours=[(0, time(hour=11))], keep_existing=True
    )

    assert all(schedule_plan_service.in_grid(h) for h in hours)


# ============================== Данные и применение ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk674p") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        role_id = (
            await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


@pytest.mark.asyncio
async def test_snapshot_counts_demand_by_hour(db):
    """Спрос по часам считается по живым пожеланиям — это главный экран вёрстки."""
    from app.schemas.schedule_preference import SchedulePreferenceWrite
    from app.services import schedule_preference_service

    student_id = await _create_user(db, role="student")
    await schedule_preference_service.save_preference(
        db,
        student_id,
        SchedulePreferenceWrite(
            lessons_per_week=1,
            hours=[
                {"weekday": 0, "start_time": "12:00", "kind": "preferred"},
                {"weekday": 1, "start_time": "13:00", "kind": "possible"},
            ],
        ),
        changed_by=student_id,
    )

    snapshot = await schedule_plan_service.get_snapshot(db)
    cells = {(c.weekday, c.start_time): c for c in snapshot.demand}

    assert cells[(0, time(hour=12))].preferred_count >= 1
    assert cells[(1, time(hour=13))].possible_count >= 1
    assert snapshot.capacity.hours_total == 33
    assert any(s.student_id == student_id for s in snapshot.students)


@pytest.mark.asyncio
async def test_apply_dry_run_changes_nothing(db):
    """Предпросмотр применения не создаёт слотов: сперва человек видит отчёт."""
    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")

    before = (
        await db.execute(
            text("SELECT count(*) FROM lesson_slot WHERE teacher_id = :t"),
            {"t": teacher_id},
        )
    ).scalar_one()

    result = await schedule_plan_service.apply_plan(
        db,
        SchedulePlanApplyRequest(
            teacher_id=teacher_id,
            slots=[
                SchedulePlanApplySlot(
                    weekday=0, start_time=time(hour=12), student_ids=[student_id]
                )
            ],
            dry_run=True,
        ),
        actor_id=None,
    )
    after = (
        await db.execute(
            text("SELECT count(*) FROM lesson_slot WHERE teacher_id = :t"),
            {"t": teacher_id},
        )
    ).scalar_one()

    assert result.dry_run is True
    assert result.slots_created == 1
    assert after == before
    assert [c.student_id for c in result.lesson_changes] == [student_id]


@pytest.mark.asyncio
async def test_apply_creates_slot_and_attaches_students(db):
    """Применение создаёт слот и ставит в него людей — тем же путём, что рукой."""
    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")

    result = await schedule_plan_service.apply_plan(
        db,
        SchedulePlanApplyRequest(
            teacher_id=teacher_id,
            slots=[
                SchedulePlanApplySlot(
                    weekday=3, start_time=time(hour=16), student_ids=[student_id]
                )
            ],
            dry_run=False,
        ),
        actor_id=None,
    )

    assert result.slots_created == 1
    slot_id = result.outcomes[0].slot_id
    members = (
        await db.execute(
            text(
                "SELECT student_id FROM lesson_slot_student "
                " WHERE slot_id = :s AND is_active"
            ),
            {"s": slot_id},
        )
    ).scalars().all()
    assert members == [student_id]


@pytest.mark.asyncio
async def test_apply_reuses_slot_and_detaches_extra_student(db):
    """Повторное применение переиспользует слот и снимает того, кого в плане нет."""
    teacher_id = await _create_user(db, role="teacher")
    keep_id = await _create_user(db, role="student")
    drop_id = await _create_user(db, role="student")

    first = await schedule_plan_service.apply_plan(
        db,
        SchedulePlanApplyRequest(
            teacher_id=teacher_id,
            slots=[
                SchedulePlanApplySlot(
                    weekday=2, start_time=time(hour=15), student_ids=[keep_id, drop_id]
                )
            ],
            dry_run=False,
        ),
        actor_id=None,
    )
    second = await schedule_plan_service.apply_plan(
        db,
        SchedulePlanApplyRequest(
            teacher_id=teacher_id,
            slots=[
                SchedulePlanApplySlot(
                    weekday=2, start_time=time(hour=15), student_ids=[keep_id]
                )
            ],
            dry_run=False,
        ),
        actor_id=None,
    )

    assert first.slots_created == 1
    assert second.slots_reused == 1
    assert second.students_detached == 1
    slot_id = first.outcomes[0].slot_id
    members = (
        await db.execute(
            text(
                "SELECT student_id FROM lesson_slot_student "
                " WHERE slot_id = :s AND is_active"
            ),
            {"s": slot_id},
        )
    ).scalars().all()
    assert members == [keep_id]


@pytest.mark.asyncio
async def test_apply_rejects_hour_outside_grid(db):
    """11:00 в плане — ошибка: этого часа осенью нет."""
    from app.utils.exceptions import DomainError

    teacher_id = await _create_user(db, role="teacher")
    with pytest.raises(DomainError, match="вне осенней сетки"):
        await schedule_plan_service.apply_plan(
            db,
            SchedulePlanApplyRequest(
                teacher_id=teacher_id,
                slots=[SchedulePlanApplySlot(weekday=0, start_time=time(hour=11))],
                dry_run=True,
            ),
            actor_id=None,
        )


@pytest.mark.asyncio
async def test_apply_rejects_overfilled_slot(db):
    """Одиннадцать человек в слоте не принимаются даже вручную."""
    from app.utils.exceptions import DomainError

    teacher_id = await _create_user(db, role="teacher")
    with pytest.raises(DomainError, match="больше 10"):
        await schedule_plan_service.apply_plan(
            db,
            SchedulePlanApplyRequest(
                teacher_id=teacher_id,
                slots=[
                    SchedulePlanApplySlot(
                        weekday=0,
                        start_time=time(hour=12),
                        student_ids=list(range(1, 13)),
                    )
                ],
                dry_run=True,
            ),
            actor_id=None,
        )


@pytest.mark.asyncio
async def test_plan_is_closed_for_students(client, db):
    """Ученику вёрстка недоступна: слот — решение методиста."""
    student_id = await _create_user(db, role="student")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.get(
        "/api/v1/methodist/schedule-plan",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_tighten_drops_hour_nobody_needs():
    """Час, без которого никто ничего не теряет, из предложения уходит.

    Жадный подбор берёт час ради двоих, а потом эти двое помещаются в общий
    слот. Формально сетка не портится, но методист видит слот на одного и час
    преподавателя, потраченный впустую.
    """
    people = [_student(i, 1, [(0, 12), (0, 13)]) for i in range(1, 4)]
    tightened = schedule_plan_service._tighten(
        people, [(0, time(hour=12)), (0, time(hour=13))], protected=set()
    )

    assert len(tightened) == 1
