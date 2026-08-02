"""tsk-463: `GET /courses/{id}/tree` падал 500 (MissingGreenlet) и, даже без
падения, отдавал бы дерево с пустыми потомками — схема ждёт поле `children`,
а репозиторий заполнял `child_courses`.

Регресс: курс с двумя уровнями потомков → 200, дети видны на обоих уровнях,
`parent_course_ids` у потомков не роняет сериализацию.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.courses import Courses

_settings = Settings()


async def _course(db, title: str) -> int:
    c = Courses(
        title=f"{title}-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
    )
    db.add(c)
    await db.flush()
    return c.id


async def _link(db, course_id: int, parent_course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id) "
            "VALUES (:c, :p)"
        ),
        {"c": course_id, "p": parent_course_id},
    )


@pytest.mark.asyncio
async def test_course_tree_returns_nested_children(db, client):
    """Root -> child -> grandchild: дерево живое на обоих уровнях."""
    root_id = await _course(db, "t463-root")
    child_id = await _course(db, "t463-child")
    grandchild_id = await _course(db, "t463-grandchild")
    await _link(db, child_id, root_id)
    await _link(db, grandchild_id, child_id)
    await db.commit()

    api_key = next(iter(_settings.valid_api_keys))
    resp = await client.get(
        f"/api/v1/courses/{root_id}/tree",
        params={"api_key": api_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["id"] == root_id
    assert len(body["children"]) == 1, body
    child = body["children"][0]
    assert child["id"] == child_id
    assert child["parent_course_ids"] == [root_id]
    assert len(child["children"]) == 1, child
    grandchild = child["children"][0]
    assert grandchild["id"] == grandchild_id
    assert grandchild["parent_course_ids"] == [child_id]
    assert grandchild["children"] == []


@pytest.mark.asyncio
async def test_course_tree_leaf_has_empty_children(db, client):
    """Курс без потомков: дерево = сам курс, children пуст, 200."""
    leaf_id = await _course(db, "t463-leaf")
    await db.commit()

    api_key = next(iter(_settings.valid_api_keys))
    resp = await client.get(
        f"/api/v1/courses/{leaf_id}/tree",
        params={"api_key": api_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == leaf_id
    assert body["children"] == []
