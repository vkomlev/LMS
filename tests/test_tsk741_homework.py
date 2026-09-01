"""tsk-741 фаза 3: домашняя работа — норма, выдача, состав, выполнение.

Проверяется то, из-за чего механика может тихо врать:

- **срок экзамена от класса** — 11 класс сдаёт этим летом, 10 — следующим,
  класс не указан считается как 11 (решение оператора 01.09);
- **норма** — не выше того, что человек тянет (`факт × 1.2`), не ниже пола, не
  выше потолка; поправка на качество при доле верных ниже 60%;
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


@pytest.mark.asyncio
async def test_volume_does_not_exceed_ceiling(db):
    """Даже когда программы много, а срока мало, норма упирается в потолок."""
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
    assert plan.volume_per_week == homework_volume_service.MAX_PER_WEEK


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
        remaining_items=100, need_per_week=3.0, fact_per_week=10.0, correct_ratio=0.9,
        quality_penalty_applied=False, volume_per_week=14, weeks_behind=0,
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
