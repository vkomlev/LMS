"""tsk-886: курс вне работы (`courses.is_active`) — поведение за признаком.

Признак завёл tsk-873, поведения за ним не было никакого: выключенный курс так
же предлагался в формах, принимал зачисление и попадал в подбор домашней
работы. Тесты держат обе стороны границы:

* **новую связь заводить нельзя** — зачисление (три пути), закрепление
  преподавателя, начало попытки;
* **уже существующее не трогаем** — карточка курса читается, открытая попытка
  возвращается, зачисленный ученик остаётся зачисленным.

Плюс отбор: выключенный курс не показывается там, где курс ВЫБИРАЮТ
(`GET /teacher/courses/search`), и не попадает в подбор домашней работы.
Возврат курса в работу (`is_active=true`) снимает всё разом.

Работают под общей откатываемой транзакцией (`db` + `client` на одном
соединении, tsk-333): своего движка модуль НЕ заводит — иначе его уборка встала
бы в блокировку за транзакцией теста (клинч, описанный в `conftest.py` над
`SELF_MANAGED_CONNECTION_MODULES`). Ничего чистить руками не нужно: откат
внешней транзакции уносит все вставки, даже если тест упал посередине.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.main import app
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.services import course_activity_service, homework_service

pytestmark = pytest.mark.asyncio

_settings = Settings()
_DENY = course_activity_service.INACTIVE_DETAIL


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest.fixture
def student_auth():
    """Подменить `get_current_user` на обычного ученика (без расширенной роли)."""

    def _apply(student_id: int):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=student_id, is_service=False
        )

    yield _apply
    app.dependency_overrides.pop(get_current_user, None)


async def _make_graph(db) -> dict:
    """Два корневых курса (в работе и вне работы) с заданиями, ученики, учитель.

    Метка `tsk886-<uuid>` в названии нужна тесту поиска: он должен находить
    ровно свои курсы, а не чужие с похожим словом.
    """
    mark = f"tsk886-{uuid.uuid4().hex[:10]}"
    ids: dict[str, int | str] = {"mark": mark}

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — задание не собрать"

    async def _course(suffix: str, *, is_active: bool) -> int:
        return int(
            (
                await db.execute(
                    text(
                        "INSERT INTO courses (title, access_level, is_active) "
                        "VALUES (:t, 'self_guided', :a) RETURNING id"
                    ),
                    {"t": f"{mark} {suffix}", "a": is_active},
                )
            ).scalar()
        )

    async def _task(course_id: int) -> int:
        return int(
            (
                await db.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_attempts, order_position) "
                        "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                        ":uid, 10, 1) RETURNING id"
                    ),
                    {
                        "tc": (
                            '{"type":"SC","stem":"2+2?","options":['
                            '{"id":"a","text":"3"},{"id":"b","text":"4"}]}'
                        ),
                        "sr": '{"max_score":1,"correct_options":["b"]}',
                        "cid": course_id,
                        "did": difficulty_id,
                        "uid": f"{mark}-{uuid.uuid4().hex[:8]}",
                    },
                )
            ).scalar()
        )

    ids["course_on"] = await _course("в работе", is_active=True)
    ids["course_off"] = await _course("вне работы", is_active=False)
    ids["task_on"] = await _task(int(ids["course_on"]))
    ids["task_off"] = await _task(int(ids["course_off"]))

    async def _user(name: str) -> int:
        return int(
            (
                await db.execute(
                    text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
                    {"n": f"{mark} {name}"},
                )
            ).scalar()
        )

    ids["student"] = await _user("ученик")
    # Ученик УЖЕ на обоих курсах: курс выключили ПОСЛЕ зачисления — именно тот
    # случай, ради которого «уже существующее» не трогают.
    for key in ("course_on", "course_off"):
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id, is_active) "
                "VALUES (:u, :c, true)"
            ),
            {"u": ids["student"], "c": ids[key]},
        )

    ids["student_free"] = await _user("ученик без курсов")
    ids["teacher"] = await _user("учитель")
    teacher_role_id = (
        await db.execute(text("SELECT id FROM roles WHERE name = 'teacher' LIMIT 1"))
    ).scalar()
    if teacher_role_id is not None:
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": ids["teacher"], "r": teacher_role_id},
        )

    await db.commit()
    return ids


@pytest.fixture
async def graph(db):
    """Граф задачи. Уборки нет намеренно — её делает откат общей транзакции."""
    return await _make_graph(db)


# ---------------------------------------------------------------------------
# Признак наружу
# ---------------------------------------------------------------------------


async def test_course_read_exposes_is_active(client, graph):
    """Клиент видит признак: до tsk-886 `CourseRead` про него не знал вовсе."""
    off = await client.get(
        f"/api/v1/courses/{graph['course_off']}", headers=_service_headers()
    )
    on = await client.get(
        f"/api/v1/courses/{graph['course_on']}", headers=_service_headers()
    )
    assert off.status_code == 200, off.text
    assert off.json()["is_active"] is False, off.text
    assert on.json()["is_active"] is True, on.text


async def test_patch_card_toggles_is_active(client, db, graph):
    """Методист снимает и возвращает признак с карточки курса."""
    course_id = graph["course_on"]

    off = await client.patch(
        f"/api/v1/courses/{course_id}/card",
        json={"is_active": False},
        headers=_service_headers(),
    )
    assert off.status_code == 200, off.text
    assert off.json()["is_active"] is False, off.text

    on = await client.patch(
        f"/api/v1/courses/{course_id}/card",
        json={"is_active": True},
        headers=_service_headers(),
    )
    assert on.status_code == 200, on.text
    assert on.json()["is_active"] is True, on.text
    assert (
        await db.execute(
            text("SELECT is_active FROM courses WHERE id = :c"), {"c": course_id}
        )
    ).scalar() is True


async def test_patch_card_does_not_touch_exam_and_service(client, db, graph):
    """Граница задачи: `is_exam` / `is_service` карточкой не правятся."""
    resp = await client.patch(
        f"/api/v1/courses/{graph['course_on']}/card",
        json={"is_exam": True, "is_service": True},
        headers=_service_headers(),
    )
    # Лишние поля Pydantic отбрасывает — 200, но в базе ничего не изменилось.
    assert resp.status_code == 200, resp.text
    row = (
        await db.execute(
            text("SELECT is_exam, is_service FROM courses WHERE id = :c"),
            {"c": graph["course_on"]},
        )
    ).fetchone()
    assert tuple(row) == (False, False), row


# ---------------------------------------------------------------------------
# Новую связь заводить нельзя
# ---------------------------------------------------------------------------


async def test_enroll_denied_on_inactive_course(client, db, graph):
    """`POST /user-courses/` — прямое зачисление отказывает 409."""
    resp = await client.post(
        "/api/v1/user-courses/",
        json={"user_id": graph["student_free"], "course_id": graph["course_off"]},
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM user_courses "
                "WHERE user_id = :u AND course_id = :c"
            ),
            {"u": graph["student_free"], "c": graph["course_off"]},
        )
    ).scalar() == 0, "при отказе связь писаться не должна"


async def test_bulk_enroll_denied_on_inactive_course(client, db, graph):
    """`POST /users/{id}/courses/bulk` — пачка отказывает целиком."""
    resp = await client.post(
        f"/api/v1/users/{graph['student_free']}/courses/bulk",
        json={"course_ids": [graph["course_on"], graph["course_off"]]},
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert (
        await db.execute(
            text("SELECT COUNT(*) FROM user_courses WHERE user_id = :u"),
            {"u": graph["student_free"]},
        )
    ).scalar() == 0, "отказ по одному курсу не должен зачислять на соседний"


async def test_bulk_enroll_passes_for_active_courses(client, db, graph):
    """Обратная сторона: курс в работе пачка принимает как раньше."""
    resp = await client.post(
        f"/api/v1/users/{graph['student_free']}/courses/bulk",
        json={"course_ids": [graph["course_on"]]},
        headers=_service_headers(),
    )
    assert resp.status_code == 201, resp.text
    assert (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM user_courses "
                "WHERE user_id = :u AND course_id = :c"
            ),
            {"u": graph["student_free"], "c": graph["course_on"]},
        )
    ).scalar() == 1


async def test_teacher_manual_assign_denied(client, graph):
    """`POST /teacher/students/{id}/assignments` — третий путь зачисления."""
    resp = await client.post(
        f"/api/v1/teacher/students/{graph['student_free']}/assignments",
        json={"course_id": graph["course_off"]},
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_teacher_manual_assign_idempotent_on_enrolled(client, graph):
    """Уже зачисленного ученика повторный вызов не ломает: новой связи нет.

    Отказ здесь означал бы, что «уже существующее» перестало читаться, — а это
    ровно то, что задача запрещает трогать.
    """
    resp = await client.post(
        f"/api/v1/teacher/students/{graph['student']}/assignments",
        json={"course_id": graph["course_off"]},
        headers=_service_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["already_enrolled"] is True, resp.text


async def test_teacher_course_link_denied(client, db, graph):
    """Закрепление преподавателя за курсом вне работы — 409, а не 404."""
    resp = await client.post(
        f"/api/v1/courses/{graph['course_off']}/teachers/{graph['teacher']}",
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM teacher_courses "
                "WHERE teacher_id = :t AND course_id = :c"
            ),
            {"t": graph["teacher"], "c": graph["course_off"]},
        )
    ).scalar() == 0


async def test_teacher_course_link_allowed_on_active(client, graph):
    """Курс в работе закрепляется как раньше — запрет не задел соседа."""
    resp = await client.post(
        f"/api/v1/courses/{graph['course_on']}/teachers/{graph['teacher']}",
        headers=_service_headers(),
    )
    assert resp.status_code == 204, resp.text


async def test_start_attempt_denied_on_inactive_course(client, db, graph, student_auth):
    """Новую попытку в курсе вне работы ученик не начинает."""
    student_auth(graph["student"])
    resp = await client.post(
        f"/api/v1/learning/tasks/{graph['task_off']}/start-or-get-attempt",
        json={"student_id": graph["student"]},
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text
    assert (
        await db.execute(
            text("SELECT COUNT(*) FROM attempts WHERE user_id = :u"),
            {"u": graph["student"]},
        )
    ).scalar() == 0, "при отказе попытка создаваться не должна"


async def test_start_attempt_allowed_on_active_course(client, graph, student_auth):
    """Соседний курс в работе открывается как раньше."""
    student_auth(graph["student"])
    resp = await client.post(
        f"/api/v1/learning/tasks/{graph['task_on']}/start-or-get-attempt",
        json={"student_id": graph["student"]},
    )
    assert resp.status_code == 200, resp.text


async def test_create_attempt_endpoint_denied(client, graph):
    """Вторая дверь того же действия — `POST /attempts` — закрыта так же.

    Отсечка по ВЛАДЕЛЬЦУ попытки: сервисный ключ (боты TG_LMS) её не обходит,
    иначе запрет снимался бы сменой клиента (урок tsk-617/tsk-673/tsk-701).
    """
    resp = await client.post(
        "/api/v1/attempts",
        json={
            "user_id": graph["student"],
            "course_id": graph["course_off"],
            "source_system": "test_tsk886",
        },
        headers=_service_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert _DENY in resp.text


async def test_started_attempt_survives(client, db, graph, student_auth):
    """Уже начатая попытка возвращается: ученик дорешает открытое."""
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, "
                    "source_system) VALUES (:u, :c, :c, 'test_tsk886') RETURNING id"
                ),
                {"u": graph["student"], "c": graph["course_off"]},
            )
        ).scalar()
    )
    await db.commit()

    student_auth(graph["student"])
    resp = await client.post(
        f"/api/v1/learning/tasks/{graph['task_off']}/start-or-get-attempt",
        json={
            "student_id": graph["student"],
            "root_course_id": graph["course_off"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["attempt_id"] == attempt_id, resp.text


async def test_reading_existing_enrollment_still_works(client, graph):
    """Курс ученика виден в его карточке — прошлое обязано объясняться."""
    resp = await client.get(
        f"/api/v1/users/{graph['student']}/courses?role=student",
        headers=_service_headers(),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["courses"]
    assert graph["course_off"] in {c["course_id"] for c in rows}, resp.text
    off = next(c for c in rows if c["course_id"] == graph["course_off"])
    assert off["course"]["is_active"] is False, off


# ---------------------------------------------------------------------------
# Отбор: где курс ВЫБИРАЮТ
# ---------------------------------------------------------------------------


async def test_teacher_course_search_hides_inactive(client, graph):
    """Форма выбора курса для назначения выключенный курс не предлагает."""
    resp = await client.get(
        f"/api/v1/teacher/courses/search?q={graph['mark']}",
        headers=_service_headers(),
    )
    assert resp.status_code == 200, resp.text
    found = {c["id"] for c in resp.json()}
    assert graph["course_on"] in found, resp.text
    assert graph["course_off"] not in found, "курс вне работы в форме выбора не место"


async def test_homework_pick_skips_inactive_course(db, graph):
    """Подбор домашней работы обходит курс вне работы стороной."""
    items = await homework_service._next_items(db, student_id=graph["student"], limit=20)
    picked = {(i["kind"], i["item_id"]) for i in items}
    assert ("task", graph["task_on"]) in picked, picked
    assert ("task", graph["task_off"]) not in picked, "выключенный курс не задают на дом"


# ---------------------------------------------------------------------------
# Возврат в работу снимает всё разом
# ---------------------------------------------------------------------------


async def test_reactivation_restores_everything(client, db, graph):
    """Включили обратно — зачисление проходит, курс снова в форме и в подборе."""
    await db.execute(
        text("UPDATE courses SET is_active = true WHERE id = :c"),
        {"c": graph["course_off"]},
    )
    await db.commit()

    enroll = await client.post(
        "/api/v1/user-courses/",
        json={"user_id": graph["student_free"], "course_id": graph["course_off"]},
        headers=_service_headers(),
    )
    assert enroll.status_code == 201, enroll.text

    search = await client.get(
        f"/api/v1/teacher/courses/search?q={graph['mark']}",
        headers=_service_headers(),
    )
    assert graph["course_off"] in {c["id"] for c in search.json()}, search.text

    items = await homework_service._next_items(db, student_id=graph["student"], limit=20)
    assert ("task", graph["task_off"]) in {
        (i["kind"], i["item_id"]) for i in items
    }, items
