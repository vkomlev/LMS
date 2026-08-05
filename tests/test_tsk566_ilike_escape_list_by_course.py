"""tsk-566 — экранирование `%`/`_` в ILIKE-поиске `GET /courses/{id}/materials?q=`.

Тот же класс бага, что tsk-565 закрыл для `/tasks/search` и `/materials/search`:
буквальный `%` или `_` в поисковой строке методиста работал как wildcard ILIKE,
а не как искомый текст — decoy-запись без спецсимвола находилась вместе с
точным совпадением. Не инъекция (SQLAlchemy параметризует значение), чисто
ложные срабатывания поиска.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.models.courses import Courses
from app.models.materials import Materials
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk566"


async def _methodist_session(db) -> str:
    u = Users(
        email=f"{_TAG}-methodist-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-methodist",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'methodist' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def escape_graph(db):
    """Курс с парой материалов: буквальный `%` в title vs decoy без него."""
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

        material_literal = Materials(
            course_id=course.id,
            title=f"{_TAG}маркер100%okматериал",
            type="text",
            content={"text": "x"},
            external_uid=f"{_TAG}-mat-literal-{uuid.uuid4().hex[:12]}",
        )
        material_decoy = Materials(
            course_id=course.id,
            title=f"{_TAG}маркер100Xokматериал",
            type="text",
            content={"text": "x"},
            external_uid=f"{_TAG}-mat-decoy-{uuid.uuid4().hex[:12]}",
        )
        db.add_all([material_literal, material_decoy])

        await db.flush()
        ids["material_literal"] = material_literal.id
        ids["material_decoy"] = material_decoy.id
        await db.commit()

        yield ids
    finally:
        await db.rollback()
        if ids.get("material_literal"):
            await db.execute(
                text("DELETE FROM materials WHERE id = ANY(:m)"),
                {"m": [ids["material_literal"], ids["material_decoy"]]},
            )
        if ids.get("course"):
            await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
        await db.commit()


async def test_list_by_course_percent_is_literal_not_wildcard(db, escape_graph, client):
    """Запрос с буквальным `%` находит только точное совпадение, не decoy без него."""
    token = await _methodist_session(db)
    resp = await client.get(
        f"/api/v1/courses/{escape_graph['course']}/materials",
        params={"q": f"{_TAG}маркер100%okматериал"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = [m["id"] for m in resp.json()["items"]]
    assert found == [escape_graph["material_literal"]], (
        f"decoy без % не должен попасть в выдачу: {found}"
    )


async def test_list_by_course_plain_query_unaffected_by_escaping(db, escape_graph, client):
    """Обычный запрос без спецсимволов по-прежнему находит оба совпадения (нет регресса)."""
    token = await _methodist_session(db)
    resp = await client.get(
        f"/api/v1/courses/{escape_graph['course']}/materials",
        params={"q": f"{_TAG}маркер"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = {m["id"] for m in resp.json()["items"]}
    assert {escape_graph["material_literal"], escape_graph["material_decoy"]} <= found
