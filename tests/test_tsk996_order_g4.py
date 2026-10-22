"""tsk-996 (группа 4): результат не зависит от плана запроса.

Каждый тест создаёт равные значения ключа сортировки (одинаковое время —
вставки одной транзакции дают равный `now()` или явный одинаковый момент),
перекладывает строку с меньшим id в конец кучи (`move_to_heap_end`) и
прогоняет вызов под всеми планами `tests/plan_order.PLANS`. Ожидается один и
тот же результат, равный детерминированному порядку по id.

Места: автоназначение курсов правилами и зависимостями (порядок курсов в
плане ученика), якорь позиции в курсе, «продолжить с места», история
ученика, хвост ДЗ «решено из прошлых выдач», датчики сигналов пробелов и их
лента, разбор результатов по урокам, персональный уровень (причина при
ничьей), темп и список учеников темы, кандидаты в дубли.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.schemas.checking import CheckResult, StudentAnswer, StudentResponse
from app.services import (
    assignment_rules_service as ars,
    course_dependencies_enrollment_service as deps_service,
    homework_service,
    learning_gap_signals_service as sig,
    learning_gaps_service,
    me_service,
    topic_mastery_service,
    users_dedup_service,
)
from app.services.course_position import anchor_for
from app.services.requirement_override_service import resolve_levels
from app.services.task_results_service import TaskResultsService
from tests.plan_order import PLANS, assert_same, move_to_heap_end, run_under_plans, with_plan

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc).replace(microsecond=0)
#: Общий момент «пакетной» записи: несколько строк с одним и тем же временем.
SAME = NOW - timedelta(days=2)
#: Число равных строк. Больше 7: на коротком списке сортировка PostgreSQL —
#: вставками (устойчивая) и ничьи случайно выходят в порядке входа.
TIES = 12


# ─── Построение данных ──────────────────────────────────────────────────────


async def _course(db, title: str = "курс", *, uid: Optional[str] = None) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid) "
            "VALUES (:t, 'self_guided', :uid) RETURNING id"
        ),
        {"t": f"tsk996g4 {title}", "uid": uid or f"tsk996g4-{uuid4().hex[:12]}"},
    )
    return int(res.scalar_one())


async def _course_uid(db, course_id: int) -> str:
    return str(
        (await db.execute(text("SELECT course_uid FROM courses WHERE id = :c"), {"c": course_id}))
        .scalar_one()
    )


async def _task(
    db, course_id: int, *, position: Optional[int] = None, task_type: str = "SA",
    level: str = "required",
) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar_one()
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid, "
            " order_position, is_active, requirement_level) "
            "VALUES (CAST(:tc AS jsonb), :c, :d, :uid, :p, true, :lvl) RETURNING id"
        ),
        {
            "tc": json.dumps({"type": task_type, "stem": "tsk996g4"}),
            "c": course_id, "d": diff, "uid": f"tsk996g4-{uuid4().hex[:12]}",
            "p": position, "lvl": level,
        },
    )
    return int(res.scalar_one())


async def _material(db, course_id: int) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active, requirement_level) "
            "VALUES (:t, 'text', CAST('{}' AS jsonb), :c, true, 'required') RETURNING id"
        ),
        {"t": f"tsk996g4 м {uuid4().hex[:6]}", "c": course_id},
    )
    return int(res.scalar_one())


async def _user(db, name: Optional[str] = None, *, role: Optional[str] = None) -> int:
    uid = int(
        (
            await db.execute(
                text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
                {"n": name or f"tsk996g4 ученик {uuid4().hex[:6]}"},
            )
        ).scalar_one()
    )
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": uid, "r": role},
        )
    return uid


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": user_id, "c": course_id},
    )


async def _attempt(db, user_id: int, course_id: int) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                    "VALUES (:u, :c, :c, 'spw') RETURNING id"
                ),
                {"u": user_id, "c": course_id},
            )
        ).scalar_one()
    )


async def _result(
    db, user_id: int, task_id: int, *, at: datetime, correct: Optional[bool] = True,
    attempt_id: Optional[int] = None, source: str = "spw_web",
    scale_scores: Optional[dict] = None, code_review: Optional[dict] = None,
) -> int:
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, attempt_id, submitted_at, "
            " count_retry, received_at, max_score, source_system, is_correct, "
            " scale_scores, code_review) "
            "VALUES (:s, :u, :t, :a, :ts, 0, :ts, 10, :src, :ok, "
            " CAST(:sc AS jsonb), CAST(:cr AS jsonb)) RETURNING id"
        ),
        {
            "s": 10 if correct else 0, "u": user_id, "t": task_id, "a": attempt_id,
            "ts": at, "src": source, "ok": correct,
            "sc": json.dumps(scale_scores) if scale_scores is not None else None,
            "cr": json.dumps(code_review) if code_review is not None else None,
        },
    )
    return int(res.scalar_one())


async def _course_order(db, user_id: int) -> list[int]:
    """Курсы ученика в порядке его плана (`user_courses.order_number`)."""
    rows = await db.execute(
        text("SELECT course_id FROM user_courses WHERE user_id = :u ORDER BY order_number"),
        {"u": user_id},
    )
    return [int(r[0]) for r in rows]


async def _per_plan(db, students: dict[str, int], call) -> dict[str, Any]:
    """Под каждым планом — вызов на СВОЁМ ученике (вызов меняет его состояние)."""
    seen: dict[str, Any] = {}
    for plan in PLANS:
        seen[plan] = await with_plan(db, plan, lambda: call(students[plan]))
    return seen


# ─── Автоназначение курсов: порядок курсов в плане ученика ──────────────────


async def test_answer_rules_enroll_in_rule_order_under_any_plan(db):
    """Два сработавших правила задания зачисляют курсы в порядке id правил.

    order_number нового курса ставит триггер (MAX+1), поэтому порядок перебора
    правил — это порядок курсов в плане ученика и в обходе движка.
    """
    trigger = await _course(db, "триггер")
    task_id = await _task(db, trigger)
    targets = [await _course(db, f"цель {i}") for i in (1, 2, 3)]
    rule_ids = []
    for target in targets:
        res = await db.execute(
            text(
                "INSERT INTO assignment_rule (code, trigger_event, task_id, target_course_uid) "
                "VALUES (:code, 'task_failed', :t, :uid) RETURNING id"
            ),
            {"code": f"tsk996g4-{uuid4().hex[:8]}", "t": task_id,
             "uid": await _course_uid(db, target)},
        )
        rule_ids.append(int(res.scalar_one()))
    await move_to_heap_end(db, "assignment_rule", rule_ids[0])
    students = {plan: await _user(db) for plan in PLANS}
    await db.commit()

    answer = StudentAnswer(type="SA", response=StudentResponse(value="x"))
    check = CheckResult(score=0, max_score=10, is_correct=False)

    async def call(student: int) -> list[int]:
        await ars.evaluate_rules_for_answer(
            db, student_id=student, task_id=task_id, answer=answer, check_result=check,
        )
        return await _course_order(db, student)

    assert_same(await _per_plan(db, students, call), targets)


@pytest.mark.parametrize("trigger_event", ["course_failed", "quiz_scale"])
async def test_attempt_rules_enroll_in_rule_order_under_any_plan(db, trigger_event):
    """Правила после попытки (`course_failed`, `quiz_scale`) — тоже в порядке id."""
    trigger = await _course(db, "триггер попытки")
    task_id = await _task(db, trigger)
    targets = [await _course(db, f"цель {i}") for i in (1, 2, 3)]
    condition = {} if trigger_event == "course_failed" else {"scale": "a", "min_score": 1}
    rule_ids = []
    for target in targets:
        res = await db.execute(
            text(
                "INSERT INTO assignment_rule (code, trigger_event, course_id, condition, "
                " target_course_uid) "
                "VALUES (:code, :ev, :c, CAST(:cond AS jsonb), :uid) RETURNING id"
            ),
            {"code": f"tsk996g4-{uuid4().hex[:8]}", "ev": trigger_event, "c": trigger,
             "cond": json.dumps(condition), "uid": await _course_uid(db, target)},
        )
        rule_ids.append(int(res.scalar_one()))
    await move_to_heap_end(db, "assignment_rule", rule_ids[0])
    students: dict[str, int] = {}
    attempts: dict[int, int] = {}
    for plan in PLANS:
        student = await _user(db)
        attempt = await _attempt(db, student, trigger)
        await _result(db, student, task_id, at=SAME, correct=False, attempt_id=attempt,
                      scale_scores={"a": 2})
        students[plan] = student
        attempts[student] = attempt
    await db.commit()

    async def call(student: int) -> list[int]:
        await ars.evaluate_rules_for_attempt(
            db, student_id=student, attempt_id=attempts[student]
        )
        return await _course_order(db, student)

    assert_same(await _per_plan(db, students, call), targets)


async def test_dependencies_assigned_in_id_order_under_any_plan(db):
    """Доназначенные зависимости встают в план ученика по возрастанию id."""
    deps = [await _course(db, f"зависимость {i}") for i in (1, 2, 3)]
    main = await _course(db, "основной")
    for dep in reversed(deps):
        await db.execute(
            text("INSERT INTO course_dependencies (course_id, required_course_id) VALUES (:c, :r)"),
            {"c": main, "r": dep},
        )
    await move_to_heap_end(db, "courses", deps[0])
    students = {plan: await _user(db) for plan in PLANS}
    await db.commit()

    async def call(student: int) -> list[int]:
        await deps_service.ensure_dependencies_assigned(
            db, student_id=student, course_ids=[main]
        )
        return await _course_order(db, student)

    assert_same(await _per_plan(db, students, call), deps)


# ─── Позиция ученика: якорь и «продолжить с места» ──────────────────────────


async def test_course_anchor_same_under_any_plan(db):
    """Задания, закрытые одной пачкой: якорь — задание с бОльшим id."""
    course = await _course(db, "якорь")
    tasks = [await _task(db, course, position=i) for i in (1, 2, 3)]
    material = await _material(db, course)
    student = await _user(db)
    result_ids = [await _result(db, student, t, at=SAME) for t in tasks]
    await db.execute(
        text(
            "INSERT INTO student_material_progress (student_id, material_id, status, completed_at) "
            "VALUES (:u, :m, 'completed', :ts)"
        ),
        {"u": student, "m": material, "ts": SAME},
    )
    await move_to_heap_end(db, "task_results", result_ids[-1])
    await db.commit()
    items = [{"item_type": "task", "item_id": t} for t in tasks]
    items.append({"item_type": "material", "item_id": material})

    seen = await run_under_plans(db, lambda: anchor_for(db, student_id=student, items=items))
    assert_same(seen, ("task", tasks[-1]))


async def test_last_position_same_under_any_plan(db):
    """«Продолжить с места» при пакетной проверке: последняя по id сдача, а не
    материал с тем же временем."""
    course = await _course(db, "место")
    tasks = [await _task(db, course) for _ in range(3)]
    material = await _material(db, course)
    student = await _user(db)
    await db.execute(
        text(
            "INSERT INTO student_material_progress (student_id, material_id, status, completed_at) "
            "VALUES (:u, :m, 'completed', :ts)"
        ),
        {"u": student, "m": material, "ts": SAME},
    )
    # Сдачи вставлены в обратном порядке заданий: у последней сдачи — первое задание.
    result_ids = [await _result(db, student, t, at=SAME) for t in reversed(tasks)]
    await move_to_heap_end(db, "task_results", result_ids[-1])
    await db.commit()

    async def call() -> tuple:
        row = (
            await db.execute(text(me_service._LAST_ACTIVITY_SQL), {"user_id": student})
        ).mappings().first()
        return (row["kind"], row["task_id"], row["material_id"])

    assert_same(await run_under_plans(db, call), ("task", tasks[0], None))


async def test_history_pages_same_under_any_plan(db):
    """История ученика: при равном received_at — новее по id, страницы стабильны."""
    course = await _course(db, "история")
    student = await _user(db)
    result_ids = []
    for _ in range(6):
        task = await _task(db, course)
        result_ids.append(await _result(db, student, task, at=SAME))
    await move_to_heap_end(db, "task_results", result_ids[-1])
    await db.commit()

    async def call() -> list[int]:
        pages = []
        for offset in (0, 2, 4):
            rows = await me_service.get_history(db, student, limit=2, offset=offset)
            pages.extend(r["task_id"] for r in rows)
        return pages

    expected_tasks = (
        await db.execute(
            text("SELECT task_id FROM task_results WHERE id = ANY(:ids) ORDER BY id DESC"),
            {"ids": result_ids},
        )
    ).scalars().all()
    assert_same(await run_under_plans(db, call), list(expected_tasks))


async def test_identities_same_under_any_plan(db):
    """Identity с одинаковым created_at (бэкфил M2/M6) — по id."""
    student = await _user(db)
    ids = []
    for kind in ("tg", "email"):
        res = await db.execute(
            text(
                "INSERT INTO identity_link (user_id, kind, value, created_at) "
                "VALUES (:u, :k, :v, :ts) RETURNING id"
            ),
            {"u": student, "k": kind, "v": f"tsk996g4-{uuid4().hex[:10]}@example.com"
             if kind == "email" else str(uuid4().int)[:12], "ts": SAME},
        )
        ids.append(int(res.scalar_one()))
    await move_to_heap_end(db, "identity_link", ids[0])
    await db.commit()

    async def call() -> list[str]:
        return [i["kind"] for i in await me_service.get_identities(db, student)]

    assert_same(await run_under_plans(db, call), ["tg", "email"])


# ─── Домашнее задание: хвост «решено из прошлых выдач» ──────────────────────


async def test_orphaned_homework_items_same_under_any_plan(db):
    """Позиции осиротевших решений — по id задания; tier при равном моменте
    отмены — из более ранней выдачи."""
    course = await _course(db, "дз")
    tasks = [await _task(db, course) for _ in range(5)]
    student = await _user(db)
    cancelled = NOW - timedelta(days=3)
    homeworks = []
    for tier in ("required", "extra"):
        res = await db.execute(
            text(
                "INSERT INTO homework_assignment (student_id, issued_at, due_at, source, "
                " planned_volume, cancelled_at) "
                "VALUES (:u, :iss, :due, 'teacher', 5, :c) RETURNING id"
            ),
            {"u": student, "iss": cancelled - timedelta(days=1), "due": cancelled, "c": cancelled},
        )
        hw = int(res.scalar_one())
        homeworks.append(hw)
        for pos, task in enumerate(reversed(tasks), start=1):
            await db.execute(
                text(
                    "INSERT INTO homework_item (homework_id, kind, task_id, position, tier) "
                    "VALUES (:h, 'task', :t, :p, :tier)"
                ),
                {"h": hw, "t": task, "p": pos, "tier": tier},
            )
    attempt = await _attempt(db, student, course)
    for task in reversed(tasks):
        await _result(db, student, task, at=NOW - timedelta(days=1), attempt_id=attempt)
    await move_to_heap_end(db, "homework_assignment", homeworks[0])
    await db.commit()

    async def call() -> list[tuple]:
        items = await homework_service._load_orphaned_items(
            db, student_id=student, start_position=1
        )
        return [(i["item_id"], i["position"], i["tier"]) for i in items]

    expected = [(t, pos, "required") for pos, t in enumerate(tasks, start=1)]
    assert_same(await run_under_plans(db, call), expected)


# ─── Сигналы пробелов: датчики и лента ──────────────────────────────────────


async def _wrong_student(db, course: int, task: int, n: int = 2) -> int:
    student = await _user(db, role="student")
    for _ in range(n):
        await _result(db, student, task, at=SAME, correct=False)
    return student


async def test_student_gaps_ties_same_under_any_plan(db):
    """Равные доля и число ошибок у разных учеников — по id ученика."""
    course = await _course(db, "пробел ученика")
    task = await _task(db, course)
    students = [await _wrong_student(db, course, task) for _ in range(TIES)]
    first_result = (
        await db.execute(text("SELECT min(id) FROM task_results WHERE user_id = :u"),
                         {"u": students[0]})
    ).scalar_one()
    await move_to_heap_end(db, "task_results", int(first_result))
    await db.commit()

    async def call() -> list[int]:
        rows = await sig.find_student_gaps(
            db, min_submissions=1, threshold=0.0, limit=100000
        )
        return [r["student_id"] for r in rows if r["student_id"] in students]

    assert_same(await run_under_plans(db, call), students)


def _ranked(ids: list[int]) -> list[int]:
    """Ожидаемый порядок при чередовании «сильный/слабый» по индексу: сначала
    чётные (сильнее), затем нечётные — внутри групп по id."""
    return ids[0::2] + ids[1::2]


#: LIMIT меньше половины кандидатов: PostgreSQL сортирует тогда ограниченной
#: кучей (top-N), и ничьи на границе режутся неустойчиво.
TOP = 4


async def test_topic_gaps_ties_same_under_any_plan(db):
    """Темы с равными долей и числом сдач на границе LIMIT — по id темы."""
    courses = [await _course(db, f"тема {i}") for i in range(TIES)]
    student = await _user(db, role="student")
    first_ids = []
    for i, course in enumerate(courses):
        task = await _task(db, course)
        first_ids.append(await _result(db, student, task, at=SAME, correct=False))
        await _result(db, student, task, at=SAME, correct=i % 2 == 1)
    await move_to_heap_end(db, "task_results", first_ids[0])
    await db.commit()

    async def call() -> list[int]:
        gaps = await learning_gaps_service.find_topic_gaps(
            db, min_submissions=1, min_students=1, threshold=0.0, limit=TOP
        )
        return [g.course_id for g in gaps]

    assert_same(await run_under_plans(db, call), _ranked(courses)[:TOP])


async def test_ai_authorship_ties_same_under_any_plan(db):
    """Равное число работ с признаком ИИ на границе LIMIT — по id ученика."""
    course = await _course(db, "авторство")
    task = await _task(db, course)
    review = {"status": "done", "signals": ["paste"]}
    students = []
    first_ids = []
    for i in range(TIES):
        student = await _user(db, role="student")
        await _enroll(db, student, course)
        first_ids.append(await _result(db, student, task, at=SAME, code_review=review))
        if i % 2 == 0:
            await _result(db, student, task, at=SAME, code_review=review)
        students.append(student)
    await move_to_heap_end(db, "task_results", first_ids[0])
    await db.commit()

    async def call() -> list[int]:
        rows = await sig.find_ai_authorship_gaps(
            db, days=30, min_flagged=1, min_share=0.0, limit=TOP
        )
        return [r["student_id"] for r in rows]

    assert_same(await run_under_plans(db, call), _ranked(students)[:TOP])


async def test_dropout_risk_ties_same_under_any_plan(db):
    """«Затих» при одинаковом моменте последней своей сдачи на границе LIMIT —
    по id ученика."""
    course = await _course(db, "затих")
    task = await _task(db, course)
    teacher = await _user(db, role="teacher")
    silent_since = NOW - timedelta(days=30)
    students = []
    for i in range(TIES):
        student = await _user(db, role="student")
        await _enroll(db, student, course)
        await _result(db, student, task, at=silent_since + timedelta(hours=i % 2))
        occ = int(
            (
                await db.execute(
                    text(
                        "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                        "VALUES (:t, :ts, 60) RETURNING id"
                    ),
                    {"t": teacher, "ts": NOW - timedelta(days=3)},
                )
            ).scalar_one()
        )
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
                "VALUES (:o, :s, 'no_show')"
            ),
            {"o": occ, "s": student},
        )
        students.append(student)
    await move_to_heap_end(db, "users", students[0])
    await db.commit()

    async def call() -> list[int]:
        rows = await sig.find_dropout_risk(db, days=14, limit=TOP)
        return [r["student_id"] for r in rows]

    assert_same(await run_under_plans(db, call), _ranked(students)[:TOP])


async def test_dropout_by_activity_ties_same_under_any_plan(db):
    """«Пропал» без расписания при равной последней активности на границе
    LIMIT — по id ученика."""
    course = await _course(db, "пропал")
    task = await _task(db, course)
    silent_since = NOW - timedelta(days=30)
    students = []
    for i in range(TIES):
        student = await _user(db, role="student")
        await _enroll(db, student, course)
        await _result(db, student, task, at=silent_since + timedelta(hours=i % 2))
        students.append(student)
    await move_to_heap_end(db, "users", students[0])
    await db.commit()

    async def call() -> list[int]:
        rows = await sig.find_dropout_risk_by_activity(db, days=14, limit=TOP)
        return [r["student_id"] for r in rows]

    assert_same(await run_under_plans(db, call), _ranked(students)[:TOP])


async def test_signal_feed_ties_same_under_any_plan(db):
    """Лента сигналов: равные сила и created_at (один проход) — новее по id."""
    course = await _course(db, "лента")
    signal_ids = []
    for _ in range(5):
        student = await _user(db)
        res = await db.execute(
            text(
                "INSERT INTO learning_gap_signal (course_id, student_id, submissions, students, "
                " wrong_rate, reason) VALUES (:c, :s, 8, 1, 1.0, 'error_rate') RETURNING id"
            ),
            {"c": course, "s": student},
        )
        signal_ids.append(int(res.scalar_one()))
    await move_to_heap_end(db, "learning_gap_signal", signal_ids[-1])
    await db.commit()

    async def call() -> list[int]:
        rows = await sig.list_signals(db, limit=100000)
        return [r["id"] for r in rows if r["id"] in signal_ids]

    assert_same(await run_under_plans(db, call), sorted(signal_ids, reverse=True))


# ─── Разбор результатов, персональный уровень, освоение темы, дубли ─────────


async def test_detail_lessons_ties_same_under_any_plan(db):
    """Уроки с равным временем начала — в порядке id их заданий."""
    root = await _course(db, "разбор")
    lessons = [await _course(db, f"урок {i}") for i in range(TIES)]
    student = await _user(db)
    attempt = await _attempt(db, student, root)
    result_ids = []
    for lesson in lessons:
        task = await _task(db, lesson)
        result_ids.append(await _result(db, student, task, at=SAME, attempt_id=attempt))
    await move_to_heap_end(db, "task_results", result_ids[0])
    await db.commit()
    service = TaskResultsService()

    async def call() -> list[int]:
        detail = await service.get_detail_by_user(db, student)
        return [lesson["lesson_id"] for lesson in detail["lessons"]]

    assert_same(await run_under_plans(db, call), lessons)


async def test_requirement_reason_tie_same_under_any_plan(db):
    """Подкурс у двух родителей с правилами одной сверки: причина — от строки
    с бОльшим id (равные глубина и created_at)."""
    parents = [await _course(db, f"родитель {i}") for i in (1, 2)]
    child = await _course(db, "общий подкурс")
    for parent in parents:
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
                "VALUES (:c, :p, 1)"
            ),
            {"c": child, "p": parent},
        )
    task = await _task(db, child)
    student = await _user(db)
    override_ids = []
    for parent, code in zip(parents, ("program_trim", "new_content")):
        res = await db.execute(
            text(
                "INSERT INTO student_requirement_override (student_id, target_kind, target_id, "
                " level, reason_code, reason_text, source, rule_version) "
                "VALUES (:s, 'course', :c, 'recommended', :code, :txt, 'rule', 1) RETURNING id"
            ),
            {"s": student, "c": parent, "code": code, "txt": f"причина {code}"},
        )
        override_ids.append(int(res.scalar_one()))
    await move_to_heap_end(db, "student_requirement_override", override_ids[-1])
    await db.commit()

    async def call() -> str:
        levels = await resolve_levels(db, student)
        return levels.reasons[("task", task)].reason_code

    assert_same(await run_under_plans(db, call), "new_content")


async def test_topic_pace_tie_same_under_any_plan(db):
    """Две сдачи темы в один момент: реальный промежуток — сдаче с меньшим id,
    нулевой — с бОльшим."""
    course = await _course(db, "темп")
    tasks = [await _task(db, course, position=i) for i in (1, 2, 3)]
    student = await _user(db)
    await _result(db, student, tasks[0], at=SAME - timedelta(seconds=60))
    second = await _result(db, student, tasks[1], at=SAME)
    await _result(db, student, tasks[2], at=SAME)
    await move_to_heap_end(db, "task_results", second)
    await db.commit()

    async def call() -> list:
        rows = await topic_mastery_service.topic_tasks(db, course_id=course)
        return [(r["task_id"], r["median_pace_seconds"]) for r in rows]

    assert_same(
        await run_under_plans(db, call),
        [(tasks[0], None), (tasks[1], 60.0), (tasks[2], 0.0)],
    )


async def test_topic_students_ties_same_under_any_plan(db):
    """Ученики темы с равными долей и числом сдач — по id."""
    course = await _course(db, "ученики темы")
    task = await _task(db, course)
    students = []
    first_ids = []
    for i in range(TIES):
        student = await _user(db)
        first_ids.append(await _result(db, student, task, at=SAME, correct=i % 2 == 1))
        students.append(student)
    await move_to_heap_end(db, "task_results", first_ids[0])
    await db.commit()

    async def call() -> list[int]:
        rows = await topic_mastery_service.topic_students(db, course_id=course)
        return [r["student_id"] for r in rows]

    assert_same(await run_under_plans(db, call), _ranked(students))


async def test_duplicate_pairs_same_under_any_plan(db):
    """Кандидаты в дубли: в паре user_a — меньший id, равные пары — по id."""
    name = f"Дублев{uuid4().hex[:6]} Тестовый"
    users = [await _user(db, name) for _ in range(3)]
    await move_to_heap_end(db, "users", users[0])
    await db.commit()

    async def call() -> list[tuple[int, int]]:
        pairs = await users_dedup_service.find_duplicate_candidates(db)
        return [(c.user_a_id, c.user_b_id) for c in pairs if c.user_a_id in users]

    expected = [(users[0], users[1]), (users[0], users[2]), (users[1], users[2])]
    assert_same(await run_under_plans(db, call), expected)
