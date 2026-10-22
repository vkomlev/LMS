"""tsk-996, хвосты прочёски: четыре места, оставленные 22.10 на решение оператора.

1. Лента преподавателя: составной курсор ``cursor`` — события с тем же временем
   на стыке страниц не теряются и не повторяются; старый ``before`` работает.
2. Тариф без оси: не больше одного действующего на группу (индекс
   ``uq_pricing_tariff_single_active``), дубль — 409, миграция с дублями
   отказывает понятной ошибкой.
3. ``GET /courses/{id}/tree``: дети узла в порядке ``/children``.
4. Ответы bulk-привязки и reorder курсов ученика — в порядке
   ``GET /users/{id}/courses``.

Приём тот же, что в ``test_tsk996_order_g*.py``: ничья по ключу, строки в куче
не по порядку, вызов под всеми планами (``run_under_plans``).
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth.current_user import CurrentUser
from app.models.users import Users
from app.repos.courses_repo import CoursesRepository
from app.repos.user_courses_repo import UserCoursesRepository
from app.services import pricing_service
from app.services import teacher_activity_feed_service as feed
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from tests.plan_order import assert_same, move_to_heap_end, run_under_plans

pytestmark = pytest.mark.asyncio

_TAG = "t996f"
#: Общий момент «одной пачки» — у всех событий ленты время одно.
_TS = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
_SERVICE = CurrentUser(id=0, is_service=True)


# ── фабрики ──────────────────────────────────────────────────────────────────

async def _user(db: Any, name: str, *, role: str | None = None) -> int:
    u = Users(
        email=f"{_TAG}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"{_TAG} {name}", tg_id=None,
    )
    db.add(u)
    await db.flush()
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    return u.id


async def _token(db: Any, user_id: int) -> str:
    email = f"{_TAG}-s{user_id}@example.com"
    await identity_link_service.upsert_identity(db, user_id, "email", email)
    token, _, _ = await create_session(db, user_id=user_id)
    return token


async def _course(db: Any, title: str = "курс") -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG} {title} {random.randint(10**6, 10**8)}"},
        )
    ).scalar_one()


async def _task(db: Any, course_id: int) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
                "course_id, difficulty_id) "
                "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :course, "
                "        (SELECT MIN(id) FROM difficulties)) RETURNING id"
            ),
            {
                "e": f"{_TAG}-{random.randint(10**8, 10**10)}",
                "c": json.dumps({"type": "SA", "stem": "x", "title": "задание"}),
                "r": json.dumps({"max_score": 10}),
                "course": course_id,
            },
        )
    ).scalar_one()


async def _material(db: Any, course_id: int) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO materials (course_id, title, type, content, order_position) "
                "VALUES (:c, :t, 'text', CAST(:content AS jsonb), 1) RETURNING id"
            ),
            {"c": course_id, "t": f"{_TAG} материал", "content": json.dumps({"body": "x"})},
        )
    ).scalar_one()


# ── 1. лента преподавателя: составной курсор ─────────────────────────────────

async def _feed_graph(db: Any) -> tuple[int, int]:
    """Ученик с девятью событиями трёх источников в одну и ту же секунду + одно старше.

    Строки вставляются не по порядку ключа и перекладываются в конец кучи —
    чтобы порядок внутри ничьей решал не план, а ключ.
    """
    student = await _user(db, "ученик")
    course = await _course(db)
    attempt = (
        await db.execute(
            text("INSERT INTO attempts (user_id) VALUES (:u) RETURNING id"), {"u": student}
        )
    ).scalar_one()
    tasks = [await _task(db, course) for _ in range(4)]
    result_ids = []
    for task in tasks:
        result_ids.append(
            (
                await db.execute(
                    text(
                        "INSERT INTO task_results (score, user_id, task_id, submitted_at, "
                        " count_retry, received_at, max_score, source_system, is_correct, "
                        " attempt_id) "
                        "VALUES (0, :u, :t, :ts, 0, :ts, 10, 'spw', true, :a) RETURNING id"
                    ),
                    {"u": student, "t": task, "ts": _TS, "a": attempt},
                )
            ).scalar_one()
        )
    help_ids = []
    for task in tasks[:3]:
        help_ids.append(
            (
                await db.execute(
                    text(
                        "INSERT INTO help_requests (status, request_type, student_id, task_id, "
                        " course_id, created_at, updated_at) "
                        "VALUES ('open', 'manual_help', :s, :t, :c, :ts, :ts) RETURNING id"
                    ),
                    {"s": student, "t": task, "c": course, "ts": _TS},
                )
            ).scalar_one()
        )
    materials = [await _material(db, course) for _ in range(2)]
    for material in materials:
        await db.execute(
            text(
                "INSERT INTO student_material_progress "
                "  (student_id, material_id, status, completed_at, source) "
                "VALUES (:s, :m, 'completed', :ts, 'system')"
            ),
            {"s": student, "m": material, "ts": _TS},
        )
    # Простой на занятии в ту же секунду — стык material_studied → student_idle.
    teacher = await _user(db, "преподаватель")
    occurrence = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, "
                " duration_minutes) VALUES (NULL, :t, :at, 90) RETURNING id"
            ),
            {"t": teacher, "at": _TS - timedelta(minutes=30)},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_idle_episode (occurrence_id, student_id, kind, context, "
            " course_id, silent_since, detected_at) "
            "VALUES (:o, :s, 'idle', 'cabinet', :c, :since, :ts)"
        ),
        {
            "o": occurrence, "s": student, "c": course,
            "since": _TS - timedelta(minutes=5), "ts": _TS,
        },
    )
    # Событие старше пачки — проверка, что после ничьей лента идёт дальше.
    await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            " received_at, max_score, source_system, is_correct, attempt_id) "
            "VALUES (0, :u, :t, :ts, 0, :ts, 10, 'spw', false, :a)"
        ),
        {"u": student, "t": tasks[0], "ts": _TS - timedelta(hours=1), "a": attempt},
    )
    await move_to_heap_end(db, "task_results", min(result_ids))
    await move_to_heap_end(db, "help_requests", min(help_ids))
    await db.commit()
    return student, len(result_ids) + len(help_ids) + len(materials) + 2


def _ident(event: dict) -> tuple:
    return (event["type"], event["task_id"], event["material_id"], event["outcome"])


async def _walk(db: Any, student: int, size: int) -> list[tuple]:
    """Пройти ленту страницами по ``next_cursor`` до пустой страницы."""
    out: list[tuple] = []
    cursor: str | None = None
    for _ in range(50):
        page, has_more, _, next_cursor = await feed.get_activity_feed(
            db, _SERVICE, limit=size, student_id=student,
            cursor=feed.FeedCursor.decode(cursor) if cursor else None,
        )
        if not page:
            return out
        out.extend(_ident(e) for e in page)
        cursor = next_cursor
        if not has_more:
            # Последняя страница: дальше событий быть не должно.
            rest, _, _, _ = await feed.get_activity_feed(
                db, _SERVICE, limit=size, student_id=student,
                cursor=feed.FeedCursor.decode(cursor) if cursor else None,
            )
            assert rest == []
            return out
    raise AssertionError("лента не кончилась за 50 страниц")


async def test_feed_cursor_pages_tie_no_loss_no_dupes(db):
    """Ничья по времени на стыке страниц: все события ровно по одному разу.

    Без составного курсора страница размера 2 обрывала пачку из девяти событий
    с одинаковым временем: ``before`` строгий, остальные семь терялись.
    """
    student, total = await _feed_graph(db)
    full, _, _, _ = await feed.get_activity_feed(db, _SERVICE, limit=100, student_id=student)
    expected = [_ident(e) for e in full]
    assert len(expected) == total

    for size in (1, 2, 3, 4):
        seen = await run_under_plans(db, lambda size=size: _walk(db, student, size))
        assert_same(seen, expected)


async def test_feed_order_is_total_inside_tie(db):
    """Внутри ничьей: task_solved → help_requested → material_studied, ключ по убыванию."""
    student, _ = await _feed_graph(db)
    page, _, _, _ = await feed.get_activity_feed(db, _SERVICE, limit=100, student_id=student)
    types = [e["type"] for e in page]
    assert types == (
        ["task_solved"] * 4 + ["help_requested"] * 3 + ["material_studied"] * 2
        + ["student_idle", "task_solved"]
    )
    tasks = [e["task_id"] for e in page[:4]]
    assert tasks == sorted(tasks, reverse=True)
    materials = [e["material_id"] for e in page[7:9]]
    assert materials == sorted(materials, reverse=True)
    assert all("_key" not in e for e in page)


async def test_feed_legacy_before_still_strict(db):
    """Старый ``before`` работает как раньше: только события строго раньше момента."""
    student, _ = await _feed_graph(db)
    page, _, next_before, next_cursor = await feed.get_activity_feed(
        db, _SERVICE, limit=100, student_id=student, before=_TS,
    )
    assert [e["outcome"] for e in page] == ["incorrect"]
    assert next_before == _TS - timedelta(hours=1)
    assert next_cursor is not None


async def test_feed_cursor_roundtrip_and_validation():
    """Курсор переживает кодирование; мусор, наивное время и чужой ключ — ValueError."""
    cur = feed.FeedCursor(timestamp=_TS, source="material_studied", key=(5, 7))
    assert feed.FeedCursor.decode(cur.encode()) == cur
    naive = feed.FeedCursor(timestamp=_TS.replace(tzinfo=None), source="task_solved", key=(1,))
    bad_key = feed.FeedCursor(timestamp=_TS, source="task_solved", key=(1, 2))
    bad_source = feed.FeedCursor(timestamp=_TS, source="nope", key=(1,))
    import base64

    def raw_cursor(t: str, src: Any, key: Any) -> str:
        data = json.dumps({"t": t, "s": src, "k": key}).encode()
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    forged = [
        raw_cursor(_TS.isoformat(), [], [1]),  # источник не строка
        raw_cursor(_TS.isoformat(), "task_solved", [2**31]),  # вне integer
        raw_cursor(_TS.isoformat(), "task_solved", [0]),
        raw_cursor("0001-01-01T00:00:00+14:00", "task_solved", [1]),  # крайняя дата
        raw_cursor("9999-12-31T23:59:59-14:00", "task_solved", [1]),
    ]
    for raw in (
        "", "@@@", "e30", naive.encode(), bad_key.encode(), bad_source.encode(), *forged,
    ):
        with pytest.raises(ValueError):
            feed.FeedCursor.decode(raw)


async def test_feed_api_cursor_contract(db, client):
    """API: ``next_cursor`` в ответе, ``cursor`` принимается; мусор и cursor+before — 422."""
    student, total = await _feed_graph(db)
    methodist = await _user(db, "методист", role="methodist")
    headers = {"Authorization": f"Bearer {await _token(db, methodist)}"}
    await db.commit()
    url = "/api/v1/teacher/activity-feed"

    seen: list[tuple] = []
    params: dict[str, Any] = {"limit": 2, "student_id": student}
    for _ in range(20):
        resp = await client.get(url, params=params, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if not body["events"]:
            break
        seen.extend(_ident(e) for e in body["events"])
        assert body["next_cursor"]
        params = {"limit": 2, "student_id": student, "cursor": body["next_cursor"]}
    assert len(seen) == total == len(set(seen))

    bad = await client.get(url, params={"cursor": "мусор"}, headers=headers)
    assert bad.status_code == 422, bad.text
    forged = feed.FeedCursor(timestamp=_TS, source="task_solved", key=(2**31,))
    big = await client.get(url, params={"cursor": forged.encode()}, headers=headers)
    assert big.status_code == 422, big.text
    both = await client.get(
        url, params={"cursor": params["cursor"], "before": _TS.isoformat()}, headers=headers,
    )
    assert both.status_code == 422, both.text
    legacy = await client.get(
        url, params={"student_id": student, "before": _TS.isoformat()}, headers=headers,
    )
    assert legacy.status_code == 200, legacy.text
    assert len(legacy.json()["events"]) == 1


# ── 2. тариф без оси: один действующий на группу ─────────────────────────────

async def _group(db: Any) -> int:
    return (
        await db.execute(
            text("INSERT INTO pricing_group (name) VALUES (:n) RETURNING id"),
            {"n": f"{_TAG} группа {random.randint(10**6, 10**8)}"},
        )
    ).scalar_one()


def _tariff(group_id: int, price: int, **kw: Any) -> dict:
    return {
        "group_id": group_id, "name": f"{_TAG} {price}", "price_minor": price,
        "currency": "RUB", "period": "month", "match_kind": None, "match_value": None,
        "is_default": False, "sort_order": 0, **kw,
    }


async def test_second_active_axisless_tariff_rejected_by_db(db):
    """Второй действующий вариант без оси в группе — нарушение индекса, не тихий дубль."""
    group = await _group(db)
    await pricing_service.create_tariff(db, payload=_tariff(group, 100000))
    with pytest.raises(IntegrityError, match="uq_pricing_tariff_single_active"):
        await pricing_service.create_tariff(db, payload=_tariff(group, 200000))


async def test_inactive_and_axis_tariffs_not_affected(db):
    """Выключенный вариант без оси и варианты с осью индексу не мешают."""
    group = await _group(db)
    old = await pricing_service.create_tariff(db, payload=_tariff(group, 100000))
    await pricing_service.update_tariff(db, tariff_id=old, patch={"is_active": False})
    await pricing_service.create_tariff(db, payload=_tariff(group, 150000))
    await pricing_service.create_tariff(
        db, payload=_tariff(group, 90000, match_kind="segment", match_value="insider"),
    )
    other = await _group(db)
    await pricing_service.create_tariff(db, payload=_tariff(other, 100000))
    count = (
        await db.execute(
            text(
                "SELECT count(*) FROM pricing_tariff "
                "WHERE group_id = ANY(:g) AND is_active AND match_kind IS NULL"
            ),
            {"g": [group, other]},
        )
    ).scalar_one()
    assert count == 2


async def test_marketer_api_duplicate_axisless_is_409(db, client):
    """Кабинет маркетолога: дубль при создании и при включении — понятный 409, не 500."""
    marketer = await _user(db, "маркетолог", role="marketer")
    headers = {"Authorization": f"Bearer {await _token(db, marketer)}"}
    group = await _group(db)
    await pricing_service.create_tariff(db, payload=_tariff(group, 100000))
    spare = await pricing_service.create_tariff(
        db, payload=_tariff(group, 80000, match_kind="segment", match_value="street"),
    )

    created = await client.post(
        "/api/v1/marketer/pricing/tariffs",
        json={"group_id": group, "name": "ещё один", "price_minor": 300000},
        headers=headers,
    )
    assert created.status_code == 409, created.text
    assert "без оси" in created.json()["detail"]

    # Снять ось у второго варианта — тоже второй вариант без оси.
    patched = await client.patch(
        f"/api/v1/marketer/pricing/tariffs/{spare}",
        json={"match_kind": None, "match_value": None},
        headers=headers,
    )
    assert patched.status_code == 409, patched.text
    assert "без оси" in patched.json()["detail"]

    # Соединение живое: следующий запрос отвечает нормально.
    ok = await client.get("/api/v1/marketer/pricing/groups", headers=headers)
    assert ok.status_code == 200, ok.text


def _load_migration() -> Any:
    path = (
        Path(__file__).resolve().parents[1]
        / "app/db/migrations/versions/2026_10_22_tsk996_pricing_tariff_single_active.py"
    )
    spec = importlib.util.spec_from_file_location("tsk996_tariff_single_active", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_migration_refuses_when_duplicates_exist(db):
    """Миграция при дублях падает со списком групп и данных не трогает.

    Индекс снимается внутри транзакции теста — откат вернёт его на место.
    """
    migration = _load_migration()
    await db.execute(text("DROP INDEX uq_pricing_tariff_single_active"))
    group = await _group(db)
    first = await pricing_service.create_tariff(db, payload=_tariff(group, 100000))
    second = await pricing_service.create_tariff(db, payload=_tariff(group, 200000))

    def upgrade(sync_conn: Any) -> None:
        with Operations.context(MigrationContext.configure(sync_conn)):
            migration.upgrade()

    conn = await db.connection()
    with pytest.raises(RuntimeError, match=f"группа {group}: варианты \\[{first}, {second}\\]"):
        await conn.run_sync(upgrade)
    active = (
        await db.execute(
            text("SELECT count(*) FROM pricing_tariff WHERE group_id = :g AND is_active"),
            {"g": group},
        )
    ).scalar_one()
    assert active == 2

    # Без дублей та же миграция создаёт индекс.
    await pricing_service.update_tariff(db, tariff_id=second, patch={"is_active": False})
    await conn.run_sync(upgrade)
    assert (
        await db.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_pricing_tariff_single_active'")
        )
    ).scalar_one() == 1


# ── 3. дерево курса: дети в порядке /children ────────────────────────────────

async def _tree_graph(db: Any) -> tuple[int, list[int], list[int]]:
    """Корень → 4 ребёнка (порядок 3, NULL, 1, 2) → у первого 3 внука (2, 1, NULL).

    Номера ставятся мимо триггера нумерации, чтобы был и NULL. Строки
    ``course_parents`` вставляются по возрастанию id ребёнка, то есть куча
    идёт НЕ в порядке ``order_number``.
    """
    root = await _course(db, "корень")
    kids = [await _course(db, f"ребёнок {i}") for i in range(4)]
    grand = [await _course(db, f"внук {i}") for i in range(3)]
    await db.execute(text("SELECT set_config('app.skip_course_parent_order_trigger', 'true', true)"))
    for child, parent, order in (
        *zip(kids, [root] * 4, [3, None, 1, 2]),
        *zip(grand, [kids[0]] * 3, [2, 1, None]),
    ):
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
                "VALUES (:c, :p, :o)"
            ),
            {"c": child, "p": parent, "o": order},
        )
    await db.execute(text("SELECT set_config('app.skip_course_parent_order_trigger', 'false', true)"))
    await db.commit()
    return root, kids, grand


async def test_course_tree_children_follow_children_order(db):
    """Под любым планом дети узла — как в ``/children``: order_number NULLS LAST, id."""
    root, kids, grand = await _tree_graph(db)
    repo = CoursesRepository()

    async def tree() -> tuple[list[int], list[int]]:
        node = await repo.get_course_tree(db, root)
        first = next(c for c in node.children if c.id == kids[0])
        return [c.id for c in node.children], [c.id for c in first.children]

    expected = ([kids[2], kids[3], kids[0], kids[1]], [grand[1], grand[0], grand[2]])
    assert_same(await run_under_plans(db, tree), expected)

    children = [c.id for c, _ in await repo.get_children(db, root)]
    assert children == expected[0]


async def test_course_tree_endpoint_order(db, client):
    """API ``/courses/{id}/tree`` отдаёт тот же порядок, что ``/children``."""
    root, kids, _ = await _tree_graph(db)
    key = os.environ.get("VALID_API_KEYS", "").split(",")[0].strip()
    tree = await client.get(f"/api/v1/courses/{root}/tree", params={"api_key": key})
    assert tree.status_code == 200, tree.text
    children = await client.get(f"/api/v1/courses/{root}/children", params={"api_key": key})
    assert children.status_code == 200, children.text
    tree_ids = [c["id"] for c in tree.json()["children"]]
    assert tree_ids == [kids[2], kids[3], kids[0], kids[1]]
    assert tree_ids == [c["id"] for c in children.json()]


# ── 4. курсы ученика: ответы bulk и reorder ──────────────────────────────────

async def test_bulk_and_reorder_answer_in_get_order(db):
    """bulk и reorder отдают курсы в порядке GET /users/{id}/courses, не по course_id."""
    student = await _user(db, "ученик курсов")
    courses = [await _course(db, f"к{i}") for i in range(4)]
    await db.commit()
    repo = UserCoursesRepository()

    # Двое уже привязаны, причём старший id — раньше: триггер даст ему номер 1.
    await repo.bulk_create_user_courses(db, student, [courses[3]])
    await repo.bulk_create_user_courses(db, student, [courses[2]])

    async def get_order(ids: list[int]) -> list[int]:
        return [
            uc.course_id for uc in await repo.get_user_courses(db, student) if uc.course_id in ids
        ]

    bulk = [uc.course_id for uc in await repo.bulk_create_user_courses(db, student, courses)]
    expected = await get_order(courses)
    assert bulk == expected == [courses[3], courses[2], courses[0], courses[1]]

    async def again() -> list[int]:
        return [uc.course_id for uc in await repo.bulk_create_user_courses(db, student, courses)]

    await move_to_heap_end(db, "user_courses", courses[3], column="course_id")
    assert_same(await run_under_plans(db, again), expected)

    reordered = await repo.reorder_user_courses(
        db, student,
        [{"course_id": c, "order_number": n} for c, n in zip(courses, [4, 3, 2, 1])],
    )
    got = [(uc.course_id, uc.order_number) for uc in reordered]
    after = await get_order(courses)
    assert [c for c, _ in got] == after
    assert after != sorted(after)
    assert [n for _, n in got] == sorted(n for _, n in got)


async def test_bulk_with_repeated_course_id_is_not_500(db):
    """Повтор курса в одной пачке ([c, c]) — одна привязка, а не две вставки одного PK."""
    student = await _user(db, "ученик с повтором")
    course = await _course(db, "повтор")
    await db.commit()
    got = await UserCoursesRepository().bulk_create_user_courses(db, student, [course, course])
    assert [uc.course_id for uc in got] == [course]
