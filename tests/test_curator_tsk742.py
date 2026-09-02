"""Кураторство — tsk-742.

Проверяем на НАСТОЯЩЕЙ БД три вещи, каждая из которых ломается по-своему:

* **правило вывода раскладки** — постоянный слот, лидер по занятиям, ничья,
  пустота, исключение оператора и (отдельно) ведущий, заданный ТОЛЬКО колонкой
  `lesson_occurrence.teacher_id` без строки совместного ведения;
* **закрепление с историей** — идемпотентность, закрытие прежнего отрезка,
  единственность действующего куратора на уровне базы;
* **доска и отчёт** — порядок по срочности и главная цифра отчёта: сколько
  учеников куратор не тронул ни разу.

Граф фикстуры (все ученики — role student, все преподаватели — role teacher):

    slot_solo    ── преп. A ──> st_slot_solo          (уровень 1)
    slot_pair    ── преп. A, B ──> st_pair            (уровень 2 решает занятия)
    занятия: A×3, B×1 у st_pair                        -> куратор A
             A×2, B×2 у st_tie                         -> ничья, к оператору
    st_lonely   — ни слота, ни занятий                 -> к оператору
    st_column   — занятие, где ведущий задан только колонкой (преп. C)
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.users import Users
from app.services import (
    curator_activity_service,
    curator_board_service,
    curator_service,
)
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk742"


_TOKENS: dict[int, str] = {}


async def _new_user(db, role: str | None, name: str) -> int:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    _TOKENS[int(u.id)] = token
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.flush()
    return int(u.id)


async def _new_slot(db, *, owner_id: int, teachers: list[int], students: list[int]) -> int:
    slot_id = (await db.execute(text(
        "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
        "VALUES (:t, 1, '18:00', 60) RETURNING id"
    ), {"t": owner_id})).scalar()
    for t in teachers:
        await db.execute(text(
            "INSERT INTO lesson_slot_teacher (slot_id, teacher_id) VALUES (:s, :t)"
        ), {"s": slot_id, "t": t})
    for s in students:
        await db.execute(text(
            "INSERT INTO lesson_slot_student (slot_id, student_id) VALUES (:sl, :st)"
        ), {"sl": slot_id, "st": s})
    return int(slot_id)


async def _new_occurrence(
    db, *, owner_id: int, teachers: list[int], student_id: int,
    days_ago: int = 7, status: str = "confirmed",
) -> int:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    occ_id = (await db.execute(text(
        "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
        "VALUES (:t, :w, 60) RETURNING id"
    ), {"t": owner_id, "w": when})).scalar()
    for t in teachers:
        await db.execute(text(
            "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id) "
            "VALUES (:o, :t)"
        ), {"o": occ_id, "t": t})
    await db.execute(text(
        "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
        "VALUES (:o, :s, :st)"
    ), {"o": occ_id, "s": student_id, "st": status})
    return int(occ_id)


@pytest.fixture
async def graph(db):
    """Мир задачи: оператор, три преподавателя, пять учеников, расписание."""
    ids: dict[str, int] = {}

    # Оператор — единственный с ролью admin, именно так его находит правило.
    ids["operator"] = await _new_user(db, "admin", "operator")
    await db.execute(text(
        "INSERT INTO user_roles (user_id, role_id) "
        "SELECT :u, r.id FROM roles r WHERE r.name = 'teacher' ON CONFLICT DO NOTHING"
    ), {"u": ids["operator"]})

    for key in ("teacher_a", "teacher_b", "teacher_c"):
        ids[key] = await _new_user(db, "teacher", key)
    for key in ("st_slot_solo", "st_pair", "st_tie", "st_lonely", "st_column"):
        ids[key] = await _new_user(db, "student", key)

    ids["course"] = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"
    ), {"t": f"{_TAG} курс"})).scalar()

    # Уровень 1: единственный преподаватель в постоянном слоте.
    # Владельцем слота ставим оператора — ровно как на бою, где он создатель
    # всех слотов. Правило обязано его не заметить.
    await _new_slot(
        db, owner_id=ids["operator"],
        teachers=[ids["teacher_a"], ids["operator"]],
        students=[ids["st_slot_solo"]],
    )
    # Уровень 2: в слоте двое, решают проведённые занятия.
    await _new_slot(
        db, owner_id=ids["operator"],
        teachers=[ids["teacher_a"], ids["teacher_b"], ids["operator"]],
        students=[ids["st_pair"], ids["st_tie"]],
    )
    for i in range(3):
        await _new_occurrence(
            db, owner_id=ids["operator"], teachers=[ids["teacher_a"]],
            student_id=ids["st_pair"], days_ago=7 + i,
        )
    await _new_occurrence(
        db, owner_id=ids["operator"], teachers=[ids["teacher_b"]],
        student_id=ids["st_pair"], days_ago=20,
    )
    # Ничья: по двое занятий у каждого.
    for i in range(2):
        await _new_occurrence(
            db, owner_id=ids["operator"], teachers=[ids["teacher_a"]],
            student_id=ids["st_tie"], days_ago=5 + i,
        )
        await _new_occurrence(
            db, owner_id=ids["operator"], teachers=[ids["teacher_b"]],
            student_id=ids["st_tie"], days_ago=10 + i,
        )
    # Ведущий задан ТОЛЬКО колонкой занятия, строки совместного ведения нет —
    # так заведены старые занятия, и отбор по одной таблице их теряет.
    await _new_occurrence(
        db, owner_id=ids["teacher_c"], teachers=[], student_id=ids["st_column"],
        days_ago=3,
    )
    await db.commit()
    return ids


def _by_student(rows, student_id):
    for r in rows:
        if int(r["student_id"]) == student_id:
            return r
    return None


async def _derive(db, graph):
    """Раскладка, суженная до учеников фикстуры: в базе есть и чужие."""
    result = await curator_service.derive_from_schedule(db)
    mine = set(graph[k] for k in
               ("st_slot_solo", "st_pair", "st_tie", "st_lonely", "st_column"))
    return {
        "resolved": [r for r in result["resolved"] if int(r["student_id"]) in mine],
        "unresolved": [r for r in result["unresolved"] if int(r["student_id"]) in mine],
    }


# ─── Правило вывода ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_slot_teacher_becomes_curator(db, graph):
    """Один преподаватель в постоянном слоте — он и куратор."""
    res = await _derive(db, graph)
    row = _by_student(res["resolved"], graph["st_slot_solo"])
    assert row is not None, "ученик с единственным преподавателем остался без куратора"
    assert int(row["curator_id"]) == graph["teacher_a"]
    assert row["reason"] == curator_service.REASON_PERMANENT_SLOT


@pytest.mark.asyncio
async def test_operator_never_becomes_curator(db, graph):
    """Оператор ведёт занятия у всех, но из кураторства вышел.

    Это не мелочь: на боевой базе он числится ведущим у КАЖДОГО ученика, и без
    исключения правило назначило бы его куратором всей школы — то есть оставило
    бы ровно то состояние, из которого задача выводит.
    """
    res = await _derive(db, graph)
    assigned = {int(r["curator_id"]) for r in res["resolved"]}
    assert graph["operator"] not in assigned


@pytest.mark.asyncio
async def test_lesson_leader_wins_when_slot_is_ambiguous(db, graph):
    """Двое в слоте — решает тот, кто провёл больше занятий."""
    res = await _derive(db, graph)
    row = _by_student(res["resolved"], graph["st_pair"])
    assert row is not None
    assert int(row["curator_id"]) == graph["teacher_a"]
    assert row["reason"] == curator_service.REASON_MOST_LESSONS
    assert int(row["lessons_in_window"]) == 3


@pytest.mark.asyncio
async def test_tie_goes_to_operator_not_to_a_coin_flip(db, graph):
    """Ничья не разрешается автоматически: у ученика правда двое ведущих."""
    res = await _derive(db, graph)
    assert _by_student(res["resolved"], graph["st_tie"]) is None
    row = _by_student(res["unresolved"], graph["st_tie"])
    assert row is not None
    assert row["unresolved_reason"] == curator_service.UNRESOLVED_AMBIGUOUS


@pytest.mark.asyncio
async def test_student_without_schedule_goes_to_operator(db, graph):
    """Ни слота, ни занятий — отдельный повод, не тот же, что ничья."""
    res = await _derive(db, graph)
    row = _by_student(res["unresolved"], graph["st_lonely"])
    assert row is not None
    assert row["unresolved_reason"] == curator_service.UNRESOLVED_NO_TEACHER


@pytest.mark.asyncio
async def test_teacher_declared_only_by_column_is_found(db, graph):
    """Ведущий, заданный колонкой занятия без строки совместного ведения.

    Регрессия по классу «ведущий занятия живёт в двух местах»: отбор только по
    `lesson_occurrence_teacher` теряет занятия, заведённые старым способом, и
    ученик молча остаётся ничей.
    """
    res = await _derive(db, graph)
    row = _by_student(res["resolved"], graph["st_column"])
    assert row is not None, "ведущий из колонки занятия потерян"
    assert int(row["curator_id"]) == graph["teacher_c"]


# ─── Закрепление и история ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_is_idempotent(db, graph):
    """Повторное закрепление того же куратора не плодит историю."""
    first = await curator_service.assign(
        db, student_id=graph["st_lonely"], curator_id=graph["teacher_a"],
        reason="проверка", commit=False,
    )
    assert first["changed"] is True
    second = await curator_service.assign(
        db, student_id=graph["st_lonely"], curator_id=graph["teacher_a"], commit=False,
    )
    assert second["changed"] is False
    hist = await curator_service.history(db, graph["st_lonely"])
    assert len(hist) == 1


@pytest.mark.asyncio
async def test_change_closes_previous_period_and_keeps_history(db, graph):
    """Смена куратора закрывает прежний отрезок и сохраняет обе причины."""
    await curator_service.assign(
        db, student_id=graph["st_lonely"], curator_id=graph["teacher_a"],
        reason="первый", commit=False,
    )
    await curator_service.assign(
        db, student_id=graph["st_lonely"], curator_id=graph["teacher_b"],
        reason="второй", ended_reason="ушёл в отпуск", commit=False,
    )
    hist = await curator_service.history(db, graph["st_lonely"])
    assert len(hist) == 2
    current = [h for h in hist if h["ended_at"] is None]
    assert len(current) == 1
    assert int(current[0]["curator_id"]) == graph["teacher_b"]
    assert current[0]["reason"] == "второй"
    closed = [h for h in hist if h["ended_at"] is not None][0]
    assert int(closed["curator_id"]) == graph["teacher_a"]
    assert closed["ended_reason"] == "ушёл в отпуск"


@pytest.mark.asyncio
async def test_two_open_periods_are_rejected_by_the_database(db, graph):
    """Единственность действующего куратора держит база, а не код.

    Проверка в приложении не спасает от двух одновременных смен: обе прошли бы,
    и у ученика оказалось бы два ответственных — каждый в уверенности, что
    разберётся второй.
    """
    await db.execute(text(
        "INSERT INTO student_curator (student_id, curator_id, source) "
        "VALUES (:s, :c, 'manual')"
    ), {"s": graph["st_lonely"], "c": graph["teacher_a"]})
    with pytest.raises(IntegrityError):
        await db.execute(text(
            "INSERT INTO student_curator (student_id, curator_id, source) "
            "VALUES (:s, :c, 'manual')"
        ), {"s": graph["st_lonely"], "c": graph["teacher_b"]})
    await db.rollback()


@pytest.mark.asyncio
async def test_self_curation_is_rejected(db, graph):
    """Человек не может быть куратором самому себе."""
    with pytest.raises(ValueError):
        await curator_service.assign(
            db, student_id=graph["teacher_a"], curator_id=graph["teacher_a"],
            commit=False,
        )


@pytest.mark.asyncio
async def test_apply_derived_does_not_overwrite_manual(db, graph):
    """Раскладка не отменяет решение человека молча."""
    await curator_service.assign(
        db, student_id=graph["st_slot_solo"], curator_id=graph["teacher_b"],
        reason="решил методист", commit=False,
    )
    await db.commit()

    plan = await curator_service.apply_derived(db, dry_run=True)
    skipped = {int(r["student_id"]) for r in plan["skipped_existing"]}
    planned = {int(r["student_id"]) for r in plan["planned"]}
    assert graph["st_slot_solo"] in skipped
    assert graph["st_slot_solo"] not in planned

    forced = await curator_service.apply_derived(db, dry_run=True, overwrite=True)
    assert graph["st_slot_solo"] in {int(r["student_id"]) for r in forced["planned"]}


@pytest.mark.asyncio
async def test_apply_derived_writes_only_when_asked(db, graph):
    """Сухой прогон ничего не пишет; обычный — пишет."""
    dry = await curator_service.apply_derived(db, dry_run=True)
    assert dry["applied"] == 0
    assert await curator_service.get_current(db, graph["st_slot_solo"]) is None

    wet = await curator_service.apply_derived(db, dry_run=False)
    assert wet["applied"] >= 1
    current = await curator_service.get_current(db, graph["st_slot_solo"])
    assert current is not None
    assert int(current["curator_id"]) == graph["teacher_a"]
    assert current["source"] == curator_service.SOURCE_DERIVED


# ─── Доска куратора ──────────────────────────────────────────────────────────

async def _open_signal(db, *, student_id: int, course_id: int, reason: str, days_ago: int):
    await db.execute(text("""
        INSERT INTO learning_gap_signal
            (course_id, student_id, submissions, students, wrong_rate, status, reason, created_at)
        VALUES (:c, :s, 5, 1, 0.0, 'new', :r, now() - make_interval(days => :d))
    """), {"c": course_id, "s": student_id, "r": reason, "d": days_ago})


@pytest.mark.asyncio
async def test_board_puts_dropout_risk_first(db, graph):
    """Риск ухода поднимается наверх, даже если у соседа сигнал старше.

    Порядок — единственное, что экран обещает: если наверху окажется не тот,
    к кому идти первым, экран бесполезен ровно так же, как его отсутствие.
    """
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await curator_service.assign(
        db, student_id=graph["st_slot_solo"], curator_id=graph["teacher_a"], commit=False)
    await _open_signal(
        db, student_id=graph["st_slot_solo"], course_id=graph["course"],
        reason="error_rate", days_ago=30)
    await _open_signal(
        db, student_id=graph["st_pair"], course_id=graph["course"],
        reason="dropout_risk", days_ago=1)
    await db.commit()

    board = await curator_board_service.get_board(db, curator_id=graph["teacher_a"])
    assert board["students"], "доска куратора пуста"
    assert int(board["students"][0]["student_id"]) == graph["st_pair"]
    assert board["students"][0]["priority"] == curator_board_service.PRIORITY_URGENT
    assert board["summary"]["urgent"] == 1
    # Старый сигнал по второму ученику — просрочен, но не срочен.
    second = _by_student(board["students"], graph["st_slot_solo"])
    assert second["priority"] == curator_board_service.PRIORITY_OVERDUE


@pytest.mark.asyncio
async def test_board_shows_only_own_students(db, graph):
    """Кураторство не расширяет видимость: чужих на доске нет."""
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await curator_service.assign(
        db, student_id=graph["st_tie"], curator_id=graph["teacher_b"], commit=False)
    await db.commit()

    board = await curator_board_service.get_board(db, curator_id=graph["teacher_a"])
    seen = {int(s["student_id"]) for s in board["students"]}
    assert graph["st_pair"] in seen
    assert graph["st_tie"] not in seen


# ─── Отчёт по активности ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_counts_untouched_students(db, graph):
    """Главная цифра отчёта — скольких куратор не тронул ни разу."""
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await curator_service.assign(
        db, student_id=graph["st_slot_solo"], curator_id=graph["teacher_a"], commit=False)
    await db.commit()

    since, _ = curator_activity_service.week_bounds()
    # Касание внутри отчётной недели — просмотр карточки одного из двоих.
    await db.execute(text("""
        INSERT INTO audit_event (user_id, event_type, ts, details)
        VALUES (:u, :e, :ts, CAST(:d AS jsonb))
    """), {"u": graph["teacher_a"], "e": curator_activity_service.CURATOR_STUDENT_VIEWED,
           "ts": since + timedelta(days=1),
           "d": json.dumps({"student_id": graph["st_pair"]})})
    await db.commit()

    report = await curator_activity_service.weekly_report(db)
    row = next(r for r in report["curators"] if int(r["curator_id"]) == graph["teacher_a"])
    assert row["students"] == 2
    assert row["students_touched"] == 1
    assert row["students_untouched"] == 1
    # Просмотр — это внимание, но не действие: обязанности § 3.2–3.3 им не закрыть.
    assert row["students_acted_on"] == 0


@pytest.mark.asyncio
async def test_report_ignores_touches_of_other_curators_students(db, graph):
    """Проверил работу чужого ученика — полезно школе, но не его кураторство."""
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await curator_service.assign(
        db, student_id=graph["st_tie"], curator_id=graph["teacher_b"], commit=False)
    await db.commit()

    since, _ = curator_activity_service.week_bounds()
    await db.execute(text("""
        INSERT INTO audit_event (user_id, event_type, ts, details)
        VALUES (:u, :e, :ts, CAST(:d AS jsonb))
    """), {"u": graph["teacher_a"], "e": curator_activity_service.CURATOR_STUDENT_VIEWED,
           "ts": since + timedelta(days=1),
           "d": json.dumps({"student_id": graph["st_tie"]})})
    await db.commit()

    report = await curator_activity_service.weekly_report(db)
    row = next(r for r in report["curators"] if int(r["curator_id"]) == graph["teacher_a"])
    assert row["students_touched"] == 0
    assert row["students_untouched"] == 1


@pytest.mark.asyncio
async def test_report_counts_overdue_signals_outside_the_week(db, graph):
    """Сигнал, поднятый месяц назад и не разобранный, обязан быть в отчёте.

    Он не попадает в недельное окно ни одной стороной — а именно он и есть
    самое важное, что отчёт должен показать.
    """
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await _open_signal(
        db, student_id=graph["st_pair"], course_id=graph["course"],
        reason="error_rate", days_ago=40)
    await db.commit()

    report = await curator_activity_service.weekly_report(db)
    row = next(r for r in report["curators"] if int(r["curator_id"]) == graph["teacher_a"])
    assert row["signals_raised"] == 0, "сигнал поднят вне недели"
    assert row["signals_overdue"] == 1
    assert row["oldest_open_signal_days"] >= 39


@pytest.mark.asyncio
async def test_staff_are_not_counted_as_students_anywhere(db, graph):
    """Сотрудник не ученик — ни в раскладке, ни в сводке «без куратора».

    Преподаватели, методисты и владелец школы заведены и как `student`, иначе
    они не открыли бы кабинет ученика. Живой прогон 02.09 показал цену
    расхождения: сам оператор стоял в собственном списке «ничьих», и числа
    раскладки и отчёта не сходились на единицу.
    """
    res = await curator_service.derive_from_schedule(db)
    listed = {int(r["student_id"]) for r in res["resolved"] + res["unresolved"]}
    for key in ("operator", "teacher_a", "teacher_b", "teacher_c"):
        assert graph[key] not in listed, f"{key} попал в список учеников"


async def _set_plan(db, student_id: int, code: str) -> None:
    """Перевести ученика на тариф по коду."""
    await db.execute(text(
        "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
        "SELECT :s, p.id, CURRENT_DATE FROM subscription_plan p WHERE p.code = :c"
    ), {"s": student_id, "c": code})


@pytest.mark.asyncio
async def test_alumni_and_demo_are_out_of_curation(db, graph):
    """Выпускник, демо и служебный тариф кураторства не получают.

    Решение оператора 02.09. Попав в список, они считались бы у куратора как
    «не тронул ни разу» — и он был бы прав: трогать там нечего.
    """
    await _set_plan(db, graph["st_slot_solo"], "alumni")
    await _set_plan(db, graph["st_pair"], "demo")
    await _set_plan(db, graph["st_tie"], "test")
    await db.commit()

    res = await curator_service.derive_from_schedule(db)
    listed = {int(r["student_id"]) for r in res["resolved"] + res["unresolved"]}
    for key in ("st_slot_solo", "st_pair", "st_tie"):
        assert graph[key] not in listed, f"{key} остался в раскладке"


@pytest.mark.asyncio
async def test_active_plan_keeps_curation(db, graph):
    """Действующий тариф с занятиями кураторство не отменяет."""
    await _set_plan(db, graph["st_slot_solo"], "base")
    await db.commit()
    res = await curator_service.derive_from_schedule(db)
    assert graph["st_slot_solo"] in {int(r["student_id"]) for r in res["resolved"]}


@pytest.mark.asyncio
async def test_alumni_drops_off_the_board_and_the_report(db, graph):
    """Уже закреплённый выпускник исчезает и с доски, и из счёта отчёта."""
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await curator_service.assign(
        db, student_id=graph["st_slot_solo"], curator_id=graph["teacher_a"], commit=False)
    await _set_plan(db, graph["st_pair"], "alumni")
    await db.commit()

    board = await curator_board_service.get_board(db, curator_id=graph["teacher_a"])
    seen = {int(s["student_id"]) for s in board["students"]}
    assert graph["st_pair"] not in seen
    assert graph["st_slot_solo"] in seen

    report = await curator_activity_service.weekly_report(db)
    row = next(r for r in report["curators"] if int(r["curator_id"]) == graph["teacher_a"])
    assert row["students"] == 1, "выпускник посчитан в группе куратора"


@pytest.mark.asyncio
async def test_report_counts_only_works_that_wait_for_a_human(db, graph):
    """«Работ на проверке дольше срока» — только те, что ждут человека.

    Без предиката обязательной ручной проверки условие вырождается в «любая
    непроверенная строка `task_results`»: живой прогон на проде дал 2201
    просроченную работу у куратора, у которого очередь честно пуста. Число,
    которое человек читает первым, обязано означать написанное рядом.
    """
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)

    difficulty_id = (await db.execute(
        text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    # Задание с АВТОМАТИЧЕСКОЙ проверкой: человека оно не ждёт никогда.
    task_id = (await db.execute(text("""
        INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id,
                           external_uid, max_score, order_position)
        VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1)
        RETURNING id
    """), {
        "tc": json.dumps({"type": "SA", "stem": f"{_TAG} авто", "title": ""}),
        "sr": json.dumps({"max_score": 10, "manual_review_required": False}),
        "cid": graph["course"], "did": difficulty_id,
        "uid": f"{_TAG}-auto-{random.randint(10**8, 10**10)}",
    })).scalar()
    attempt_id = (await db.execute(text(
        "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
        "VALUES (:u, :c, :c, 'test') RETURNING id"
    ), {"u": graph["st_pair"], "c": graph["course"]})).scalar()
    await db.execute(text("""
        INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score,
                                  is_correct, submitted_at, received_at, count_retry,
                                  source_system)
        VALUES (:u, :t, :a, 0, 10, false, now() - interval '30 days',
                now() - interval '30 days', 0, 'spw_web')
    """), {"u": graph["st_pair"], "t": task_id, "a": attempt_id})
    await db.commit()

    report = await curator_activity_service.weekly_report(db)
    row = next(r for r in report["curators"] if int(r["curator_id"]) == graph["teacher_a"])
    assert row["reviews_overdue"] == 0, (
        "авто-проверенная работа не ждёт человека и в просроченные идти не должна"
    )


# ─── Слияние учёток ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_merge_moves_curation_to_the_live_account(db, graph):
    """Кураторство переезжает к живой учётке, а не остаётся у слитой.

    Тот же класс, что перерыв (tsk-610) и ручная цена (tsk-548): строка,
    оставшаяся у слитой учётки, молча пропадает. Здесь цена ошибки —
    живой ученик снова становится «ничей» между занятиями.
    """
    from app.services import user_merge_service

    duplicate = await _new_user(db, "student", "st_duplicate")
    await curator_service.assign(
        db, student_id=duplicate, curator_id=graph["teacher_a"],
        reason="закреплён до слияния", commit=False,
    )
    await db.commit()

    await user_merge_service.apply_merge(db, duplicate, graph["st_lonely"])
    await db.commit()

    moved = await curator_service.get_current(db, graph["st_lonely"])
    assert moved is not None, "закрепление осталось у слитой учётки"
    assert int(moved["curator_id"]) == graph["teacher_a"]
    assert await curator_service.get_current(db, duplicate) is None


@pytest.mark.asyncio
async def test_merge_keeps_one_curator_when_both_accounts_had_one(db, graph):
    """У живой учётки свой куратор — двух действующих после слияния не будет."""
    from app.services import user_merge_service

    duplicate = await _new_user(db, "student", "st_duplicate2")
    await curator_service.assign(
        db, student_id=duplicate, curator_id=graph["teacher_a"], commit=False)
    await curator_service.assign(
        db, student_id=graph["st_lonely"], curator_id=graph["teacher_b"], commit=False)
    await db.commit()

    await user_merge_service.apply_merge(db, duplicate, graph["st_lonely"])
    await db.commit()

    hist = await curator_service.history(db, graph["st_lonely"])
    open_periods = [h for h in hist if h["ended_at"] is None]
    assert len(open_periods) == 1
    # Побеждает куратор живой учётки: он закреплён за тем человеком, который
    # остаётся работать.
    assert int(open_periods[0]["curator_id"]) == graph["teacher_b"]
    # Прошлое не потеряно — история слитой учётки переехала целиком.
    assert any(int(h["curator_id"]) == graph["teacher_a"] for h in hist)


@pytest.mark.asyncio
async def test_merge_moves_roster_of_a_merged_teacher(db, graph):
    """Слили преподавателя — его группа переходит к живой учётке, а не исчезает."""
    from app.services import user_merge_service

    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await db.commit()

    await user_merge_service.apply_merge(db, graph["teacher_a"], graph["teacher_b"])
    await db.commit()

    roster = await curator_service.roster_ids(db, graph["teacher_b"])
    assert graph["st_pair"] in roster


# ─── Сигнал молчащему куратору ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_silent_curator_is_flagged_after_two_weeks(db, graph):
    """Куратор, не тронувший НИКОГО две недели подряд, попадает в список."""
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await db.commit()

    silent = await curator_activity_service.curators_without_coverage(db, weeks=2)
    assert graph["teacher_a"] in {int(c["curator_id"]) for c in silent}


@pytest.mark.asyncio
async def test_one_touch_in_either_week_clears_the_flag(db, graph):
    """Одно касание в любой из недель снимает сигнал.

    Недели считаются ПОДРЯД: человек, молчавший в июле и работавший вчера,
    предупреждения получить не должен — иначе сигнал наказывает за прошлое.
    """
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    since, _ = curator_activity_service.week_bounds()
    await db.execute(text("""
        INSERT INTO audit_event (user_id, event_type, ts, details)
        VALUES (:u, :e, :ts, CAST(:d AS jsonb))
    """), {"u": graph["teacher_a"], "e": curator_activity_service.CURATOR_STUDENT_VIEWED,
           "ts": since + timedelta(days=1),
           "d": json.dumps({"student_id": graph["st_pair"]})})
    await db.commit()

    silent = await curator_activity_service.curators_without_coverage(db, weeks=2)
    assert graph["teacher_a"] not in {int(c["curator_id"]) for c in silent}


@pytest.mark.asyncio
async def test_weekly_run_delivers_report_and_nudge_without_repeats(db, graph):
    """Прогон кладёт отчёт владельцу школы и сигнал молчащему куратору.

    И не делает этого дважды: планировщик просыпается каждый час, и без защиты
    от повтора в понедельник пришло бы двадцать четыре одинаковых сводки.
    """
    from app.services import curator_report_cron_service as cron

    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await db.commit()

    first = await cron.send_weekly_report(db, force=True)
    assert first["sent"] >= 1
    assert first["nudged"] >= 1

    got = (await db.execute(text("""
        SELECT count(*) FROM notifications
        WHERE kind = :k AND user_id = :u
    """), {"k": cron.INACTIVITY_KIND, "u": graph["teacher_a"]})).scalar()
    assert got == 1

    second = await cron.send_weekly_report(db)
    assert second.get("skipped"), "повторная отправка за ту же неделю не заблокирована"
    got_again = (await db.execute(text("""
        SELECT count(*) FROM notifications
        WHERE kind = :k AND user_id = :u
    """), {"k": cron.INACTIVITY_KIND, "u": graph["teacher_a"]})).scalar()
    assert got_again == 1


@pytest.mark.asyncio
async def test_curator_without_students_is_not_flagged(db, graph):
    """У кого нет учеников — тому и молчать не о чем."""
    silent = await curator_activity_service.curators_without_coverage(db, weeks=2)
    assert graph["teacher_c"] not in {int(c["curator_id"]) for c in silent}


# ─── API ─────────────────────────────────────────────────────────────────────

def _auth(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKENS[user_id]}"}


@pytest.mark.asyncio
async def test_api_teacher_cannot_open_someone_elses_board(db, graph, client):
    """Чужая доска преподавателю закрыта: кураторство не даёт видеть чужих."""
    await db.commit()
    resp = await client.get(
        f"/api/v1/curator/board?curator_id={graph['teacher_b']}",
        headers=_auth(graph["teacher_a"]),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_view_is_not_recorded_for_someone_elses_student(db, graph, client):
    """Просмотр чужого ученика не идёт в кураторскую активность.

    Отвечаем `recorded: false`, а не ошибкой: карточку открывают и методист, и
    преподаватель занятия — падать тут нечему. Но засчитывать такой просмотр
    нельзя: охват в отчёте показывал бы работу, которой не было.
    """
    await curator_service.assign(
        db, student_id=graph["st_tie"], curator_id=graph["teacher_b"], commit=False)
    await db.commit()
    resp = await client.post(
        f"/api/v1/curator/students/{graph['st_tie']}/view",
        headers=_auth(graph["teacher_a"]),
    )
    assert resp.status_code == 200
    assert resp.json()["recorded"] is False


@pytest.mark.asyncio
async def test_api_derive_apply_is_dry_by_default(db, graph, client):
    """Пустое тело — сухой прогон. Запись, которую видят люди, не должна
    происходить от нажатия по инерции."""
    await db.commit()
    resp = await client.post(
        "/api/v1/curator/derive-apply", headers=_auth(graph["operator"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["applied"] == 0


@pytest.mark.asyncio
async def test_report_is_closed_to_a_plain_teacher(db, graph, client):
    """Преподаватель без роли методиста сводку по коллегам не видит.

    Решение оператора 02.09: доступ решается РОЛЬЮ, а не гейтом. Роль методиста
    в этой школе означает доступ к работе кураторов; у кого её нет — тот видит
    только свою доску.
    """
    await db.commit()
    resp = await client.get(
        "/api/v1/curator/weekly-report", headers=_auth(graph["teacher_a"])
    )
    assert resp.status_code == 403

    ok = await client.get(
        "/api/v1/curator/weekly-report", headers=_auth(graph["operator"])
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_report_recipients_match_the_gate(db, graph):
    """Кому ручка открыта — тому и рассылка. Разойтись им нельзя.

    Иначе получается тихий перекос: на экране человек отчёт видит, а письмо
    ему не приходит (или наоборот — приходит тому, кто открыть не может).
    """
    from app.services import curator_report_cron_service as cron

    methodist = await _new_user(db, "methodist", "methodist_recipient")
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await db.commit()

    await cron.send_weekly_report(db, force=True)
    for uid, who in ((methodist, "методист"), (graph["operator"], "владелец школы")):
        got = (await db.execute(text("""
            SELECT count(*) FROM notifications WHERE kind = :k AND user_id = :u
        """), {"k": cron.NOTIFICATION_KIND, "u": uid})).scalar()
        assert got == 1, f"{who} не получил сводку"
    # А преподаватель без роли — не получил.
    none = (await db.execute(text("""
        SELECT count(*) FROM notifications WHERE kind = :k AND user_id = :u
    """), {"k": cron.NOTIFICATION_KIND, "u": graph["teacher_a"]})).scalar()
    assert none == 0


@pytest.mark.asyncio
async def test_report_text_names_the_gap(db, graph):
    """Отчёт словами называет непокрытых учеников — его читают, а не парсят."""
    await curator_service.assign(
        db, student_id=graph["st_pair"], curator_id=graph["teacher_a"], commit=False)
    await db.commit()
    report = await curator_activity_service.weekly_report(db)
    body = curator_activity_service.render_report_text(report)
    assert "Кураторство, неделя" in body
    assert f"{_TAG}-teacher_a" in body
    assert "без внимания" in body
