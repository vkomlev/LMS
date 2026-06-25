"""tsk-121: DELETE /api/v1/courses/{id} — каскад vs явный отказ.

Раньше удаление курса со связями (подкурсы/материалы) падало в 500 из-за
ORM-каскада в async-режиме. Теперь — чистый 409 без cascade и 204 с ?cascade=true.
"""
from __future__ import annotations

from typing import AsyncGenerator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

_settings = Settings()
_API_KEY = next(iter(_settings.valid_api_keys))


async def _insert_course(
    db: AsyncSession, title: str, access_level: str = "self_guided"
) -> int:
    """Вставить курс напрямую и вернуть его id."""
    row = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, :a) RETURNING id"),
        {"t": title, "a": access_level},
    )
    return int(row.scalar_one())


@pytest_asyncio.fixture
async def course_with_relations(db: AsyncSession) -> AsyncGenerator[dict, None]:
    """Родительский курс с одним подкурсом и одним материалом. Чистит за собой."""
    parent_id = await _insert_course(db, "tsk121 родитель")
    child_id = await _insert_course(db, "tsk121 подкурс")
    await db.execute(
        text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"),
        {"c": child_id, "p": parent_id},
    )
    await db.execute(
        text(
            "INSERT INTO materials (course_id, title, type, content) "
            "VALUES (:c, :t, 'text', '{}'::jsonb)"
        ),
        {"c": parent_id, "t": "tsk121 материал"},
    )
    await db.commit()
    try:
        yield {"parent_id": parent_id, "child_id": child_id}
    finally:
        # На случай провала теста до удаления — подчистить оба курса
        # (CASCADE уберёт материал/связи).
        await db.execute(
            text("DELETE FROM courses WHERE id IN (:p, :c)"),
            {"p": parent_id, "c": child_id},
        )
        await db.commit()


async def test_delete_course_with_relations_without_cascade_returns_409(
    client, course_with_relations, db
):
    """Без cascade удаление курса со связями отклоняется 409 (а не 500)."""
    parent_id = course_with_relations["parent_id"]

    resp = await client.delete(
        f"/api/v1/courses/{parent_id}", params={"api_key": _API_KEY}
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "domain_error"
    relations = body["payload"]["relations"]
    assert relations["children"] == 1
    assert relations["materials"] == 1

    # Курс остался на месте
    row = await db.execute(text("SELECT 1 FROM courses WHERE id = :id"), {"id": parent_id})
    assert row.scalar_one_or_none() == 1


async def test_delete_course_with_cascade_returns_204(
    client, course_with_relations, db
):
    """С ?cascade=true курс и материал удаляются, подкурс отвязывается, но жив."""
    parent_id = course_with_relations["parent_id"]
    child_id = course_with_relations["child_id"]

    resp = await client.delete(
        f"/api/v1/courses/{parent_id}",
        params={"api_key": _API_KEY, "cascade": "true"},
    )

    assert resp.status_code == 204, resp.text

    parent = await db.execute(text("SELECT 1 FROM courses WHERE id = :id"), {"id": parent_id})
    assert parent.scalar_one_or_none() is None

    materials = await db.execute(
        text("SELECT count(*) FROM materials WHERE course_id = :id"), {"id": parent_id}
    )
    assert materials.scalar_one() == 0

    # Подкурс не удалён, лишь отвязан
    child = await db.execute(text("SELECT 1 FROM courses WHERE id = :id"), {"id": child_id})
    assert child.scalar_one_or_none() == 1
    link = await db.execute(
        text("SELECT count(*) FROM course_parents WHERE parent_course_id = :id"),
        {"id": parent_id},
    )
    assert link.scalar_one() == 0


async def test_delete_course_without_relations_returns_204(client, db):
    """Курс без связей удаляется и без cascade."""
    course_id = await _insert_course(db, "tsk121 одинокий курс")
    await db.commit()
    try:
        resp = await client.delete(
            f"/api/v1/courses/{course_id}", params={"api_key": _API_KEY}
        )
        assert resp.status_code == 204, resp.text
        row = await db.execute(
            text("SELECT 1 FROM courses WHERE id = :id"), {"id": course_id}
        )
        assert row.scalar_one_or_none() is None
    finally:
        await db.execute(text("DELETE FROM courses WHERE id = :id"), {"id": course_id})
        await db.commit()


async def test_delete_missing_course_returns_404(client):
    """Несуществующий курс — чистый 404 DomainError."""
    resp = await client.delete(
        "/api/v1/courses/999999999", params={"api_key": _API_KEY}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "domain_error"
