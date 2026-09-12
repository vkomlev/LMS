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

#: «Сегодня» для тестов нормы и темпа — константа, а не `datetime.now()`.
#:
#: НОРМА КЛАССА зависит от календаря по делу: с марта и до экзамена
#: одиннадцатый класс переходит на отработку вариантов и норма падает с 20 до
#: 6 (`TARGET_PER_WEEK_EXAM_SPRINT`), а альтернативные сроки десятикласснику
#: показываются, только пока до его срока больше 400 дней. Тесты, ожидающие 20
#: и наличие альтернатив, зеленели бы с сентября по февраль и краснели с марта
#: — при верном поведении сервиса (класс дефекта tsk-606: дата в фикстуре
#: против часов внутри сервиса). Сентябрь взят как середина обычного учебного
#: режима.
#:
#: От ДНЯ НЕДЕЛИ расчёт больше не зависит: окна темпа скользящие, по семь дней
#: от момента расчёта (tsk-819). Это держит `test_pace_does_not_depend_on_weekday`.
_PACE_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)  # понедельник


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


async def _new_task(
    db, *, course_id: int, order_position: int, fresh: bool = False
) -> int:
    """Задание курса. По умолчанию — старше любых сдач в тестах (tsk-912):
    иначе правило tsk-692 сочтёт его досыпанным после прохождения и простит.
    `fresh=True` — создано «сейчас», для тестов самого правила."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
                "  external_uid, max_score, order_position, created_at) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, :pos, "
                "  CASE WHEN :fresh THEN now() ELSE now() - interval '400 days' END) "
                "RETURNING id"
            ),
            {
                "fresh": fresh,
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} задача {order_position}"}),
                "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                "pos": order_position,
            },
        )
    ).scalar()


async def _new_material(
    db, *, course_id: int, order_position: int, fresh: bool = False
) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO materials (course_id, title, type, content, order_position, created_at) "
                "VALUES (:c, :t, 'text', CAST(:content AS jsonb), :pos, "
                "  CASE WHEN :fresh THEN now() ELSE now() - interval '400 days' END) RETURNING id"
            ),
            {
                "fresh": fresh,
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

    plan = await homework_volume_service.compute(
        db, student_id=student_id, now=_PACE_NOW,
    )
    assert plan.fact_per_week == 0.0
    # tsk-909: пол — половина нормы класса, а не `MIN_PER_WEEK`. Прежние три
    # элемента при норме двадцать и были «механика молчит»: на проде 11.09 у
    # Хантанова при норме 90 минут задавалось 10.
    assert plan.volume_per_week == round(
        plan.target_per_week * homework_volume_service.TARGET_FLOOR_SHARE
    )
    assert plan.volume_per_week > homework_volume_service.MIN_PER_WEEK
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
    now = _PACE_NOW
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
        plans[grade] = await homework_volume_service.compute(
            db, student_id=student_id, now=now,
        )

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
    now = _PACE_NOW
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

    plan = await homework_volume_service.compute(
        db, student_id=student_id, now=now,
    )
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
        catch_up_factor=1.0, pace_gap=10, program_kind=None,
        program_deadline=None, program_tasks_remaining=None,
        target_unreachable=False,
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
    # tsk-909: норма — половина нормы класса (10), но больше остатка задать
    # нельзя, поэтому выдача упирается в сами 6 элементов: программы хватит
    # ровно на неделю. До подъёма пола норма была 3, и хватало на две.
    assert plan.volume_per_week == plan.remaining_items == 6
    assert plan.weeks_of_program_left == 1
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
    # tsk-914: не множитель, а вес часа занятия — и только за непогашенный.
    assert after.missed_unpaid == 1
    # Вес часа есть только при измеренном весе заданий; без телеметрии нагон
    # идёт в штуках — но идёт.
    if after.effort_measured:
        assert after.lesson_norm_minutes is not None and after.lesson_norm_minutes > 0
    assert after.catch_up_factor > 1.0
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
    assert plan.missed_lessons == 5 and plan.missed_unpaid == 5
    assert plan.catch_up_factor <= homework_volume_service.MAX_CATCH_UP_FACTOR
    # Пять часов по весу часа — заведомо больше полутора объёмов: упёрлись.
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


async def _slot(db, *, teacher_id: int, student_id: int, weekday: int, hour: int) -> int:
    """Постоянный слот расписания с учеником — «план недели» (tsk-914)."""
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, "
                "  timezone, is_active, created_by) "
                "VALUES (:t, :wd, make_time(:h, 0, 0), 60, 'Europe/Moscow', true, :t) RETURNING id"
            ),
            {"t": teacher_id, "wd": weekday, "h": hour},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active, added_by) "
            "VALUES (:s, :u, true, :t)"
        ),
        {"s": slot_id, "u": student_id, "t": teacher_id},
    )
    await db.commit()
    return slot_id


async def _work_in_window(db, *, student_id: int, course_id: int, at: datetime, n: int = 3):
    """`n` верных сдач внутри часа, начавшегося в `at` (tsk-914)."""
    for i in range(n):
        task_id = await _new_task(db, course_id=course_id, order_position=500 + i)
        await _submit(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=True, at=at + timedelta(minutes=10 + i * 5),
        )


@pytest.mark.asyncio
async def test_miss_is_paid_off_by_the_week_hours(db):
    """Пропуск погашен, если часов за неделю отработано не меньше плана (tsk-914).

    Курунов 12.09: переехал с четверга на субботу, слот четверга выключен, но
    уже созданные занятия четверга остались с ним как участником — два
    `no_show` при двух отработанных субботних часах в неделю. Оператор:
    «пропуск добавляет норматив урока, но только если он не погашен».
    """
    student_id, _ = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    # План — два часа в неделю.
    await _slot(db, teacher_id=teacher_id, student_id=student_id, weekday=5, hour=10)
    await _slot(db, teacher_id=teacher_id, student_id=student_id, weekday=5, hour=11)
    # На той же неделе: призрачный пропуск и два отработанных часа.
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=4, status="no_show",
    )
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=2, status="confirmed",
    )
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=1, status="confirmed",
    )
    plan = await homework_volume_service.compute(db, student_id=student_id)

    assert plan.missed_lessons == 1
    assert plan.missed_unpaid == 0, "пропуск погашен двумя отработанными часами, а нагон остался"
    assert plan.catch_up_minutes == 0 and plan.catch_up_factor == 1.0


@pytest.mark.asyncio
async def test_unmarked_but_worked_hour_is_attended(db):
    """Не отметили явку, но человек работал весь час — он был (tsk-914)."""
    student_id, course_id = await _student_with_pace(db)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    occurrence_id = await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3, status="no_show",
    )
    starts_at = (
        await db.execute(
            text("SELECT scheduled_at FROM lesson_occurrence WHERE id = :o"),
            {"o": occurrence_id},
        )
    ).scalar()
    await _work_in_window(db, student_id=student_id, course_id=course_id, at=starts_at)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.missed_lessons == 1 and plan.missed_unpaid == 0


@pytest.mark.asyncio
async def test_work_during_someone_elses_hour_pays_off_a_miss(db):
    """Пропустил свой час, но отработал чужой — пропуск погашен (tsk-914).

    Оператор 12.09: «ученик штатно не перенёс занятие, но фактически был на
    другом часе — это тоже нужно отслеживать».
    """
    student_id, course_id = await _student_with_pace(db)
    other_id, _ = await _new_user(db, name="other")
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3, status="no_show",
    )
    # Чужой час на той же неделе: наш ученик в нём не участник, но работал.
    foreign_at = datetime.now(UTC) - timedelta(days=2)
    await _create_occurrence(
        db, student_id=other_id, teacher_id=teacher_id, scheduled_at=foreign_at,
    )
    await _work_in_window(db, student_id=student_id, course_id=course_id, at=foreign_at)

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.missed_lessons == 1 and plan.missed_unpaid == 0


@pytest.mark.asyncio
async def test_two_submissions_do_not_make_an_hour(db):
    """Две случайные сдачи в окне чужого часа — ещё не «был на занятии»."""
    student_id, course_id = await _student_with_pace(db)
    other_id, _ = await _new_user(db, name="other")
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    await _lesson_with_status(
        db, student_id=student_id, teacher_id=teacher_id, days_ago=3, status="no_show",
    )
    foreign_at = datetime.now(UTC) - timedelta(days=2)
    await _create_occurrence(
        db, student_id=other_id, teacher_id=teacher_id, scheduled_at=foreign_at,
    )
    await _work_in_window(
        db, student_id=student_id, course_id=course_id, at=foreign_at,
        n=homework_volume_service.LESSON_PRESENCE_MIN_ITEMS - 1,
    )

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.missed_unpaid == 1


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


# ============== Сдвоенный час: ДЗ после последней пары ==============


@pytest.mark.asyncio
async def test_no_homework_between_two_lessons_in_a_row(db, monkeypatch):
    """Сдвоенный час: после первой пары ДЗ не выдаём.

    Вопрос оператора 02.09. Срок берётся по следующему занятию ученика — а у
    сдвоенного часа следующее начинается сразу, через перемену. Ученик получил
    бы домашнюю работу со сроком «через пять минут», сидя на второй паре, и
    выполнить её не мог бы физически. На проде сдвоенные часы у 22 учеников,
    52 пары.
    """
    from app.core import settings_store

    student_id, _ = await _student_with_program(db, materials=0, tasks=12)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    first_at = datetime.now(UTC) - timedelta(minutes=61)
    first_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=first_at,
    )
    # Вторая пара начинается сразу за первой.
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=first_at + timedelta(minutes=60),
    )

    result = await homework_service.auto_issue_after_lesson(
        db, student_id=student_id, occurrence_id=first_id, occurrence_at=first_at,
    )
    await db.commit()
    assert result is None, "ДЗ выдали между парами сдвоенного часа"
    assert await homework_service.get_current(db, student_id=student_id) is None


@pytest.mark.asyncio
async def test_homework_after_the_last_lesson_of_the_block(db, monkeypatch):
    """После ВТОРОЙ пары ДЗ выдаётся, и срок — не через пять минут."""
    from app.core import settings_store

    student_id, _ = await _student_with_program(db, materials=0, tasks=12)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)

    first_at = datetime.now(UTC) - timedelta(minutes=121)
    second_at = first_at + timedelta(minutes=60)
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=first_at,
    )
    second_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=second_at,
    )
    # Следующий учебный день.
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=second_at + timedelta(days=3),
    )

    result = await homework_service.auto_issue_after_lesson(
        db, student_id=student_id, occurrence_id=second_id, occurrence_at=second_at,
    )
    await db.commit()
    assert result is not None
    assert result["due_at"] >= datetime.now(UTC) + timedelta(days=2), (
        "срок ДЗ пришёлся на соседнюю пару"
    )


@pytest.mark.asyncio
async def test_due_date_skips_the_paired_lesson(db):
    """Срок ручной выдачи тоже перепрыгивает сдвоенную пару.

    Преподаватель может задать ДЗ между парами — из карточки ученика. Срок
    «через пять минут» ошибочен независимо от того, кто его поставил.
    """
    student_id, _ = await _student_with_program(db, materials=0, tasks=8)
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")

    now = datetime.now(UTC)
    # Идущая сейчас пара и сразу за ней вторая.
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(minutes=30),
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(minutes=30),
    )
    far = now + timedelta(days=4)
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=far,
    )

    due = await homework_service.next_due_for(
        db, student_id=student_id, after=now - timedelta(minutes=30), now=now,
    )
    assert due == far, "срок пришёлся на вторую пару того же блока"


# ========== Персональная норма из остатка программы (tsk-797) ==========


async def _program_student(db, *, done_tasks: int = 0, total_tasks: int = 100,
                           grade: int = 11, monkeypatch=None, kind: str = "ege"):
    """Ученик, записанный на курс программы подготовки, с заданным прогрессом."""
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, f"program-{kind}")
    await _enroll(db, student_id=student_id, course_id=course_id)
    now = datetime.now(UTC)
    for pos in range(1, total_tasks + 1):
        task_id = await _new_task(db, course_id=course_id, order_position=pos)
        if pos <= done_tasks:
            await _submit(
                db, student_id=student_id, task_id=task_id, course_id=course_id,
                is_correct=True, at=now - timedelta(days=40),
            )
    await _set_grade(db, student_id=student_id, grade=grade)
    return student_id, course_id


def _settings_with_program(course_id: int, kind: str = "ege"):
    """Подмена настроек: этот курс — программа подготовки."""
    values = {
        "homework_program_ege_courses": str(course_id) if kind == "ege" else "",
        "homework_program_oge_courses": str(course_id) if kind == "oge" else "",
        "homework_program_ege_deadline": "03-31",
        "homework_program_oge_deadline": "04-30",
        "homework_program_early_finish": "05-31",
        "homework_program_summer_finish": "08-31",
    }
    return lambda key: values.get(key, "")


@pytest.mark.asyncio
async def test_homework_takes_only_the_program_courses(db, monkeypatch):
    """Домой идут ТОЛЬКО курсы программы подготовки (tsk-869).

    Требование оператора 09.09. Раньше обход шёл по всем записям
    `user_courses`, и в домашнюю работу попадало что угодно из соседних
    курсов — на проде так уходили «Собираем Бот-Угадайку» и «Знакомство с
    SQLite». К экзамену это не готовит, а место в недельном объёме занимает.
    """
    from app.core import settings_store

    student_id, program_course = await _program_student(
        db, done_tasks=0, total_tasks=5,
    )
    # Соседний курс — не из программы, но записан раньше по порядку.
    other = await _new_course(db, "не-из-программы")
    await _enroll(db, student_id=student_id, course_id=other)
    await db.execute(
        text(
            "UPDATE user_courses SET order_number = 0 "
            " WHERE user_id = :u AND course_id = :c"
        ),
        {"u": student_id, "c": other},
    )
    outsider = await _new_task(db, course_id=other, order_position=1)
    await db.commit()

    monkeypatch.setattr(
        settings_store, "get_str", _settings_with_program(program_course)
    )

    picked = await homework_service._next_items(db, student_id=student_id, limit=10)

    assert outsider not in [i["item_id"] for i in picked], (
        "домой ушло задание из курса вне программы подготовки"
    )
    assert picked, "программные задания тоже пропали — обход сломан"


@pytest.mark.asyncio
async def test_student_outside_programs_still_gets_all_his_courses(db, monkeypatch):
    """Ученик вне программ подготовки берёт домой все свои курсы, как раньше.

    Для него «программа» — и есть его курсы, других ориентиров нет; пустая
    выдача была бы регрессией для всех, кто не готовится к экзамену.
    """
    from app.core import settings_store

    student_id, course_id = await _student_with_program(db, materials=1, tasks=3)
    monkeypatch.setattr(settings_store, "get_str", lambda key: "")

    picked = await homework_service._next_items(db, student_id=student_id, limit=5)

    assert picked, "ученик вне программ остался без домашней работы"


@pytest.mark.asyncio
async def test_student_outside_programs_gets_the_course_he_works_on_now(db, monkeypatch):
    """Вне программы домой идёт курс, где ученик работал ПОСЛЕДНИМ (tsk-913).

    Правило оператора 12.09: «ДЗ должно быть по курсу, над которым сейчас
    работает ученик». Курунов летом проходил чат-ботов и бросил, осенью
    занялся ЕГЭ — а домой уходили хвосты чат-ботов, потому что они стояли
    первыми по порядку записи.
    """
    from app.core import settings_store

    student_id, summer = await _student_with_program(db, materials=0, tasks=3)
    autumn = await _new_course(db, "осенний")
    await _enroll(db, student_id=student_id, course_id=autumn)
    # Летний курс записан раньше — по порядку записи он первый.
    await db.execute(
        text(
            "UPDATE user_courses SET order_number = CASE course_id WHEN :s THEN 1 ELSE 2 END "
            " WHERE user_id = :u"
        ),
        {"u": student_id, "s": summer},
    )
    autumn_tasks = [
        await _new_task(db, course_id=autumn, order_position=i) for i in range(1, 4)
    ]
    await db.commit()
    now = datetime.now(UTC)
    # Летом решал в летнем, вчера — в осеннем.
    summer_task = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c ORDER BY order_position LIMIT 1"),
            {"c": summer},
        )
    ).scalar()
    await _submit(
        db, student_id=student_id, task_id=summer_task, course_id=summer,
        is_correct=True, at=now - timedelta(days=60),
    )
    await _submit(
        db, student_id=student_id, task_id=autumn_tasks[0], course_id=autumn,
        is_correct=True, at=now - timedelta(days=1),
    )
    monkeypatch.setattr(settings_store, "get_str", lambda key: "")

    picked = await homework_service._next_items(db, student_id=student_id, limit=2)

    assert [i["item_id"] for i in picked] == autumn_tasks[1:3], (
        "домой ушёл летний курс, а не тот, где ученик работает сейчас"
    )


@pytest.mark.asyncio
async def test_content_added_after_the_topic_was_passed_is_not_homework(db):
    """Задание, досыпанное в пройденную тему, домой не задаётся (tsk-838).

    Находка оператора 08.09 на живом ученике: курс «Первая программа на Python»
    пройден 21 июля, 7 сентября в него добавили два задания — и 8 сентября они
    пришли ученице домой как долг по давно закрытой теме.

    Правило tsk-692 такое содержимое ПРОЩАЕТ: движок его не предлагает и не
    считает в прогрессе. Выдача про правило не знала — теперь знает, иначе
    система требует то, что сама же считает необязательным.
    """
    student_id, course_id = await _student_with_program(db, materials=0, tasks=3)
    now = datetime.now(UTC)

    old_tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c ORDER BY order_position"),
            {"c": course_id},
        )
    ).scalars().all()
    for task_id in old_tasks:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=45),
        )
    await db.commit()

    # Тема пройдена — и только теперь в курс добавляют новое задание.
    fresh = await _new_task(db, course_id=course_id, order_position=99, fresh=True)
    await db.commit()

    # Сначала убеждаемся, что правило вообще сработало на этих данных — иначе
    # проверка ниже проходила бы просто потому, что прощать было нечего.
    from app.services.content_grace_service import compute_graced_items

    graced = await compute_graced_items(db, student_id, course_id)
    assert fresh in graced.tasks, "правило tsk-692 не сработало — тест ничего не ловит"

    picked = await homework_service._next_items(
        db, student_id=student_id, limit=5,
    )

    assert fresh not in [i["item_id"] for i in picked], (
        "домой ушло то, что система сама считает для этого ученика необязательным"
    )


@pytest.mark.asyncio
async def test_forgiven_content_is_not_in_the_remaining_program(db, monkeypatch):
    """Досыпанное в пройденную тему не идёт в остаток, из которого считается
    норма (tsk-912).

    Разбор Литовкина 12.09: курс Python пройден целиком, но в остатке сидело
    21 задание, добавленное в закрытые темы, — норма требовала за них
    ≈ 4%, а задать их выдача не могла (она правило tsk-692 соблюдает).
    Одна величина, три потребителя: остаток всех курсов, остаток программы
    подготовки и подрезка программы — вычет один и тот же.
    """
    from app.core import settings_store
    from app.services.content_grace_service import compute_graced_items, grace_cache

    student_id, course_id = await _student_with_program(db, materials=0, tasks=3)
    now = datetime.now(UTC)
    old_tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c ORDER BY order_position"),
            {"c": course_id},
        )
    ).scalars().all()
    for task_id in old_tasks:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=45),
        )
    await db.commit()
    monkeypatch.setattr(settings_store, "get_str", lambda key: "")

    before = await homework_volume_service.compute(db, student_id=student_id)
    assert before.remaining_items == 0, "курс пройден целиком — остатка быть не должно"

    # Тема пройдена — досыпаем два задания.
    fresh = [
        await _new_task(db, course_id=course_id, order_position=p, fresh=True)
        for p in (98, 99)
    ]
    await db.commit()
    # Кеш правила живёт в сессии (tsk-662): первый расчёт выше его заполнил,
    # а в бою добавление заданий и расчёт нормы — разные запросы.
    grace_cache(db).clear()
    graced = await compute_graced_items(db, student_id, course_id)
    assert set(fresh) <= set(graced.tasks), "правило tsk-692 не сработало — тест ничего не ловит"

    after = await homework_volume_service.compute(db, student_id=student_id)
    assert after.remaining_items == 0, (
        "остаток вырос на прощённые задания — норму считают за то, что задать нельзя"
    )
    assert after.remaining_minutes in (None, 0)


@pytest.mark.asyncio
async def test_items_carry_what_a_link_needs(db, monkeypatch):
    """Состав выдачи несёт коды для ссылки на сам элемент (tsk-838).

    Замечание оператора 08.09: пункты списка ДЗ не открывались нажатием — до
    задания приходилось добираться через курс, вспоминая, где оно лежит. Адрес
    урока строится из `course_uid` узла и `external_uid` задания, числовых id
    для него мало.
    """
    student_id, course_id = await _student_with_program(db, materials=1, tasks=1)
    await db.execute(
        text("UPDATE courses SET course_uid = :u WHERE id = :c"),
        {"u": f"{_TAG}-uid-{course_id}", "c": course_id},
    )
    await db.commit()

    # Объём задаём явно: до срока два дня, и обычный расчёт взял бы один
    # элемент — а нам нужны оба вида, у них разные поля ссылки.
    homework = await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=2),
        source="teacher", volume_override=2,
    )
    await db.commit()

    by_kind = {i["kind"]: i for i in homework["items"]}
    assert by_kind["material"]["course_uid"] == f"{_TAG}-uid-{course_id}"
    assert by_kind["material"]["external_uid"] is None, "у материала внешнего кода нет"
    assert by_kind["task"]["course_uid"] == f"{_TAG}-uid-{course_id}"
    assert by_kind["task"]["external_uid"], "без внешнего кода задание не открыть"


@pytest.mark.asyncio
async def test_newcomer_pace_is_measured_over_weeks_he_has_been_here(db, monkeypatch):
    """Темп новичка считается по его неделям, а не по трём (tsk-798, 07.09).

    На проде ученица решила 60 заданий за одно занятие, а в сводке стояло
    «делает 0»: медиана трёх недель у человека, занимающегося три дня, — это
    медиана [0, 0, 60]. По нулевому темпу ей и выдача считалась как совсем
    неработающей.
    """
    from app.core import settings_store

    student_id, course_id = await _program_student(db, done_tasks=0, total_tasks=200)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    now = _PACE_NOW
    tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c LIMIT 30"), {"c": course_id}
        )
    ).scalars().all()
    for task_id in tasks:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=1),
        )

    plan = await homework_volume_service.compute(db, student_id=student_id, now=now)

    assert plan.fact_weeks_used == 1, "окно шире, чем ученик вообще занимается"
    assert plan.fact_per_week == 30, "работа новичка потеряна медианой пустых недель"


@pytest.mark.asyncio
async def test_lesson_work_counts_and_is_shown_separately(db, monkeypatch):
    """Работа на занятии входит в темп и видна отдельной долей.

    Требование оператора 07.09: «делает N» без разбивки не читается —
    непонятно, работает человек сам или только под присмотром преподавателя.
    """
    from app.core import settings_store

    student_id, course_id = await _program_student(db, done_tasks=0, total_tasks=60)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    now = _PACE_NOW
    lesson_at = now - timedelta(days=1)
    teacher_id, _ = await _new_user(db, role="teacher", name="teacher")
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=lesson_at,
    )

    tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c ORDER BY id LIMIT 10"),
            {"c": course_id},
        )
    ).scalars().all()
    # Шесть заданий — прямо на занятии, четыре — дома вечером.
    for i, task_id in enumerate(tasks):
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True,
            at=lesson_at + timedelta(minutes=10 * (i + 1)) if i < 6
            else lesson_at + timedelta(hours=5),
        )

    plan = await homework_volume_service.compute(db, student_id=student_id, now=now)

    assert plan.fact_per_week == 10, "работа на занятии обязана входить в темп"
    assert plan.lesson_share == 0.6


@pytest.mark.parametrize("shift", range(7))
@pytest.mark.asyncio
async def test_pace_does_not_depend_on_weekday(db, monkeypatch, shift):
    """Тот же ученик и та же работа дают один темп в любой день недели.

    До tsk-819 окно темпа резалось календарными неделями от `since`, и
    последнее окно кончалось прошлым воскресеньем: работа текущей недели не
    попадала никуда. Темп выходил ненулевым ровно по понедельникам — замер
    08.09.2026 давал пн 10.0, вт-вс 0.0. Это и был прод-симптом tsk-798:
    «делает 0» у ученицы, решившей 60 заданий за занятие.

    Тест гоняет один и тот же сценарий по всем семи дням недели: расхождение
    между днями означает, что окно снова привязали к календарю.
    """
    from app.core import settings_store

    student_id, course_id = await _program_student(db, done_tasks=0, total_tasks=60)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    now = _PACE_NOW + timedelta(days=shift)
    tasks = (
        await db.execute(
            text("SELECT id FROM tasks WHERE course_id = :c ORDER BY id LIMIT 10"),
            {"c": course_id},
        )
    ).scalars().all()
    for task_id in tasks:
        await _submit(
            db, student_id=student_id, task_id=int(task_id), course_id=course_id,
            is_correct=True, at=now - timedelta(days=1),
        )

    plan = await homework_volume_service.compute(db, student_id=student_id, now=now)

    assert plan.fact_per_week == 10, (
        f"вчерашняя работа потеряна, если «сегодня» — {now:%a}"
    )


@pytest.mark.asyncio
async def test_non_graduate_sees_what_finishing_early_would_take(db, monkeypatch):
    """Десятикласснику показываются два альтернативных срока.

    Требование оператора 07.09: у него есть выбор, которого нет у выпускника —
    закончить за учебный год или прихватить лето, отдав выпускной год
    вариантам. Одна цифра «до марта через год» этот выбор прячет.
    """
    from app.core import settings_store

    student_id, course_id = await _program_student(
        db, done_tasks=0, total_tasks=200, grade=10,
    )
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    plan = await homework_volume_service.compute(
        db, student_id=student_id, now=_PACE_NOW,
    )

    assert plan.early_target_per_week is not None
    assert plan.summer_target_per_week is not None
    assert plan.early_target_per_week > plan.summer_target_per_week > plan.target_per_week
    assert plan.early_deadline is not None and plan.summer_deadline is not None
    assert plan.early_deadline < plan.summer_deadline


@pytest.mark.asyncio
async def test_graduate_has_no_alternative_deadlines(db, monkeypatch):
    """Выпускнику альтернативы не показываем: у него срок один."""
    from app.core import settings_store

    student_id, course_id = await _program_student(
        db, done_tasks=0, total_tasks=200, grade=11,
    )
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    plan = await homework_volume_service.compute(db, student_id=student_id)

    assert plan.early_target_per_week is None
    assert plan.summer_target_per_week is None


@pytest.mark.asyncio
async def test_norm_is_personal_not_per_grade(db, monkeypatch):
    """Два одиннадцатиклассника с разным прогрессом получают РАЗНУЮ норму.

    Замечание оператора 04.09: раньше всем 11 классам показывалось 20 в неделю
    независимо от того, прошёл человек половину курса или не начинал.
    """
    from app.core import settings_store

    ahead_id, course_id = await _program_student(db, done_tasks=80, total_tasks=100)
    behind_id, _ = await _new_user(db)
    await _enroll(db, student_id=behind_id, course_id=course_id)
    await _set_grade(db, student_id=behind_id, grade=11)

    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    ahead = await homework_volume_service.compute(db, student_id=ahead_id)
    behind = await homework_volume_service.compute(db, student_id=behind_id)

    assert ahead.program_kind == "ege" and behind.program_kind == "ege"
    assert ahead.program_tasks_remaining == 20
    assert behind.program_tasks_remaining == 100
    assert behind.target_per_week > ahead.target_per_week, (
        "норма не зависит от личного прогресса"
    )


@pytest.mark.asyncio
async def test_norm_counts_only_required_items(db, monkeypatch):
    """Необязательные задания в остаток программы не входят.

    Прямое требование оператора: «только обязательные, опциональные считать не
    нужно».
    """
    from app.core import settings_store

    student_id, course_id = await _program_student(db, done_tasks=0, total_tasks=20)
    extra = [await _new_task(db, course_id=course_id, order_position=500 + i) for i in range(30)]
    await db.execute(
        text("UPDATE tasks SET requirement_level = 'recommended' WHERE id = ANY(:ids)"),
        {"ids": extra},
    )
    await db.commit()
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.program_tasks_remaining == 20, "рекомендованные попали в программу"


@pytest.mark.asyncio
async def test_tenth_grade_has_a_year_more_and_a_softer_norm(db, monkeypatch):
    """Срок — 31 марта года ИХ экзамена (решение оператора 04.09).

    У десятиклассника до его марта на год больше, значит и норма мягче при том
    же остатке.
    """
    from app.core import settings_store

    eleventh_id, course_id = await _program_student(db, done_tasks=0, total_tasks=200)
    tenth_id, _ = await _new_user(db)
    await _enroll(db, student_id=tenth_id, course_id=course_id)
    await _set_grade(db, student_id=tenth_id, grade=10)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    eleventh = await homework_volume_service.compute(db, student_id=eleventh_id)
    tenth = await homework_volume_service.compute(db, student_id=tenth_id)

    assert eleventh.program_deadline.year + 1 == tenth.program_deadline.year
    assert tenth.target_per_week < eleventh.target_per_week


@pytest.mark.asyncio
async def test_unreachable_norm_is_shown_honestly(db, monkeypatch):
    """Норма может быть больше потолка выдачи — и показывается как есть.

    Решение оператора 04.09: преподаватель видит «нужно 41, делает 9, задаём
    11» — три разных числа. Прятать разрыв нельзя: на проде 49 учеников из 70
    нуждаются в 30+ элементов в неделю.
    """
    from app.core import settings_store

    student_id, course_id = await _program_student(db, done_tasks=0, total_tasks=900)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(course_id))

    plan = await homework_volume_service.compute(
        db, student_id=student_id, now=_PACE_NOW,
    )
    assert plan.target_per_week > homework_volume_service.MAX_PER_WEEK
    # Выдача при этом остаётся посильной.
    assert plan.volume_per_week <= homework_volume_service.MAX_PER_WEEK
    assert plan.pace_gap == plan.target_per_week  # темпа нет вовсе
    # …и разрыв назван причиной, а не оставлен голым числом: «нужно 48» без
    # пояснения читается как упрёк ученику, хотя это про программу и срок.
    assert plan.target_unreachable is True


@pytest.mark.asyncio
async def test_oge_program_wins_over_ege(db, monkeypatch):
    """Девятикласснику могли открыть материалы ЕГЭ — сдаёт он ОГЭ.

    Программа определяется по фактической записи на курсы, а не по классу:
    класс ученик указывает сам, и у 59 из 82 он пустой.
    """
    from app.core import settings_store

    student_id, oge_course = await _program_student(
        db, done_tasks=0, total_tasks=30, grade=9, kind="oge",
    )
    ege_course = await _new_course(db, "ege-extra")
    await _enroll(db, student_id=student_id, course_id=ege_course)
    for pos in range(1, 40):
        await _new_task(db, course_id=ege_course, order_position=pos)

    values = {
        "homework_program_ege_courses": str(ege_course),
        "homework_program_oge_courses": str(oge_course),
        "homework_program_ege_deadline": "03-31",
        "homework_program_oge_deadline": "04-30",
    }
    monkeypatch.setattr(settings_store, "get_str", lambda key: values.get(key, ""))

    plan = await homework_volume_service.compute(db, student_id=student_id)
    assert plan.program_kind == "oge"
    assert plan.program_deadline.month == 4 and plan.program_deadline.day == 30
    assert plan.program_tasks_remaining == 30, "в программу попал курс ЕГЭ"


@pytest.mark.asyncio
async def test_without_program_falls_back_to_grade_norm(db, monkeypatch):
    """Ученик вне программ подготовки считается по классу, как раньше."""
    from app.core import settings_store

    student_id, _ = await _student_with_program(db, materials=0, tasks=40)
    monkeypatch.setattr(settings_store, "get_str", lambda key: "")

    plan = await homework_volume_service.compute(
        db, student_id=student_id, now=_PACE_NOW,
    )
    assert plan.program_kind is None
    assert plan.target_per_week == 20  # норма 11 класса
