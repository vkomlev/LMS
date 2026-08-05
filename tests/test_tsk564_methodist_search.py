"""tsk-564: глобальный поиск задания/материала в кабинете методиста.

Оба эндпоинта (`GET /tasks/search`, `GET /materials/search`) существовали
раньше на legacy `get_db` (любой валидный API-ключ, без разбора роли).
`/tasks/search` при этом звал `TaskRead.model_validate(task)` напрямую, минуя
`_task_read_for` — держатель ЛЮБОГО валидного сервисного ключа мог вытащить
`solution_rules` (верные ответы) поиском по тексту/курсу, тот же класс
утечки, что tsk-460 закрыл для трёх других эндпоинтов.

Проверяем перевод на `_STRUCTURE_GATE = require_role("methodist", "admin")`
(тот же гейт, что уже применяют структурные операции в этих же файлах,
tsk-433 Волна 2.3):

- без аутентификации → 401;
- ученик по cookie → 403 (утечка закрыта СТРУКТУРНО — тело ответа с
  `solution_rules` вообще не формируется для непривилегированного вызова);
- методист/админ по cookie → 200, `solution_rules` в ответе присутствует
  (не обнулено — гейт уже отсеял непривилегированных, дальше обнулять нечего);
- **сервисный ключ → по-прежнему работает** — TG_LMS дергает оба роута
  реальным кодом (`api_client.py`: `search_materials`/`search_tasks` через
  заголовок `X-API-Key`), и `is_service` bypass в `require_role` обязан его
  пропускать, иначе перевод гейта молча сломал бы рабочий канал ботов.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.courses import Courses
from app.models.materials import Materials
from app.models.tasks import Tasks
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()
_TAG = "tsk564"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{role or 'norole'}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sgraph(db):
    """Курс с одним заданием и одним материалом, найдёт поиск по маркеру."""
    marker = f"{_TAG}маркер{random.randint(10**8, 10**10)}"
    ids: dict[str, int] = {}
    try:
        course = Courses(
            title=f"{_TAG}-course-{random.randint(10**8, 10**10)}",
            access_level="self_guided",
            course_uid=f"lms:test:{_TAG}:{uuid.uuid4().hex[:12]}",
        )
        db.add(course)
        await db.flush()
        ids["course"] = course.id

        difficulty_id = (
            await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()

        task = Tasks(
            task_content={"type": "SC", "stem": f"условие {marker}", "title": ""},
            solution_rules={
                "max_score": 10,
                "correct_options": ["A"],
                "penalties": {"wrong_answer": 0, "missing_answer": 0, "extra_wrong_mc": 0},
            },
            course_id=course.id,
            difficulty_id=difficulty_id,
            external_uid=f"{_TAG}-task-{uuid.uuid4().hex[:12]}",
            max_score=10,
        )
        db.add(task)

        material = Materials(
            course_id=course.id,
            title=f"материал {marker}",
            type="text",
            content={"text": "содержимое"},
            external_uid=f"{_TAG}-mat-{uuid.uuid4().hex[:12]}",
        )
        db.add(material)

        await db.flush()
        ids["task"] = task.id
        ids["material"] = material.id
        await db.commit()

        yield {"ids": ids, "marker": marker}
    finally:
        await db.rollback()
        if ids.get("task"):
            await db.execute(text("DELETE FROM task_results WHERE task_id = :t"), {"t": ids["task"]})
            await db.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
        if ids.get("material"):
            await db.execute(text("DELETE FROM materials WHERE id = :m"), {"m": ids["material"]})
        if ids.get("course"):
            await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
        await db.commit()


# ---------------------------------------------------------------------------
# /tasks/search
# ---------------------------------------------------------------------------


async def test_task_search_requires_auth(sgraph, client):
    resp = await client.get("/api/v1/tasks/search", params={"q": sgraph["marker"]})
    assert resp.status_code == 401


async def test_task_search_denied_for_student(db, sgraph, client):
    _, token = await _user_with_session(db, "student")
    resp = await client.get(
        "/api/v1/tasks/search", params={"q": sgraph["marker"]}, headers=_auth(token)
    )
    assert resp.status_code == 403


async def test_task_search_ok_for_methodist_returns_solution_rules(db, sgraph, client):
    """Методисту, прошедшему гейт, задание отдаётся с solution_rules — не null.

    Обнулять поле после гейта было бы избыточно: методист/сервисный ключ уже
    единственные, кто до эндпоинта вообще доходит (tsk-460 семантика
    `_task_read_for`), и кабинет методиста без верного ответа при правке
    задания неработоспособен.
    """
    _, token = await _user_with_session(db, "methodist")
    ids = sgraph["ids"]
    resp = await client.get(
        "/api/v1/tasks/search", params={"q": sgraph["marker"]}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    found = [t for t in body if t["id"] == ids["task"]]
    assert len(found) == 1, f"задание не найдено поиском: {body}"
    assert found[0]["solution_rules"] is not None, "методисту solution_rules обязан быть виден"
    assert found[0]["solution_rules"]["correct_options"] == ["A"]


async def test_task_search_ok_for_admin(db, sgraph, client):
    _, token = await _user_with_session(db, "admin")
    resp = await client.get(
        "/api/v1/tasks/search", params={"q": sgraph["marker"]}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text


async def test_task_search_works_for_service_key(sgraph, client):
    """TG_LMS/ContentBackbone ходят с X-API-Key — регресс не должен сломать бота."""
    ids = sgraph["ids"]
    resp = await client.get(
        "/api/v1/tasks/search",
        params={"q": sgraph["marker"]},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200, resp.text
    found = [t for t in resp.json() if t["id"] == ids["task"]]
    assert len(found) == 1
    assert found[0]["solution_rules"] is not None, "сервисный ключ обязан видеть solution_rules как раньше"


async def test_task_search_course_id_filter_still_works(db, sgraph, client):
    _, token = await _user_with_session(db, "methodist")
    ids = sgraph["ids"]
    resp = await client.get(
        "/api/v1/tasks/search",
        params={"q": sgraph["marker"], "course_id": ids["course"] + 10_000_000},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [], "фильтр по чужому course_id обязан вернуть пусто"


# ---------------------------------------------------------------------------
# /materials/search
# ---------------------------------------------------------------------------


async def test_material_search_requires_auth(sgraph, client):
    resp = await client.get("/api/v1/materials/search", params={"q": sgraph["marker"]})
    assert resp.status_code == 401


async def test_material_search_denied_for_student(db, sgraph, client):
    _, token = await _user_with_session(db, "student")
    resp = await client.get(
        "/api/v1/materials/search", params={"q": sgraph["marker"]}, headers=_auth(token)
    )
    assert resp.status_code == 403


async def test_material_search_ok_for_methodist(db, sgraph, client):
    _, token = await _user_with_session(db, "methodist")
    ids = sgraph["ids"]
    resp = await client.get(
        "/api/v1/materials/search", params={"q": sgraph["marker"]}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert any(m["id"] == ids["material"] for m in items), f"материал не найден поиском: {items}"


async def test_material_search_works_for_service_key(sgraph, client):
    """TG_LMS реально дёргает GET /materials/search (api_client.py) сервисным ключом."""
    ids = sgraph["ids"]
    resp = await client.get(
        "/api/v1/materials/search",
        params={"q": sgraph["marker"]},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert any(m["id"] == ids["material"] for m in items)
