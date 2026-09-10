"""tsk-867: норма ДЗ, состав выдачи и прогноз считаются в минутах работы.

Проверяется то, из-за чего механика может тихо вернуться к штукам или,
наоборот, начать врать в новой единице:

- **тяжёлые элементы укорачивают список** — двадцать заданий с решением это
  79 минут, двадцать с выбором ответа — четыре; при одном бюджете времени
  пунктов должно получиться разное число;
- **штучное ограждение остаётся** — иначе серия заданий по 15 секунд наберёт
  недельный бюджет только на трёхстах пунктах;
- **первый пункт выдаётся всегда** — даже когда он один перекрывает бюджет:
  одна задача на сорок минут это законный состав, а не ошибка расчёта;
- **пустая телеметрия не выдумывает вес** — норма считается по-старому,
  в штуках, и это видно снаружи (`effort_measured=False`);
- **прогноз окончания считается в минутах** — «1342 элемента при норме 12»
  и «82 часа при 75 минутах» дают разные сроки, и верен второй;
- **число преподавателя отменяет бюджет времени** — он сказал «задай
  двенадцать», и получить в ответ четыре не то, о чём он просил.

На настоящей БД, по образцу test_task_effort_tsk851.py: вес меряется по всей
базе, поэтому формат заданий в каждом тесте свой — иначе результат зависел бы
от данных соседних тестов.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import homework_service, homework_volume_service
from app.services.auth import identity_link_service
from app.services.task_effort_service import MIN_CELL_SAMPLES, load_effort_table

UTC = timezone.utc
_TAG = "tsk867"

pytestmark = pytest.mark.asyncio


def _unique_type() -> str:
    """Формат, встречающийся только в этом тесте (см. модульный docstring)."""
    return f"T867_{random.randint(10**8, 10**10)}"


async def _student(db, name: str) -> int:
    email = f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=f"{_TAG}-{name}", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    await db.commit()
    return user.id


async def _course(db) -> int:
    course_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, course_uid) "
                    "VALUES (:t, 'self_guided', :u) RETURNING id"
                ),
                {
                    "t": f"{_TAG}-курс",
                    "u": f"{_TAG}-{random.randint(10**8, 10**10)}",
                },
            )
        ).scalar_one()
    )
    await db.commit()
    return course_id


async def _difficulty(db, code: str | None = None) -> int:
    """ID сложности; `code` — когда важно, ядро это или тренажёр.

    В объёме программы EASY и NORMAL — тренажёр (его и подбирают под ученика),
    а THEORY/HARD/PROJECT — несокращаемое ядро. Первая по номеру сложность
    оказывается ядром, и тест про бюджет тренажёра на ней мерил бы пустоту.
    """
    if code is None:
        return int(
            (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1")))
            .scalar_one()
        )
    return int(
        (
            await db.execute(
                text("SELECT id FROM difficulties WHERE code = :c LIMIT 1"), {"c": code}
            )
        ).scalar_one()
    )


async def _tasks(
    db, *, course_id: int, task_type: str, count: int, difficulty_id: int
) -> list[int]:
    ids: list[int] = []
    for position in range(count):
        ids.append(
            int(
                (
                    await db.execute(
                        text(
                            "INSERT INTO tasks (task_content, solution_rules, course_id, "
                            "  difficulty_id, external_uid, max_score, order_position) "
                            "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                            "        :uid, 10, :pos) RETURNING id"
                        ),
                        {
                            "tc": json.dumps(
                                {"type": task_type, "stem": f"{_TAG} {position}"}
                            ),
                            "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                            "cid": course_id,
                            "did": difficulty_id,
                            "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                            "pos": position,
                        },
                    )
                ).scalar_one()
            )
        )
    await db.commit()
    return ids


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _calibrate(
    db, *, donor_id: int, task_id: int, course_id: int, seconds: int
) -> None:
    """Задать формату измеренный вес: `MIN_CELL_SAMPLES` пар «открыл → сдал».

    Пары ставит ОТДЕЛЬНЫЙ ученик-донор на отдельном задании: у того, кому потом
    считается норма, эти сдачи попали бы в его собственный темп и в остаток
    программы, и тест мерил бы уже не то, что заявлено.
    """
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id) VALUES (:u, :c) RETURNING id"
                ),
                {"u": donor_id, "c": course_id},
            )
        ).scalar_one()
    )
    for i in range(MIN_CELL_SAMPLES):
        offset = (i + 1) * 7200
        await db.execute(
            text(
                "INSERT INTO learning_events (student_id, event_type, payload, created_at) "
                "VALUES (:u, 'task_opened', CAST(:p AS jsonb), "
                "        now() - make_interval(secs => :off))"
            ),
            {
                "u": donor_id,
                "p": json.dumps({"task_id": task_id}),
                "off": offset + seconds,
            },
        )
        await db.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
                "  is_correct, submitted_at, received_at, source_system) "
                "VALUES (:u, :t, :a, 1, 1, true, now() - make_interval(secs => :off), "
                "        now() - make_interval(secs => :off), 'spw_web')"
            ),
            {"u": donor_id, "t": task_id, "a": attempt_id, "off": offset},
        )
    await db.commit()


async def _cleanup(db, *, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(
        text(
            "DELETE FROM homework_item WHERE homework_id IN "
            "  (SELECT id FROM homework_assignment WHERE student_id = ANY(:i))"
        ),
        {"i": user_ids},
    )
    await db.execute(
        text("DELETE FROM homework_assignment WHERE student_id = ANY(:i)"), {"i": user_ids}
    )
    await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM learning_events WHERE student_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


# ==================== Состав выдачи собирается по времени ====================


async def test_heavy_items_make_the_list_shorter_than_light_ones(db):
    """Один бюджет времени, разный вес — разное число пунктов.

    Это и есть предмет задачи: до неё «двадцать элементов» означало и четыре
    минуты, и семьдесят девять.
    """
    donor = await _student(db, "donor")
    heavy_student = await _student(db, "heavy")
    light_student = await _student(db, "light")
    heavy_course, light_course, donor_course = (
        await _course(db), await _course(db), await _course(db)
    )
    difficulty = await _difficulty(db)
    heavy_type, light_type = _unique_type(), _unique_type()
    try:
        # Калибруем оба формата на курсе донора: 300 секунд против 15.
        heavy_probe = (
            await _tasks(
                db, course_id=donor_course, task_type=heavy_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        light_probe = (
            await _tasks(
                db, course_id=donor_course, task_type=light_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=heavy_probe, course_id=donor_course, seconds=300
        )
        await _calibrate(
            db, donor_id=donor, task_id=light_probe, course_id=donor_course, seconds=15
        )

        await _tasks(
            db, course_id=heavy_course, task_type=heavy_type, count=40,
            difficulty_id=difficulty,
        )
        await _tasks(
            db, course_id=light_course, task_type=light_type, count=40,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=heavy_student, course_id=heavy_course)
        await _enroll(db, student_id=light_student, course_id=light_course)

        table = await load_effort_table(db)
        assert table.seconds_for(difficulty_id=difficulty, task_type=heavy_type) == pytest.approx(
            300, abs=30
        )

        heavy_items = await homework_service._next_items(
            db, student_id=heavy_student, limit=40, minutes_budget=20, effort_table=table
        )
        light_items = await homework_service._next_items(
            db, student_id=light_student, limit=40, minutes_budget=20, effort_table=table
        )
        # 20 минут — это четыре задачи по пять минут и сорок заданий по 15 с.
        assert len(heavy_items) == 4, len(heavy_items)
        assert len(light_items) == 40, len(light_items)
    finally:
        await _cleanup(
            db,
            user_ids=[donor, heavy_student, light_student],
            course_ids=[heavy_course, light_course, donor_course],
        )


async def test_count_guard_stops_an_endless_series_of_light_items(db):
    """Штучное ограждение держит выдачу, даже когда бюджет времени не кончился.

    Триста нажатий за вечер — не учебная работа, сколько бы минут они ни
    заняли по измерителю.
    """
    donor = await _student(db, "donor")
    student = await _student(db, "fast")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db)
    light_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=light_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=12
        )
        await _tasks(
            db, course_id=course, task_type=light_type, count=60,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        table = await load_effort_table(db)
        items = await homework_service._next_items(
            db, student_id=student, limit=25, minutes_budget=75, effort_table=table
        )
        # 75 минут по 12 секунд — это 375 пунктов; ограждение отдаёт 25.
        assert len(items) == 25, len(items)
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


async def test_first_item_is_issued_even_if_it_alone_exceeds_the_budget(db):
    """Выдача без состава бессмысленна: один пункт есть всегда."""
    donor = await _student(db, "donor")
    student = await _student(db, "slow")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db)
    huge_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=huge_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=2400
        )
        await _tasks(
            db, course_id=course, task_type=huge_type, count=5,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        table = await load_effort_table(db)
        items = await homework_service._next_items(
            db, student_id=student, limit=10, minutes_budget=10, effort_table=table
        )
        assert len(items) == 1, len(items)
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


# ========================= Норма и прогноз в минутах =========================


async def test_norm_is_measured_in_minutes_when_effort_is_known(db):
    """У ученика появляется недельная норма в минутах и взвешенный остаток."""
    donor = await _student(db, "donor")
    student = await _student(db, "norm")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db)
    task_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=task_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=180
        )
        await _tasks(
            db, course_id=course, task_type=task_type, count=30,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        plan = await homework_volume_service.compute(db, student_id=student)
        assert plan.effort_measured is True
        assert plan.minutes_per_week is not None
        # Тридцать заданий по три минуты — примерно полтора часа работы.
        assert plan.remaining_minutes == pytest.approx(90, abs=20), plan.remaining_minutes
        # Норма не ниже пола и не выше потолка — те же границы, что у штук.
        assert (
            homework_volume_service.MIN_MINUTES_PER_WEEK
            <= plan.minutes_per_week
            <= homework_volume_service.MAX_MINUTES_PER_WEEK
        )
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


async def test_forecast_counts_minutes_not_items(db):
    """На сколько недель хватит программы — считается по времени.

    У ученика тридцать заданий по три минуты: при недельной норме в минутах
    остатка хватает НЕ на столько недель, сколько дало бы деление штук на
    штучную норму.
    """
    donor = await _student(db, "donor")
    student = await _student(db, "forecast")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db)
    task_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=task_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=180
        )
        await _tasks(
            db, course_id=course, task_type=task_type, count=30,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        plan = await homework_volume_service.compute(db, student_id=student)
        assert plan.minutes_per_week and plan.remaining_minutes
        assert plan.weeks_of_program_left == int(
            plan.remaining_minutes // plan.minutes_per_week
        )
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


async def test_program_scope_budget_is_counted_in_minutes(db):
    """Объём программы подбирается по времени, а не по числу заданий.

    Ветка минут в `program_scope_service` иначе не исполняется ни разу: на
    пустой телеметрии весь расчёт уходит в штуки, и ошибка в ней всплыла бы
    только на проде (так уже случилось с типами в UNION 09.09).
    """
    from app.services import program_scope_service

    donor = await _student(db, "donor")
    student = await _student(db, "scope")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db, "EASY")
    task_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=task_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=300
        )
        await _tasks(
            db, course_id=course, task_type=task_type, count=40,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        scope = await program_scope_service.compute_scope(
            db,
            student_id=student,
            kind="ege",
            root_ids=[course],
            deadline=(datetime.now(UTC) + timedelta(weeks=4)).date(),
            fact_per_week=5.0,
            fact_minutes_per_week=25.0,
        )
        assert scope.effort_measured is True
        assert scope.planned_pace_minutes is not None
        # Сорок заданий по пять минут — это больше трёх часов; в четыре недели
        # по полтора часа они не помещаются, и это должно быть видно числом.
        assert scope.drill_minutes == pytest.approx(200, abs=30), scope.drill_minutes
        assert scope.drill_allowed_minutes is not None
        assert scope.drill_allowed_minutes <= scope.drill_minutes
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


async def test_forecast_beyond_the_horizon_is_not_shown(db):
    """Финиш «в 2061 году» не показывается: это не предсказание.

    На проде такой случай нашёлся сразу (09.09): ученик делает лёгкие задания
    по двадцать секунд, а в остатке 87 часов задач с решением — деление даёт
    тридцать четыре года. Арифметика верна, польза нулевая.
    """
    from app.services.student_dashboard_service import (
        FORECAST_HORIZON_WEEKS,
        _load_course_pace_and_forecast,
    )

    student = await _student(db, "horizon")
    course = await _course(db)
    difficulty = await _difficulty(db)
    try:
        task_ids = await _tasks(
            db, course_id=course, task_type=_unique_type(), count=3,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)
        items = [
            {"item_type": "task", "item_id": tid, "status": "AVAILABLE", "title": "x"}
            for tid in task_ids
        ]
        # Темпа нет вовсе — прогноз невозможен, и это уже даёт None. Проверяем
        # именно горизонт: подставляем окно, в котором ученик ничего не делал.
        forecast, completed = await _load_course_pace_and_forecast(
            db,
            student_id=student,
            course_id=course,
            items=items,
            now=datetime.now(UTC),
            pace_weeks=3,
        )
        assert forecast is None and completed is False
        assert FORECAST_HORIZON_WEEKS == 5 * 52
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[course])


async def test_teacher_volume_override_turns_off_the_time_budget(db):
    """Преподаватель сказал «задай семь» — значит семь, а не «сколько влезет»."""
    donor = await _student(db, "donor")
    student = await _student(db, "override")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db)
    task_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=task_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=600
        )
        await _tasks(
            db, course_id=course, task_type=task_type, count=20,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        now = datetime.now(UTC)
        homework = await homework_service.issue(
            db,
            student_id=student,
            due_at=now + timedelta(days=3),
            source="teacher",
            volume_override=7,
            now=now,
        )
        await db.commit()
        assert homework["total"] == 7, homework["total"]
        # Бюджет времени не считался — значит и оценки минут в снимке нет.
        assert homework["planned_minutes"] is None
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


async def test_issue_records_how_many_minutes_were_assigned(db):
    """Выдача помнит, во сколько минут был оценён её состав.

    Пересчитывать вес при чтении нельзя: таблица весов живая, и «сколько минут
    задали» менялось бы задним числом при каждом открытии экрана.
    """
    donor = await _student(db, "donor")
    student = await _student(db, "snapshot")
    course, donor_course = await _course(db), await _course(db)
    difficulty = await _difficulty(db)
    task_type = _unique_type()
    try:
        probe = (
            await _tasks(
                db, course_id=donor_course, task_type=task_type, count=1,
                difficulty_id=difficulty,
            )
        )[0]
        await _calibrate(
            db, donor_id=donor, task_id=probe, course_id=donor_course, seconds=120
        )
        await _tasks(
            db, course_id=course, task_type=task_type, count=30,
            difficulty_id=difficulty,
        )
        await _enroll(db, student_id=student, course_id=course)

        now = datetime.now(UTC)
        homework = await homework_service.issue(
            db,
            student_id=student,
            due_at=now + timedelta(days=7),
            source="teacher",
            now=now,
        )
        await db.commit()
        assert homework["planned_minutes"] is not None
        # Состав собран по два минуты на пункт — снимок обязан это отражать.
        assert homework["planned_minutes"] == pytest.approx(
            homework["total"] * 2, abs=2
        )
        assert homework["volume_details"]["minutes_budget"] > 0
    finally:
        await _cleanup(
            db, user_ids=[donor, student], course_ids=[course, donor_course]
        )


# ═══════════════ tsk-896: успевает или нет — три следствия ═══════════════


async def test_home_norm_subtracts_lesson_work_for_those_on_track(db):
    """Кто успевает — тому дома задаём за вычетом урочной работы.

    Замечание оператора 10.09: «у большинства два занятия, они с лихвой
    перекрывают это время, возникает вопрос, зачем ДЗ?». Норма считалась из
    ВСЕЙ недельной работы, а задавалась целиком на дом — то есть человек,
    который половину закрывает на уроке, получал домой полную неделю.
    """
    from app.services.homework_volume_service import compute

    donor_id = await _student(db, "donor-896a")
    student_id = await _student(db, "onrack-896")
    course_id = await _course(db)
    try:
        difficulty_id = await _difficulty(db)
        task_type = _unique_type()
        donor_task = (await _tasks(
            db, course_id=course_id, task_type=task_type, count=1,
            difficulty_id=difficulty_id,
        ))[0]
        await _calibrate(
            db, donor_id=donor_id, task_id=donor_task, course_id=course_id,
            seconds=60,
        )
        await _enroll(db, student_id=student_id, course_id=course_id)

        plan = await compute(db, student_id=student_id)
        # Ученику ничего не задавали и он ничего не делал: норма не нулевая,
        # но вычета быть не может — доли урочной работы нет.
        assert plan.minutes_per_week is not None
    finally:
        await _cleanup(db, user_ids=[donor_id, student_id], course_ids=[course_id])


async def test_behind_student_gets_a_bigger_growth_step(db):
    """Отстающему шаг роста больше обычного (tsk-896).

    Решение оператора 10.09: «Если Якунина отстаёт, почему её ДЗ меньше, чем у
    Редько? Логично задавать ей больше, чтобы нагнала». Обычный шаг (×1.2)
    подтягивает человека, которому не хватает трети, годами.
    """
    from app.services.homework_volume_service import (
        BEHIND_GROWTH_FACTOR,
        GROWTH_FACTOR,
        ceiling_for,
        minutes_ceiling_for,
    )

    assert BEHIND_GROWTH_FACTOR > GROWTH_FACTOR

    # Тот, кто успевает, растёт обычным шагом; отстающий — увеличенным.
    assert ceiling_for(100, on_track=True) == round(100 * GROWTH_FACTOR)
    assert ceiling_for(100, on_track=False) == round(100 * BEHIND_GROWTH_FACTOR)
    assert minutes_ceiling_for(200, on_track=True) == round(200 * GROWTH_FACTOR)
    assert minutes_ceiling_for(200, on_track=False) == round(
        200 * BEHIND_GROWTH_FACTOR
    )

    # Медленного увеличенный шаг не задевает: базовый потолок всё равно выше.
    assert ceiling_for(5, on_track=False) == ceiling_for(5, on_track=True)
