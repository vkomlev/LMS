"""tsk-996 (группа 1): выдача не зависит от плана запроса.

Каждый тест создаёт строки с РАВНЫМ ключом сортировки (вставки одной
транзакции дают один `now()`), кладёт «ожидаемо первую» строку в конец кучи
(`move_to_heap_end`) и прогоняет вызов под всеми планами `tests/plan_order.py`.
Результат обязан быть одинаковым и равным детерминированному — по id.

Поведенческие места (порядок решает, ЧТО выбрано):
* первая задача попытки (`results[0]`) — лимит попыток в ответе попытки;
* напоминания/эскалации для ботов на границе LIMIT;
* преподаватель, которому уходит заявка о помощи (`resolve_assigned_teacher`);
* обход курсов ученика движком (`get_user_courses`);
* открытая попытка, куда ляжет ответ (`_course_attempt`, start-or-get-attempt).

Списки с LIMIT/OFFSET: страницы без дублей и пропусков при равных ключах.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.main import app
from app.auth.current_user import CurrentUser
from app.models.courses import Courses
from app.repos.base import BaseRepository
from tests.plan_order import assert_same, move_to_heap_end, run_under_plans

pytestmark = pytest.mark.asyncio

SERVICE = CurrentUser(id=0, is_service=True)


# ─── Построение данных ──────────────────────────────────────────────────────


def _mark() -> str:
    return f"tsk996g1-{uuid.uuid4().hex[:10]}"


async def _scalar(db: Any, sql: str, params: Optional[dict] = None) -> Any:
    return (await db.execute(text(sql), params or {})).scalar()


async def _user(db: Any, name: str) -> int:
    return int(await _scalar(db, "INSERT INTO users (full_name) VALUES (:n) RETURNING id", {"n": name}))


async def _course(db: Any, title: str) -> int:
    return int(
        await _scalar(
            db,
            "INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id",
            {"t": title},
        )
    )


async def _task(db: Any, course_id: int, task_type: str = "SC") -> int:
    difficulty_id = await _scalar(db, "SELECT id FROM difficulties ORDER BY id LIMIT 1")
    assert difficulty_id is not None, "нет difficulties — задание не собрать"
    return int(
        await _scalar(
            db,
            "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
            "external_uid, max_attempts, order_position) VALUES (CAST(:tc AS jsonb), "
            "CAST(:sr AS jsonb), :c, :d, :u, 10, 1) RETURNING id",
            {
                "tc": (
                    f'{{"type":"{task_type}","stem":"2+2?","options":['
                    '{"id":"a","text":"3"},{"id":"b","text":"4"}]}'
                ),
                "sr": '{"max_score":1,"correct_options":["b"]}',
                "c": course_id,
                "d": difficulty_id,
                "u": _mark(),
            },
        )
    )


async def _attempt(db: Any, user_id: int, course_id: int, root_id: Optional[int]) -> int:
    return int(
        await _scalar(
            db,
            "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
            "VALUES (:u, :c, :r, 'test_tsk996') RETURNING id",
            {"u": user_id, "c": course_id, "r": root_id},
        )
    )


async def _notification(db: Any, user_id: int, kind: str) -> int:
    return int(
        await _scalar(
            db,
            "INSERT INTO notifications (user_id, kind, title, content, payload) "
            "VALUES (:u, :k, 'tsk996', 'tsk996', '{}'::jsonb) RETURNING id",
            {"u": user_id, "k": kind},
        )
    )


async def _pages(db: Any, call: Any, n: int) -> Any:
    """Все страницы по одной строке: `call(offset)` → список id."""
    out = []
    for offset in range(n):
        out.extend(await call(offset))
    return out


# ─── Поведенческие места (А) ────────────────────────────────────────────────


async def test_attempt_first_task_same_under_any_plan(db):
    """`results[0]` попытки — задание первой сдачи (меньший id результата).

    По нему `_enrich_attempt_with_learning_fields` считает лимит попыток.
    """
    from app.api.v1.attempts import _build_attempt_with_results
    from app.models.attempts import Attempts

    mark = _mark()
    course = await _course(db, mark)
    task_a, task_b = await _task(db, course), await _task(db, course)
    student = await _user(db, mark)
    attempt_id = await _attempt(db, student, course, course)
    first = None
    for task in (task_a, task_b):
        rid = int(
            await _scalar(
                db,
                "INSERT INTO task_results (score, user_id, task_id, attempt_id, max_score) "
                "VALUES (0, :u, :t, :a, 1) RETURNING id",
                {"u": student, "t": task, "a": attempt_id},
            )
        )
        first = first or rid
    await move_to_heap_end(db, "task_results", first)
    await db.commit()
    attempt = await db.get(Attempts, attempt_id)

    async def call():
        res = await _build_attempt_with_results(db, attempt)
        return [r.task_id for r in res.results]

    assert_same(await run_under_plans(db, call), [task_a, task_b])


async def test_methodist_escalations_limit_plan(db):
    """Бот методиста на границе LIMIT получает самое новое — при ничьей больший id."""
    from app.api.v1.methodist_escalations import list_pending_escalations

    methodist = await _user(db, _mark())
    ids = [await _notification(db, methodist, "review_escalated") for _ in range(3)]
    await move_to_heap_end(db, "notifications", max(ids))
    await db.commit()

    async def call():
        res = await list_pending_escalations(
            since=None, user_id=methodist, limit=2, current_user=SERVICE, db=db
        )
        return [i.id for i in res.items]

    assert_same(await run_under_plans(db, call), sorted(ids, reverse=True)[:2])


async def test_schedule_preference_reminders_limit_plan(db):
    """Напоминания о пожеланиях: старые первыми, при ничьей — меньший id."""
    from app.api.v1.schedule_preferences import list_pending_schedule_preference_reminders
    from app.services import schedule_preference_reminder_service

    student = await _user(db, _mark())
    kind = schedule_preference_reminder_service.REMINDER_KIND
    ids = [await _notification(db, student, kind) for _ in range(3)]
    await move_to_heap_end(db, "notifications", min(ids))
    await db.commit()

    async def call():
        res = await list_pending_schedule_preference_reminders(
            student_id=student, limit=2, current_user=SERVICE, db=db
        )
        return [i.id for i in res.items]

    assert_same(await run_under_plans(db, call), sorted(ids)[:2])


async def test_lesson_reminders_limit_plan(db):
    """Напоминания о занятиях одного тика крона: при ничьей — меньший id."""
    from app.api.v1.student_lesson_reminders import list_pending_lesson_reminders

    student = await _user(db, _mark())
    ids = [await _notification(db, student, "lesson_reminder") for _ in range(3)]
    await move_to_heap_end(db, "notifications", min(ids))
    await db.commit()

    async def call():
        res = await list_pending_lesson_reminders(
            student_id=student, since=None, limit=2, current_user=SERVICE, db=db
        )
        return [i.id for i in res.items]

    assert_same(await run_under_plans(db, call), sorted(ids)[:2])


async def test_assigned_teacher_from_links_plan(db):
    """Заявка о помощи уходит преподавателю с меньшим id из `student_teacher_links`."""
    from app.services.help_requests_service import resolve_assigned_teacher

    mark = _mark()
    student = await _user(db, f"{mark} ученик")
    teachers = [await _user(db, f"{mark} учитель {i}") for i in range(3)]
    for t in teachers:
        await db.execute(
            text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
            {"s": student, "t": t},
        )
    first = min(teachers)
    await move_to_heap_end(db, "student_teacher_links", first, column="teacher_id")
    await move_to_heap_end(db, "users", first)
    await db.commit()

    seen = await run_under_plans(db, lambda: resolve_assigned_teacher(db, student, None))
    assert_same(seen, first)


async def test_assigned_teacher_from_course_plan(db):
    """Без личной связи — преподаватель курса: последний привязанный, при ничьей больший id."""
    from app.services.help_requests_service import resolve_assigned_teacher
    from app.services.teacher_courses_service import TeacherCoursesService

    mark = _mark()
    course = await _course(db, mark)
    student = await _user(db, f"{mark} ученик")
    teachers = [await _user(db, f"{mark} учитель {i}") for i in range(3)]
    for t in teachers:
        await db.execute(
            text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (:t, :c)"),
            {"t": t, "c": course},
        )
    expected = max(teachers)
    await move_to_heap_end(db, "teacher_courses", expected, column="teacher_id")
    await move_to_heap_end(db, "users", expected)
    await db.commit()

    seen = await run_under_plans(db, lambda: resolve_assigned_teacher(db, student, course))
    assert_same(seen, expected)

    svc = TeacherCoursesService()
    for sort_by, order, want in (
        ("linked_at", "desc", sorted(teachers, reverse=True)),
        ("linked_at", "asc", sorted(teachers)),
        ("full_name", "asc", sorted(teachers)),
    ):
        if sort_by == "full_name":
            await db.execute(
                text("UPDATE users SET full_name = :n WHERE id = ANY(:ids)"),
                {"n": f"{mark} тёзка", "ids": teachers},
            )
            await move_to_heap_end(db, "users", min(teachers))

        async def page(offset: int, sort_by=sort_by, order=order):
            items, _ = await svc.list_teachers(
                db, course, skip=offset, limit=1, sort_by=sort_by, order=order
            )
            return [u.id for u in items]

        seen = await run_under_plans(db, lambda page=page: _pages(db, page, 3))
        assert_same(seen, want)


async def _enroll_tied(db: Any, student: int, courses: list[int]) -> None:
    """Зачислить с order_number NULL и равным added_at (триггер нумерации выключен)."""
    await db.execute(text("SELECT set_config('app.skip_order_trigger', 'true', true)"))
    for c in courses:
        await db.execute(
            text("INSERT INTO user_courses (user_id, course_id, order_number) VALUES (:u, :c, NULL)"),
            {"u": student, "c": c},
        )
    await db.execute(
        text("UPDATE user_courses SET course_id = course_id WHERE user_id = :u AND course_id = :c"),
        {"u": student, "c": min(courses)},
    )
    await db.execute(text("SELECT set_config('app.skip_order_trigger', '', true)"))


async def test_user_courses_engine_order_plan(db):
    """Курсы ученика (обход движка без корня) при равных ключах — по course_id."""
    from app.repos.user_courses_repo import UserCoursesRepository

    mark = _mark()
    courses = [await _course(db, f"{mark} {i}") for i in range(3)]
    student = await _user(db, mark)
    await _enroll_tied(db, student, courses)
    await db.commit()
    repo = UserCoursesRepository()
    for by_order in (True, False):
        async def call(by_order=by_order):
            return [uc.course_id for uc in await repo.get_user_courses(db, student, by_order)]

        assert_same(await run_under_plans(db, call), sorted(courses))


async def test_user_courses_api_lists_plan(db):
    """`GET /users/{id}/courses`: курсы ученика и преподавателя при равных ключах — по id."""
    from app.api.v1.user_courses_extra import _get_student_courses, _get_teacher_courses

    mark = _mark()
    courses = [await _course(db, f"{mark} {i}") for i in range(3)]
    student = await _user(db, f"{mark} ученик")
    await _enroll_tied(db, student, courses)
    teacher = await _user(db, f"{mark} учитель")
    for c in courses:
        await db.execute(
            text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (:t, :c)"),
            {"t": teacher, "c": c},
        )
    await move_to_heap_end(db, "courses", min(courses))
    await move_to_heap_end(db, "courses", max(courses))
    await db.commit()
    for by_order in (True, False):
        async def students(by_order=by_order):
            return [x.course_id for x in await _get_student_courses(db, student, by_order)]

        async def teachers(by_order=by_order):
            return [x.course_id for x in await _get_teacher_courses(db, teacher, by_order)]

        assert_same(await run_under_plans(db, students), sorted(courses))
        assert_same(
            await run_under_plans(db, teachers), sorted(courses, reverse=by_order)
        )


async def test_course_attempt_picks_same_open_attempt_plan(db):
    """Две открытые попытки с равным created_at: ответ повтора ложится в бо́льший id."""
    from app.services.retrieval_service import _course_attempt

    mark = _mark()
    course = await _course(db, mark)
    task = await _task(db, course)
    student = await _user(db, mark)
    ids = [await _attempt(db, student, course, course) for _ in range(2)]
    await move_to_heap_end(db, "attempts", max(ids))
    await db.commit()

    async def call():
        attempt = await _course_attempt(
            db, student_id=student, task_id=task, task_course_id=course,
            root_course_id=course, source_system="test_tsk996",
        )
        return attempt.id

    assert_same(await run_under_plans(db, call), max(ids))


async def test_start_or_get_attempt_same_open_attempt_plan(client, db):
    """`start-or-get-attempt` при двух открытых попытках возвращает бо́льший id."""
    mark = _mark()
    course = await _course(db, mark)
    task = await _task(db, course)
    student = await _user(db, mark)
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": student, "c": course},
    )
    ids = [await _attempt(db, student, course, course) for _ in range(2)]
    await move_to_heap_end(db, "attempts", max(ids))
    await db.commit()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=student)
    try:
        async def call():
            resp = await client.post(
                f"/api/v1/learning/tasks/{task}/start-or-get-attempt",
                json={"student_id": student, "root_course_id": course},
            )
            assert resp.status_code == 200, resp.text
            return resp.json()["attempt_id"]

        assert_same(await run_under_plans(db, call), max(ids))
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ─── Списки с LIMIT/OFFSET (Б) ──────────────────────────────────────────────


async def test_base_paginate_pages_plan(db):
    """`BaseRepository.paginate`: без сортировки и с неполной — страницы по id."""
    mark = _mark()
    ids = [await _course(db, mark) for _ in range(3)]
    await move_to_heap_end(db, "courses", min(ids))
    await db.commit()
    repo = BaseRepository(Courses)
    for order_by in (None, [Courses.title], [Courses.title.desc()]):
        async def page(offset: int, order_by=order_by):
            items, _ = await repo.paginate(
                db, limit=1, offset=offset, filters=[Courses.id.in_(ids)], order_by=order_by
            )
            return [c.id for c in items]

        want = sorted(ids, reverse=order_by is not None and "DESC" in str(order_by[0]))
        assert_same(await run_under_plans(db, lambda page=page: _pages(db, page, 3)), want)


async def test_course_search_pages_plan(db):
    """`/courses/search` (search_text по title): одноимённые курсы — по id."""
    from app.repos.courses_repo import CoursesRepository

    mark = _mark()
    ids = [await _course(db, mark) for _ in range(3)]
    await move_to_heap_end(db, "courses", min(ids))
    await db.commit()
    repo = CoursesRepository()

    async def by_text(offset: int):
        items = await repo.search_text(
            db, field=["title"], query=mark, limit=1, offset=offset, order_by=Courses.title
        )
        return [c.id for c in items]

    assert_same(await run_under_plans(db, lambda: _pages(db, by_text, 3)), sorted(ids))


async def test_root_course_search_pages_plan(db):
    """`/teacher/courses/search` (search_root_courses): одноимённые корни — по id."""
    from app.repos.courses_repo import CoursesRepository

    mark = _mark()
    ids = [await _course(db, mark) for _ in range(3)]
    await move_to_heap_end(db, "courses", min(ids))
    await db.commit()
    repo = CoursesRepository()

    async def roots(offset: int):
        items = await repo.search_root_courses(db, query=mark, limit=1, offset=offset)
        return [c.id for c in items]

    assert_same(await run_under_plans(db, lambda: _pages(db, roots, 3)), sorted(ids))


async def test_pending_review_queue_pages_plan(db):
    """Очередь ручной проверки: сдачи одной транзакции — по id убыв., без дублей."""
    from app.api.v1.task_results_extra import get_pending_review_results

    mark = _mark()
    course = await _course(db, mark)
    task = await _task(db, course, "TA")
    student = await _user(db, mark)
    # Частичный индекс очереди читается назад: по ctid. Правка строки здесь не
    # помогает (HOT-обновление оставляет запись индекса на месте), поэтому id
    # берём заранее и вставляем в обратном порядке: ctid растёт, id убывает.
    ids = list((await db.execute(text(
        "SELECT nextval(pg_get_serial_sequence('task_results', 'id')) FROM generate_series(1, 3)"
    ))).scalars())
    for rid in sorted(ids, reverse=True):
        await db.execute(
            text(
                "INSERT INTO task_results (id, score, user_id, task_id, max_score) "
                "VALUES (:id, 0, :u, :t, 1)"
            ),
            {"id": rid, "u": student, "t": task},
        )
    await db.commit()

    async def page(offset: int):
        res = await get_pending_review_results(
            course_id=course, user_id=None, review_kind="mandatory",
            limit=1, offset=offset, db=db, current_user=SERVICE,
        )
        return [r.id for r in res]

    assert_same(await run_under_plans(db, lambda: _pages(db, page, 3)), sorted(ids, reverse=True))


async def test_users_list_pages_plan(db):
    """`GET /users/`: тёзки и равный created_at — по id (в направлении сортировки)."""
    from sqlalchemy import asc, desc

    from app.models.users import Users
    from app.repos.users_repo import UsersRepository

    mark = _mark()
    role_name = mark.lower()
    role_id = int(await _scalar(
        db, "INSERT INTO roles (id, name) VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM roles), :n) "
        "RETURNING id", {"n": role_name},
    ))
    ids = [await _user(db, f"{mark} тёзка") for _ in range(3)]
    for u in ids:
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": u, "r": role_id}
        )
    await move_to_heap_end(db, "users", min(ids))
    await db.commit()
    repo = UsersRepository()
    for field, fn in ((Users.full_name, asc), (Users.created_at, desc)):
        async def page(offset: int, field=field, fn=fn):
            items, _ = await repo.list_with_role_filter(
                db, role_name=role_name, limit=1, offset=offset, order_by=[fn(field)]
            )
            return [u.id for u in items]

        want = sorted(ids, reverse=fn is desc)
        assert_same(await run_under_plans(db, lambda page=page: _pages(db, page, 3)), want)



async def test_users_search_pages_plan(db):
    """`/users/search`: тёзки — по id."""
    from app.repos.users_repo import UsersRepository

    mark = _mark()
    ids = [await _user(db, f"{mark} тёзка") for _ in range(3)]
    await move_to_heap_end(db, "users", min(ids))
    await db.commit()
    repo = UsersRepository()

    async def search(offset: int):
        items = await repo.search_by_full_name_with_role(db, q=mark, limit=1, offset=offset)
        return [u.id for u in items]

    assert_same(await run_under_plans(db, lambda: _pages(db, search, 3)), sorted(ids))


async def test_course_users_pages_plan(db):
    """`/courses/{id}/users`: равный added_at (массовое зачисление) — по user_id."""
    from app.repos.user_courses_repo import UserCoursesRepository

    mark = _mark()
    course = await _course(db, mark)
    students = [await _user(db, f"{mark} ученик") for _ in range(3)]
    for s in students:
        await db.execute(
            text("INSERT INTO user_courses (user_id, course_id) VALUES (:u, :c)"),
            {"u": s, "c": course},
        )
    await db.execute(text("SELECT set_config('app.skip_order_trigger', 'true', true)"))
    await db.execute(
        text("UPDATE user_courses SET course_id = course_id WHERE user_id = :u AND course_id = :c"),
        {"u": min(students), "c": course},
    )
    await db.execute(text("SELECT set_config('app.skip_order_trigger', '', true)"))
    await db.commit()

    async def course_users(offset: int):
        rows = await UserCoursesRepository().get_course_users(db, course, limit=1, offset=offset)
        return [r.user_id for r in rows]

    assert_same(await run_under_plans(db, lambda: _pages(db, course_users, 3)), sorted(students))


async def test_teacher_course_links_pages_plan(db):
    """`GET /teacher-courses/`: равный linked_at — по (teacher_id, course_id)."""
    from app.repos.teacher_courses_repo import TeacherCoursesRepository

    mark = _mark()
    course = await _course(db, mark)
    teachers = [await _user(db, f"{mark} учитель") for _ in range(3)]
    # Индекс (course_id, linked_at) отдаёт ничьи по ctid, а правка строки
    # индекс не двигает (HOT) — поэтому вставляем в обратном порядке id.
    for t in sorted(teachers, reverse=True):
        await db.execute(
            text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (:u, :c)"),
            {"u": t, "c": course},
        )
    await db.commit()

    for order in ("asc", "desc"):
        async def links(offset: int, order=order):
            rows = await TeacherCoursesRepository().list(
                db, skip=offset, limit=1, course_id=course, order=order
            )
            return [r[0] for r in rows]

        assert_same(
            await run_under_plans(db, lambda links=links: _pages(db, links, 3)),
            sorted(teachers, reverse=order == "desc"),
        )
