"""tsk-741 фаза 3: домашняя работа — норма, выдача, состав, выполнение.

Проверяется то, из-за чего механика может тихо врать:

- **срок экзамена от класса** — 11 класс сдаёт этим летом, 10 — следующим,
  класс не указан считается как 11 (решение оператора 01.09);
- **класс влияет на объём** — через целевую недельную норму (11 → 20, 10 и 9 →
  12, младше → 8). Первая редакция выводила нагрузку из «остатка программы,
  делённого на недели до экзамена», и на живых данных класс переставал влиять
  вовсе: курс — банк из 1758 заданий, остаток 1700-4800, «надо» упиралось в
  потолок у всех;
- **норма** — не выше того, что человек тянет (`факт × 1.2`), не ниже пола, не
  больше остатка программы; поправка на качество при доле верных ниже 60%;
- **выдача** — материалы попадают домой наравне с заданиями и идут первыми
  (прямое требование «теорию учат дома»), уже пройденное не выдаётся повторно;
- **выполнение считается у источника** — верная сдача закрывает пункт ДЗ, хотя
  никто ничего не «отмечал»; ручной зачёт преподавателя темпом не считается;
- **одна действующая выдача** — новая гасит прежнюю;
- **сводка перед занятием** отличает «не задавали» (null) от «не сделал» (0).

На настоящей БД, по образцу test_tsk494_student_dashboard.py.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services import homework_service, homework_volume_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk741hw"


# ============================== Helpers ==============================


async def _new_user(db, *, role: str | None = "student", name: str = "student") -> tuple[int, str]:
    user = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", user.email)
    token, _, _ = await create_session(db, user_id=user.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "role": role},
        )
    await db.commit()
    return user.id, token


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": f"{_TAG}-{title}"},
        )
    ).scalar()


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _new_task(db, *, course_id: int, order_position: int) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
                "  external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, :pos) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} задача {order_position}"}),
                "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                "pos": order_position,
            },
        )
    ).scalar()


async def _new_material(db, *, course_id: int, order_position: int) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO materials (course_id, title, type, content, order_position) "
                "VALUES (:c, :t, 'text', CAST(:content AS jsonb), :pos) RETURNING id"
            ),
            {
                "c": course_id,
                "t": f"{_TAG} материал {order_position}",
                "content": json.dumps({"body": "x"}),
                "pos": order_position,
            },
        )
    ).scalar()


async def _submit(
    db, *, student_id: int, task_id: int, course_id: int, is_correct: bool, at: datetime,
    source: str = "spw_web",
) -> None:
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, :src)"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "ts": at, "src": source,
        },
    )
    await db.commit()


async def _complete_material(db, *, student_id: int, material_id: int, at: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "  (student_id, material_id, status, completed_at, source) "
            "VALUES (:s, :m, 'completed', :ts, 'system')"
        ),
        {"s": student_id, "m": material_id, "ts": at},
    )
    await db.commit()


async def _create_occurrence(
    db, *, student_id: int, teacher_id: int, scheduled_at: datetime,
) -> int:
    """Занятие с одним участником — автовыдача ссылается на него внешним ключом."""
    occurrence = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=60,
    )
    db.add(occurrence)
    await db.flush()
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=occurrence.id, student_id=student_id, status="confirmed",
        )
    )
    occurrence_id = occurrence.id
    await db.commit()
    return occurrence_id


async def _set_grade(db, *, student_id: int, grade: int | None) -> None:
    await db.execute(
        text(
            "UPDATE users SET category = :cat, school_grade = :g WHERE id = :u"
        ),
        {"u": student_id, "g": grade, "cat": "school_student" if grade else None},
    )
    await db.commit()


# ====================== Срок экзамена от класса ======================


def test_exam_date_eleventh_grade_is_this_academic_year():
    """11 класс 1 сентября 2026 сдаёт в июне 2027."""
    assert homework_volume_service.exam_date_for(11, date(2026, 9, 1)) == date(2027, 6, 1)


def test_exam_date_tenth_grade_is_a_year_later():
    """10 класс — годом позже: именно эта разница и есть смысл вопроса о классе."""
    assert homework_volume_service.exam_date_for(10, date(2026, 9, 1)) == date(2028, 6, 1)


def test_exam_date_ninth_grade_is_oge_this_year():
    """9 класс сдаёт ОГЭ этим же летом, а не через два года."""
    assert homework_volume_service.exam_date_for(9, date(2026, 9, 1)) == date(2027, 6, 1)


def test_exam_date_unknown_grade_assumes_eleventh():
    """Класс не указан — считаем пессимистично (решение оператора 01.09)."""
    assert homework_volume_service.exam_date_for(None, date(2026, 9, 1)) == date(2027, 6, 1)


def test_exam_date_after_june_rolls_to_next_year():
    """В июле экзамен этого года уже прошёл — считаем до следующего."""
    assert homework_volume_service.exam_date_for(11, date(2027, 7, 10)) == date(2028, 6, 1)


def test_exam_date_for_younger_counts_to_oge():
    """Семикласснику ближайший экзамен — ОГЭ через два года."""
    assert homework_volume_service.exam_date_for(7, date(2026, 9, 1)) == date(2029, 6, 1)


# ============================== Норма ==============================


@pytest.mark.asyncio
async def test_volume_has_floor_for_idle_student(db):
    """У молчащего ученика факт нулевой, но норма не ноль.

    18 из 60 учеников на проде за месяц не решили ничего — если бы механика
    молчала при нулевом темпе, она молчала бы ровно там, где нужнее всего.
    """
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "idle")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, 40):
        await _new_task(db, course_id=course_id, order_position=pos)
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.fact_per_week == 0.0
    assert plan.volume_per_week == homework_volume_service.MIN_PER_WEEK
    assert plan.remaining_items == 39
    assert plan.grade == 11 and plan.grade_assumed is False
    # Норма класса видна, даже когда объём до неё не дотягивает: разрыв — это и
    # есть сигнал преподавателю.
    assert plan.target_per_week == 20
    assert plan.pace_gap == 20


@pytest.mark.asyncio
async def test_grade_changes_the_target(db):
    """Класс влияет на норму — ради этого и спрашивали про класс.

    Первая редакция формулы выводила нагрузку из «остатка программы, делённого
    на недели до экзамена». На живых данных 01.09 остаток оказался 1700-4800
    элементов (курс — банк заданий, а не конечная программа), «надо» выходило
    52-58 в неделю у всех, всегда упиралось в потолок, и класс переставал
    влиять на объём вовсе. Этот тест держит исправление.
    """
    now = datetime.now(UTC)
    plans = {}
    for grade in (11, 10, 7):
        student_id, _ = await _new_user(db)
        course_id = await _new_course(db, f"grade{grade}")
        await _enroll(db, student_id=student_id, course_id=course_id)
        for pos in range(1, 60):
            task_id = await _new_task(db, course_id=course_id, order_position=pos)
            if pos <= 45:
                # Быстрый ученик: 15 сдач в каждую из трёх недель.
                await _submit(
                    db, student_id=student_id, task_id=task_id, course_id=course_id,
                    is_correct=True, at=now - timedelta(days=(pos % 3) * 7 + 1),
                )
        await _set_grade(db, student_id=student_id, grade=grade)
        plans[grade] = await homework_volume_service.compute(db, student_id=student_id)

    assert plans[11].target_per_week == 20
    assert plans[10].target_per_week == 12
    assert plans[7].target_per_week == 8
    # Тот же темп, разные классы — разный объём: одиннадцатикласснику больше.
    assert plans[11].volume_per_week > plans[10].volume_per_week
    assert plans[10].volume_per_week > plans[7].volume_per_week


@pytest.mark.asyncio
async def test_volume_never_exceeds_remaining_program(db):
    """Больше, чем осталось в программе, не задаём: выдавать нечего."""
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "almost-done")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, 3):
        await _new_task(db, course_id=course_id, order_position=pos)
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.remaining_items == 2
    assert plan.volume_per_week == 2


@pytest.mark.asyncio
async def test_volume_does_not_exceed_grade_target(db):
    """Быстрый ученик получает ровно норму своего класса, не больше.

    Потолок `MAX_PER_WEEK` остаётся крайним предохранителем (он откалиброван по
    p90 живого темпа), но раньше него срабатывает цель класса — сегодня она
    ниже для всех классов.
    """
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "huge")
    await _enroll(db, student_id=student_id, course_id=course_id)
    now = datetime.now(UTC)
    # Быстрый ученик: много верных сдач в каждой из трёх недель.
    for week in range(3):
        for pos in range(40):
            task_id = await _new_task(db, course_id=course_id, order_position=pos + week * 100)
            await _submit(
                db, student_id=student_id, task_id=task_id, course_id=course_id,
                is_correct=True, at=now - timedelta(days=week * 7 + 1),
            )
    for pos in range(500, 1500):
        await _new_task(db, course_id=course_id, order_position=pos)
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.volume_per_week == plan.target_per_week == 20
    assert plan.volume_per_week <= homework_volume_service.MAX_PER_WEEK
    assert plan.pace_gap == 0, "человек и так делает норму — отставания нет"


@pytest.mark.asyncio
async def test_volume_penalised_when_quality_is_low(db):
    """Доля верных ниже 60% — объём уменьшается: человек тонет.

    Решение оператора 01.09: «скорость с поправкой на качество». Без поправки
    тот, кто угадывает и ошибается, получал бы БОЛЬШЕ заданий, а не меньше.
    """
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "sinking")
    await _enroll(db, student_id=student_id, course_id=course_id)
    now = datetime.now(UTC)
    for pos in range(1, 200):
        await _new_task(db, course_id=course_id, order_position=pos)
    # 6 верных и 20 неверных за окно: доля верных ≈ 0.23.
    correct_ids = [await _new_task(db, course_id=course_id, order_position=900 + i) for i in range(6)]
    wrong_ids = [await _new_task(db, course_id=course_id, order_position=800 + i) for i in range(20)]
    for task_id in correct_ids:
        await _submit(db, student_id=student_id, task_id=task_id, course_id=course_id,
                      is_correct=True, at=now - timedelta(days=2))
    for task_id in wrong_ids:
        await _submit(db, student_id=student_id, task_id=task_id, course_id=course_id,
                      is_correct=False, at=now - timedelta(days=2))
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.correct_ratio is not None and plan.correct_ratio < 0.6
    assert plan.quality_penalty_applied is True


@pytest.mark.asyncio
async def test_volume_ignores_manual_grants(db):
    """Ручной зачёт преподавателя темпом ученика не считается (tsk-656).

    Ручные отметки ставят пачками; приняв их за темп, формула задала бы
    человеку норму, которой он никогда не делал.
    """
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "manual")
    await _enroll(db, student_id=student_id, course_id=course_id)
    now = datetime.now(UTC)
    for pos in range(1, 100):
        await _new_task(db, course_id=course_id, order_position=pos)
    for i in range(30):
        task_id = await _new_task(db, course_id=course_id, order_position=500 + i)
        await _submit(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=True, at=now - timedelta(days=3), source="manual_teacher",
        )
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.fact_per_week == 0.0, "ручные зачёты попали в темп"


@pytest.mark.asyncio
async def test_volume_for_window_scales_by_days(db):
    """Норма недельная, а выдача — до следующего занятия."""
    plan = homework_volume_service.VolumePlan(
        grade=11, grade_assumed=False, exam_date=date(2027, 6, 1), weeks_to_exam=39.0,
        remaining_items=100, target_per_week=20, fact_per_week=10.0, correct_ratio=0.9,
        quality_penalty_applied=False, volume_per_week=14, weeks_of_program_left=7,
        needs_more_program=False, exam_sprint=False, missed_lessons=0,
        catch_up_factor=1.0, pace_gap=10,
    )
    assert homework_volume_service.volume_for_window(plan, days=7) == 14
    assert homework_volume_service.volume_for_window(plan, days=3) == 6
    # До занятия остался день — «ноль» не выдаём, выдача без состава бессмысленна.
    assert homework_volume_service.volume_for_window(plan, days=0) == 2


# ============================== Выдача ==============================


async def _student_with_program(db, *, materials: int = 2, tasks: int = 5) -> tuple[int, int]:
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "program")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, materials + 1):
        await _new_material(db, course_id=course_id, order_position=pos)
    for pos in range(1, tasks + 1):
        await _new_task(db, course_id=course_id, order_position=pos)
    await _set_grade(db, student_id=student_id, grade=11)
    return student_id, course_id


@pytest.mark.asyncio
async def test_issue_puts_theory_first(db):
    """Материалы идут в ДЗ первыми — «теорию учат дома» выполняется само.

    Своего порядка выдача не заводит: она берёт учебный порядок дерева, где
    материалы узла стоят перед его заданиями.
    """
    student_id, _ = await _student_with_program(db)
    due = datetime.now(UTC) + timedelta(days=7)

    homework = await homework_service.issue(
        db, student_id=student_id, due_at=due, source="teacher", volume_override=4,
    )
    await db.commit()

    kinds = [item["kind"] for item in homework["items"]]
    assert kinds[:2] == ["material", "material"], kinds
    assert "task" in kinds
    assert homework["total"] == 4
    assert homework["done"] == 0


@pytest.mark.asyncio
async def test_issue_skips_already_done(db):
    """Пройденное повторно не задаём."""
    student_id, course_id = await _student_with_program(db, materials=1, tasks=3)
    first_material = (
        await db.execute(
            text("SELECT id FROM materials WHERE course_id = :c ORDER BY order_position LIMIT 1"),
            {"c": course_id},
        )
    ).scalar()
    await _complete_material(
        db, student_id=student_id, material_id=first_material, at=datetime.now(UTC),
    )

    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=3,
    )
    await db.commit()

    assert all(
        not (i["kind"] == "material" and i["item_id"] == first_material)
        for i in homework["items"]
    )


@pytest.mark.asyncio
async def test_completion_is_derived_from_real_work(db):
    """Верная сдача закрывает пункт ДЗ, хотя «отметки о выполнении» никто не ставил."""
    student_id, course_id = await _student_with_program(db, materials=0, tasks=3)
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=3,
    )
    await db.commit()
    assert homework["done"] == 0

    first_task = homework["items"][0]["item_id"]
    await _submit(
        db, student_id=student_id, task_id=first_task, course_id=course_id,
        is_correct=True, at=datetime.now(UTC),
    )

    updated = await homework_service.get_current(db, student_id=student_id)
    assert updated["done"] == 1
    assert updated["items"][0]["done"] is True


@pytest.mark.asyncio
async def test_manual_grant_closes_homework_item_but_not_pace(db):
    """Ручной зачёт закрывает пункт ДЗ, но темпом не считается.

    Это два разных вопроса. «Сделано ли задание» решает преподаватель: зачёл —
    значит закрыто, иначе он видел бы красную отметку, которую сам же и снял.
    «С какой скоростью работает человек» — про его собственные сдачи.
    """
    student_id, course_id = await _student_with_program(db, materials=0, tasks=3)
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=3,
    )
    await db.commit()

    await _submit(
        db, student_id=student_id, task_id=homework["items"][0]["item_id"],
        course_id=course_id, is_correct=True, at=datetime.now(UTC),
        source="manual_teacher",
    )

    updated = await homework_service.get_current(db, student_id=student_id)
    assert updated["done"] == 1, "ручной зачёт обязан закрывать пункт ДЗ"

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.fact_per_week == 0.0, "ручной зачёт не должен считаться темпом"


@pytest.mark.asyncio
async def test_new_issue_cancels_previous(db):
    """Действующая выдача одна: иначе «текущее ДЗ» перестаёт быть определённым."""
    student_id, _ = await _student_with_program(db)
    due = datetime.now(UTC) + timedelta(days=7)
    first = await homework_service.issue(
        db, student_id=student_id, due_at=due, source="teacher", volume_override=2,
    )
    await db.commit()
    second = await homework_service.issue(
        db, student_id=student_id, due_at=due, source="teacher", volume_override=3,
    )
    await db.commit()

    assert second["id"] != first["id"]
    current = await homework_service.get_current(db, student_id=student_id)
    assert current["id"] == second["id"]
    cancelled = (
        await db.execute(
            text("SELECT cancelled_at FROM homework_assignment WHERE id = :i"),
            {"i": first["id"]},
        )
    ).scalar()
    assert cancelled is not None


@pytest.mark.asyncio
async def test_issue_rejects_past_due(db):
    """Срок в прошлом — не выдача, а ошибка."""
    student_id, _ = await _student_with_program(db)
    with pytest.raises(ValueError):
        await homework_service.issue(
            db, student_id=student_id, due_at=datetime.now(UTC) - timedelta(days=1),
            source="teacher",
        )


@pytest.mark.asyncio
async def test_overdue_when_deadline_passed_and_not_done(db):
    """Просрочка видна, но ничего не блокирует — это показатель, а не долг."""
    student_id, _ = await _student_with_program(db)
    now = datetime.now(UTC)
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=now + timedelta(days=1), source="teacher",
        volume_override=2,
    )
    await db.commit()
    assert homework["is_overdue"] is False

    later = await homework_service.get_current(
        db, student_id=student_id, now=now + timedelta(days=2),
    )
    assert later["is_overdue"] is True


@pytest.mark.asyncio
async def test_status_distinguishes_never_assigned_from_not_done(db):
    """«Не задавали» и «не сделал» — разные утверждения.

    Спутать их на экране перед занятием дороже всего: преподаватель спросит с
    человека за то, чего ему не давали.
    """
    assigned_student, _ = await _student_with_program(db)
    silent_student, _ = await _new_user(db)

    await homework_service.issue(
        db, student_id=assigned_student, due_at=datetime.now(UTC) + timedelta(days=3),
        source="teacher", volume_override=2,
    )
    await db.commit()

    status = await homework_service.status_for_students(
        db, student_ids=[assigned_student, silent_student],
    )
    assert status[assigned_student]["assigned_total"] == 2
    assert status[assigned_student]["assigned_done"] == 0
    assert silent_student not in status, "ученику без выдачи нельзя подставлять ноль"


@pytest.mark.asyncio
async def test_cancel_is_idempotent(db):
    """Отмена повторно ничего не ломает и выдачу не удаляет."""
    student_id, _ = await _student_with_program(db)
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=3),
        source="teacher", volume_override=2,
    )
    await db.commit()

    assert await homework_service.cancel(db, homework_id=homework["id"]) is True
    assert await homework_service.cancel(db, homework_id=homework["id"]) is False
    await db.commit()

    assert await homework_service.get_current(db, student_id=student_id) is None
    still_there = (
        await db.execute(
            text("SELECT count(*) FROM homework_assignment WHERE id = :i"),
            {"i": homework["id"]},
        )
    ).scalar()
    assert still_there == 1, "отменённая выдача должна оставаться в истории"


# ========================= Автовыдача после занятия =========================


@pytest.mark.asyncio
async def test_auto_issue_is_off_by_default(db, monkeypatch):
    """Рубильник выключен — автовыдача молчит.

    Формула согласована, но на живых учениках не обкатана, а выдача видна
    ученику сразу. Включение — переключатель, без выката.
    """
    from app.core import settings_store

    student_id, _ = await _student_with_program(db)
    monkeypatch.setattr(settings_store, "get_bool", lambda key: False)

    result = await homework_service.auto_issue_after_lesson(
        db, student_id=student_id, occurrence_id=1,
        occurrence_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert result is None
    assert await homework_service.get_current(db, student_id=student_id) is None


@pytest.mark.asyncio
async def test_auto_issue_does_not_repeat_for_same_lesson(db, monkeypatch):
    """Повторная отметка явки не перевыдаёт ДЗ.

    Преподаватель правит статусы задним числом; каждая новая выдача гасит
    прежнюю — ученик потерял бы то, что уже начал делать.
    """
    from app.core import settings_store

    student_id, _ = await _student_with_program(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)
    occurrence_at = datetime.now(UTC) - timedelta(hours=1)
    occurrence_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=occurrence_at,
    )

    first = await homework_service.auto_issue_after_lesson(
        db, student_id=student_id, occurrence_id=occurrence_id,
        occurrence_at=occurrence_at,
    )
    await db.commit()
    assert first is not None
    assert first["source"] == "auto"

    again = await homework_service.auto_issue_after_lesson(
        db, student_id=student_id, occurrence_id=occurrence_id,
        occurrence_at=occurrence_at,
    )
    assert again is None
    current = await homework_service.get_current(db, student_id=student_id)
    assert current["id"] == first["id"]


@pytest.mark.asyncio
async def test_completion_ratio_skips_students_without_assignments(db):
    """Кому не задавали — у того нет доли, а не ноль.

    Ноль утянул бы человека в нижний терциль сравнения с группой за то, чего
    ему не давали.
    """
    student_id, course_id = await _student_with_program(db, materials=0, tasks=4)
    silent_student, _ = await _new_user(db)
    period_from = datetime.now(UTC) - timedelta(days=1)
    period_to = datetime.now(UTC) + timedelta(days=1)

    homework = await homework_service.issue(
        db, student_id=student_id, due_at=period_to, source="teacher", volume_override=4,
    )
    await db.commit()
    await _submit(
        db, student_id=student_id, task_id=homework["items"][0]["item_id"],
        course_id=course_id, is_correct=True, at=datetime.now(UTC),
    )

    ratios = await homework_service.completion_ratio_for_students(
        db, student_ids=[student_id, silent_student],
        period_from=period_from, period_to=period_to,
    )
    assert ratios[student_id] == pytest.approx(0.25)
    assert silent_student not in ratios


# ============== Необязательное, опережение и финишный спринт ==============


@pytest.mark.asyncio
async def test_recommended_items_are_not_part_of_the_program(db):
    """Необязательные задания в остаток программы не входят.

    Вопрос оператора 01.09. В ВЫДАЧУ они не попадали и раньше — дерево курса
    фильтрует их тем же правилом, что движок, — а вот остаток считался по
    всему подряд. На проде это половина: у одного ученика 1224 обязательных
    задания против 982 рекомендованных, то есть «сколько ещё осталось» врало
    почти вдвое, и ученик с опережением не получил бы сигнала вовремя.
    """
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "mixed")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, 6):
        await _new_task(db, course_id=course_id, order_position=pos)
    recommended = [await _new_task(db, course_id=course_id, order_position=90 + i) for i in range(4)]
    await db.execute(
        text("UPDATE tasks SET requirement_level = 'recommended' WHERE id = ANY(:ids)"),
        {"ids": recommended},
    )
    await db.commit()
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.remaining_items == 5, "рекомендованные попали в остаток программы"


@pytest.mark.asyncio
async def test_program_running_out_is_visible_in_advance(db):
    """Идущего с опережением видно ЗАРАНЕЕ, а не в день, когда задавать нечего.

    Требование оператора 01.09: без ДЗ такого ученика не оставляем — значит
    ему нужно добавить курс, и узнать об этом надо загодя.
    """
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "ending")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, 7):
        await _new_task(db, course_id=course_id, order_position=pos)
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    # Норма 3 (пол, темпа нет), остатка 6 — хватит на две недели.
    assert plan.weeks_of_program_left == 2
    assert plan.needs_more_program is True


@pytest.mark.asyncio
async def test_full_program_does_not_ask_for_more(db):
    """Пока программы вдоволь, о новых курсах не напоминаем."""
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "plenty")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, 60):
        await _new_task(db, course_id=course_id, order_position=pos)
    await _set_grade(db, student_id=student_id, grade=11)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.needs_more_program is False
    assert plan.weeks_of_program_left is not None and plan.weeks_of_program_left >= 4


def test_final_grade_target_drops_in_march():
    """С марта у выпускников норма падает: время уходит на варианты.

    До марта одиннадцатикласснику 20 в неделю, с 1 марта — 6: он решает
    1-2 полных варианта в неделю, и на обычное ДЗ времени почти не остаётся.
    Оставить прежние 20 значило бы весь финиш показывать «не дотягивает»,
    хотя человек занят ровно тем, чем должен.
    """
    assert homework_volume_service.target_per_week_for(11, date(2026, 12, 1)) == 20
    assert homework_volume_service.target_per_week_for(11, date(2027, 3, 1)) == 6
    assert homework_volume_service.target_per_week_for(11, date(2027, 5, 20)) == 6


def test_march_sprint_does_not_touch_other_grades():
    """Десятикласснику в марте до его экзамена ещё год — норма прежняя."""
    assert homework_volume_service.target_per_week_for(10, date(2027, 3, 1)) == 12
    assert homework_volume_service.target_per_week_for(9, date(2027, 3, 1)) == 12


def test_unknown_grade_follows_the_final_grade_sprint():
    """Класс не указан — считаем как 11, значит и спринт с марта тот же."""
    assert homework_volume_service.target_per_week_for(None, date(2026, 12, 1)) == 20
    assert homework_volume_service.target_per_week_for(None, date(2027, 3, 1)) == 6


# ================= Мотивация: напоминание и видимость =================


async def _reminder_content(db, *, student_id: int) -> tuple[str, dict]:
    """Текст и payload последнего напоминания «Скоро занятие» этому ученику."""
    row = (
        await db.execute(
            text(
                "SELECT content, payload FROM notifications "
                " WHERE kind = 'lesson_reminder' AND user_id = :u "
                " ORDER BY id DESC LIMIT 1"
            ),
            {"u": student_id},
        )
    ).one()
    return row.content, row.payload


@pytest.mark.asyncio
async def test_reminder_mentions_homework_only_when_something_is_left(
    db, db_session_factory
):
    """Строка про ДЗ едет ВНУТРИ напоминания о занятии и только при несделанном.

    Решение оператора 01.09. Отдельной рассылки нет намеренно: `lesson_reminder`
    — самый читаемый канал у учеников (60% против 16% у «ученик молчит»), и
    второй рядом делил бы то же внимание. Кто всё сделал — лишнего не читает,
    иначе похвала в каждом напоминании обесценивается.
    """
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, course_id = await _student_with_program(db, materials=0, tasks=4)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = 'scheduled' "
            " WHERE student_id = :s"
        ),
        {"s": student_id},
    )
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=2),
        source="teacher", volume_override=4,
    )
    await db.commit()

    await lesson_attendance_cron_tick(db_session_factory)
    content, payload = await _reminder_content(db, student_id=student_id)
    assert "Домашняя работа: 0 из 4" in content
    # Числа отдельно от текста: бот показывает их по-своему, разбирать строку
    # ему нельзя.
    assert payload["homework_done"] == 0 and payload["homework_total"] == 4
    # Факт без оценки: ни похвалы, ни укора, ни «ты отстаёшь».
    for forbidden in ("отстаёшь", "успей", "молодец", "!"):
        assert forbidden not in content, content


@pytest.mark.asyncio
async def test_reminder_stays_silent_when_homework_is_done(db, db_session_factory):
    """Всё сделано — напоминание прежнее, без строки про ДЗ."""
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, course_id = await _student_with_program(db, materials=0, tasks=2)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = 'scheduled' "
            " WHERE student_id = :s"
        ),
        {"s": student_id},
    )
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=2),
        source="teacher", volume_override=2,
    )
    await db.commit()
    for item in homework["items"]:
        await _submit(
            db, student_id=student_id, task_id=item["item_id"], course_id=course_id,
            is_correct=True, at=datetime.now(UTC),
        )

    await lesson_attendance_cron_tick(db_session_factory)
    content, _ = await _reminder_content(db, student_id=student_id)
    assert "Домашняя работа" not in content, content


@pytest.mark.asyncio
async def test_reminder_survives_student_without_homework(db, db_session_factory):
    """Ученику без выдачи напоминание приходит как раньше — и не падает."""
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, _ = await _new_user(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = 'scheduled' "
            " WHERE student_id = :s"
        ),
        {"s": student_id},
    )
    await db.commit()

    await lesson_attendance_cron_tick(db_session_factory)
    content, payload = await _reminder_content(db, student_id=student_id)
    assert "Занятие начинается" in content
    assert "Домашняя работа" not in content
    assert payload["homework_total"] is None


# ============== Пропуски: нагоняем, но не за перенос ==============


async def _lesson_with_status(
    db, *, student_id: int, teacher_id: int, days_ago: int, status: str,
    rescheduled_to: int | None = None,
) -> int:
    """Прошедшее занятие ученика в нужном статусе участия."""
    occurrence_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant "
            "   SET status = :st, rescheduled_to_occurrence_id = :to "
            " WHERE occurrence_id = :oid AND student_id = :sid"
        ),
        {"st": status, "to": rescheduled_to, "oid": occurrence_id, "sid": student_id},
    )
    await db.commit()
    return occurrence_id


async def _student_with_pace(db, *, tasks: int = 80, done_per_week: int = 8):
    """Ученик с ровным темпом: столько верных сдач в каждую из трёх недель."""
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "pace")
    await _enroll(db, student_id=student_id, course_id=course_id)
    now = datetime.now(UTC)
    for pos in range(1, tasks + 1):
        task_id = await _new_task(db, course_id=course_id, order_position=pos)
        week = (pos - 1) // done_per_week
        if week < 3:
            await _submit(
                db, student_id=student_id, task_id=task_id, course_id=course_id,
                is_correct=True, at=now - timedelta(days=week * 7 + 1),
            )
    await _set_grade(db, student_id=student_id, grade=11)
    return student_id, course_id


@pytest.mark.asyncio
async def test_missed_lesson_increases_the_volume(db):
    """Не пришёл — материал занятия придётся пройти самому, объём растёт.

    Требование оператора 02.09.
    """
    student_id, _ = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    before = await homework_volume_service.compute(db, student_id=student_id)

    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3, status="no_show",
    )
    after = await homework_volume_service.compute(db, student_id=student_id)

    assert before.missed_lessons == 0 and before.catch_up_factor == 1.0
    assert after.missed_lessons == 1
    assert after.catch_up_factor == 1.25
    assert after.volume_per_week > before.volume_per_week


@pytest.mark.asyncio
async def test_rescheduled_lesson_is_not_a_miss(db):
    """Перенёс — нагонять нечего: занятие состоится.

    Прямое требование оператора 02.09 и то, ради чего пропуск и перенос вообще
    различаются. На проде все 38 переносов несут ссылку, куда участие переехало,
    а `no_show` не несёт её ни разу — состояния в данных не смешиваются.
    """
    student_id, _ = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    before = await homework_volume_service.compute(db, student_id=student_id)

    target = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(days=2),
    )
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3,
        status="rescheduled", rescheduled_to=target,
    )
    after = await homework_volume_service.compute(db, student_id=student_id)

    assert after.missed_lessons == 0
    assert after.catch_up_factor == 1.0
    assert after.volume_per_week == before.volume_per_week


@pytest.mark.asyncio
async def test_break_is_not_a_miss(db):
    """Перерыв — не прогул: школа сама поставила паузу."""
    student_id, _ = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3, status="on_break",
    )
    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.missed_lessons == 0 and plan.catch_up_factor == 1.0


@pytest.mark.asyncio
async def test_catch_up_has_a_ceiling(db):
    """Нагон упирается в полтора объёма, сколько бы ни пропустил.

    Пропустивший занятия — чаще всего и есть отстающий, и удвоенная выдача для
    него не «нагон», а повод бросить совсем.
    """
    student_id, _ = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    for days_ago in (2, 5, 9, 12, 16):
        await _lesson_with_status(
            db, student_id=student_id, teacher_id=teacher_id,
            days_ago=days_ago, status="no_show",
        )
    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.missed_lessons == 5
    assert plan.catch_up_factor == homework_volume_service.MAX_CATCH_UP_FACTOR


@pytest.mark.asyncio
async def test_attended_lesson_changes_nothing(db):
    """Пришёл — нагонять нечего."""
    student_id, _ = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    before = await homework_volume_service.compute(db, student_id=student_id)
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3, status="confirmed",
    )
    after = await homework_volume_service.compute(db, student_id=student_id)
    assert after.missed_lessons == 0
    assert after.volume_per_week == before.volume_per_week


# ============ Сводка занятия: ДЗ на момент ЭТОГО занятия ============


@pytest.mark.asyncio
async def test_summary_shows_homework_the_student_had_to_bring(db):
    """У прошедшего занятия видно ДЗ, которое к нему задавали, а не итог урока.

    Дефект, замеченный оператором 02.09: после занятия автовыдача создаёт новую
    домашнюю работу — к СЛЕДУЮЩЕМУ занятию. Сводка брала «текущую действующую»
    и показывала на прошедшем занятии именно её, хотя на нём проверяли совсем
    другое.
    """
    student_id, _ = await _student_with_program(db, materials=0, tasks=12)
    lesson_at = datetime.now(UTC) - timedelta(hours=2)

    # Задано ДО занятия — это ученик и должен был принести.
    before_lesson = await homework_service.issue(
        db, student_id=student_id, due_at=lesson_at, source="teacher",
        volume_override=3, now=lesson_at - timedelta(days=3),
    )
    await db.commit()
    # Задано ПОСЛЕ занятия — это уже к следующему.
    after_lesson = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=5),
        source="auto", volume_override=4, now=lesson_at + timedelta(minutes=61),
    )
    await db.commit()

    at_lesson = await homework_service.status_for_students(
        db, student_ids=[student_id], as_of=lesson_at,
    )
    assert at_lesson[student_id]["homework_id"] == before_lesson["id"]
    assert at_lesson[student_id]["assigned_total"] == 3

    # А «сейчас» — по-прежнему свежая выдача: экран ученика не меняется.
    now_status = await homework_service.status_for_students(
        db, student_ids=[student_id],
    )
    assert now_status[student_id]["homework_id"] == after_lesson["id"]
    assert now_status[student_id]["assigned_total"] == 4


@pytest.mark.asyncio
async def test_summary_silent_when_nothing_was_assigned_before_the_lesson(db):
    """На занятии, к которому ничего не задавали, полей плана нет.

    «Не задавали» и «не сделал» — разные утверждения; появившаяся позже выдача
    не должна задним числом превращаться в долг к прошедшему занятию.
    """
    student_id, _ = await _student_with_program(db, materials=0, tasks=6)
    lesson_at = datetime.now(UTC) - timedelta(hours=2)

    await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=5),
        source="auto", volume_override=3, now=lesson_at + timedelta(minutes=61),
    )
    await db.commit()

    at_lesson = await homework_service.status_for_students(
        db, student_ids=[student_id], as_of=lesson_at,
    )
    assert student_id not in at_lesson


@pytest.mark.asyncio
async def test_manual_cancel_before_the_lesson_hides_the_assignment(db):
    """Отменённое ДО занятия на нём не показывается.

    Отличать «погашено следующей выдачей» от «преподаватель передумал» нечем,
    поэтому смотрим на момент: если к началу занятия выдача уже была отменена,
    ученик её не нёс.
    """
    student_id, _ = await _student_with_program(db, materials=0, tasks=6)
    lesson_at = datetime.now(UTC) - timedelta(hours=2)

    homework = await homework_service.issue(
        db, student_id=student_id, due_at=lesson_at, source="teacher",
        volume_override=2, now=lesson_at - timedelta(days=2),
    )
    await db.commit()
    await homework_service.cancel(
        db, homework_id=homework["id"], now=lesson_at - timedelta(hours=1),
    )
    await db.commit()

    at_lesson = await homework_service.status_for_students(
        db, student_ids=[student_id], as_of=lesson_at,
    )
    assert student_id not in at_lesson


@pytest.mark.asyncio
async def test_auto_issue_respects_what_the_teacher_assigned_himself(db, monkeypatch):
    """Преподаватель задал ДЗ сам — отметка явки его выдачу НЕ перезаписывает.

    Вопрос оператора 02.09: «если кнопку не нажмёт, задание назначится
    автоматом?». Ответ «да» верен только когда преподаватель ничего не задавал.
    А если задал — автовыдача не должна затирать его работу: ученик увидел бы
    один список, а преподаватель задавал другой. Ручная выдача идёт без
    `occurrence_id`, поэтому проверка «уже выдавали по этому занятию» её не
    видела.
    """
    from app.core import settings_store

    student_id, _ = await _student_with_program(db, materials=0, tasks=12)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    occurrence_at = datetime.now(UTC) - timedelta(minutes=30)
    occurrence_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=occurrence_at,
    )

    # Преподаватель задал сам, из карточки ученика: занятие в выдаче не указано.
    manual = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=5),
        source="teacher", issued_by=teacher_id, volume_override=2,
    )
    await db.commit()

    # И только потом отметил явку.
    result = await homework_service.auto_issue_after_lesson(
        db, student_id=student_id, occurrence_id=occurrence_id,
        occurrence_at=occurrence_at,
    )
    await db.commit()

    assert result is None, "автовыдача перезаписала то, что задал преподаватель"
    current = await homework_service.get_current(db, student_id=student_id)
    assert current["id"] == manual["id"]
    assert current["source"] == "teacher"


# ======== Автовыдача после занятия, кто бы ни отметил явку ========


async def _finished_lesson(db, *, student_id: int, teacher_id: int, status: str,
                           ended_minutes_ago: int = 30) -> int:
    """Занятие, которое уже закончилось, с нужным статусом участия."""
    scheduled_at = datetime.now(UTC) - timedelta(minutes=60 + ended_minutes_ago)
    occurrence_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=scheduled_at,
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = :st "
            " WHERE occurrence_id = :oid AND student_id = :sid"
        ),
        {"st": status, "oid": occurrence_id, "sid": student_id},
    )
    await db.commit()
    return occurrence_id


@pytest.mark.asyncio
async def test_homework_appears_when_student_marked_attendance_himself(
    db, db_session_factory, monkeypatch
):
    """Явку поставил ученик, а не преподаватель — ДЗ всё равно появляется.

    Решение оператора 02.09. Раньше автовыдача висела только на действии
    преподавателя «Пришёл»: подтвердил ученик сам или сработала автоотметка
    «сел за работу» (tsk-439) — домашней работы не было вовсе.
    """
    from app.core import settings_store
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, _ = await _student_with_program(db, materials=0, tasks=10)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _finished_lesson(
        db, student_id=student_id, teacher_id=teacher_id, status="confirmed",
    )
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    summary = await lesson_attendance_cron_tick(db_session_factory)
    assert summary["homework_issued"] >= 1

    homework = await homework_service.get_current(db, student_id=student_id)
    assert homework is not None and homework["source"] == "auto"


@pytest.mark.asyncio
async def test_no_homework_while_the_lesson_is_still_running(
    db, db_session_factory, monkeypatch
):
    """Занятие ещё идёт — ДЗ не выдаём.

    Ученик подтверждает явку и накануне; выдай в тот момент — и он получит
    домашнюю работу до урока, из того самого материала, который на уроке и
    будут разбирать. Момент выдачи — конец занятия, а не отметка явки.
    """
    from app.core import settings_store
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, _ = await _student_with_program(db, materials=0, tasks=10)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    occurrence_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = 'confirmed' "
            " WHERE occurrence_id = :oid"
        ),
        {"oid": occurrence_id},
    )
    await db.commit()
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    await lesson_attendance_cron_tick(db_session_factory)
    assert await homework_service.get_current(db, student_id=student_id) is None


@pytest.mark.asyncio
async def test_no_homework_for_the_one_who_did_not_come(
    db, db_session_factory, monkeypatch
):
    """Не пришёл — ДЗ по этому занятию не выдаём: нагонять он будет по формуле."""
    from app.core import settings_store
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, _ = await _student_with_program(db, materials=0, tasks=10)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _finished_lesson(
        db, student_id=student_id, teacher_id=teacher_id, status="no_show",
    )
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    await lesson_attendance_cron_tick(db_session_factory)
    assert await homework_service.get_current(db, student_id=student_id) is None


@pytest.mark.asyncio
async def test_cron_does_not_reissue_homework_on_every_tick(
    db, db_session_factory, monkeypatch
):
    """Второй проход не перевыдаёт: иначе ученик каждые пару минут получал бы
    новый список вместо того, что начал делать."""
    from app.core import settings_store
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, _ = await _student_with_program(db, materials=0, tasks=10)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _finished_lesson(
        db, student_id=student_id, teacher_id=teacher_id, status="confirmed",
    )
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    await lesson_attendance_cron_tick(db_session_factory)
    first = await homework_service.get_current(db, student_id=student_id)
    await lesson_attendance_cron_tick(db_session_factory)
    second = await homework_service.get_current(db, student_id=student_id)
    assert first["id"] == second["id"]


@pytest.mark.asyncio
async def test_cron_is_silent_when_auto_issue_is_off(
    db, db_session_factory, monkeypatch
):
    """Рубильник выключен — фоновый проход тоже молчит."""
    from app.core import settings_store
    from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick

    student_id, _ = await _student_with_program(db, materials=0, tasks=10)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _finished_lesson(
        db, student_id=student_id, teacher_id=teacher_id, status="confirmed",
    )
    monkeypatch.setattr(settings_store, "get_bool", lambda key: False)

    summary = await lesson_attendance_cron_tick(db_session_factory)
    assert summary["homework_issued"] == 0
    assert await homework_service.get_current(db, student_id=student_id) is None
