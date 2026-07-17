"""tsk-261: `/courses/by-code` обязан отдавать РЕАЛЬНЫЕ `parent_course_ids`.

Найдено независимым ревью перед деплоем. `Courses.parent_course_ids` — property,
и при незагруженной связи `parent_courses` она возвращает пустой список, а не
падает (`app/models/courses.py`). `get_by_course_uid` шёл через `repo.get_by_keys`
без eager-load ⇒ эндпоинт отдавал `parent_course_ids: []` для ЛЮБОГО курса,
включая подкурс.

Цена: SPW `useRootCourseId` отличает корень от подкурса именно по этому полю.
Пустой список для подкурса означал «это корень» — id подкурса уходил в next-item
как `root_course_id`, там `active` фильтруется по `user_courses` (а в них только
корни) ⇒ ученик на deep-link по uid подкурса видел бы «всё пройдено» на живом
курсе. Проверка выглядела рабочей и молча пропускала дефект.

Теста на это поле не было — поэтому дефект и прошёл.
"""
from __future__ import annotations

import random
from typing import AsyncGenerator, Tuple

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _student_session(db: AsyncSession) -> Tuple[int, str]:
    email = f"tsk261-bycode-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk261 by-code", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _cleanup_user(db: AsyncSession, user_id: int) -> None:
    for tbl in ("user_session", "identity_link"):
        await db.execute(text(f"DELETE FROM {tbl} WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest_asyncio.fixture
async def parent_and_child(db: AsyncSession) -> AsyncGenerator[dict, None]:
    """Корневой курс + подкурс под ним (у подкурса есть родитель)."""
    suffix = random.randint(10**6, 10**7)
    parent_uid = f"TSK261-ROOT-{suffix}"
    child_uid = f"TSK261-CHILD-{suffix}"
    r1 = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid) "
            "VALUES ('tsk261 корень', 'self_guided', :uid) RETURNING id"
        ),
        {"uid": parent_uid},
    )
    parent_id = int(r1.scalar_one())
    r2 = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid) "
            "VALUES ('tsk261 подкурс', 'self_guided', :uid) RETURNING id"
        ),
        {"uid": child_uid},
    )
    child_id = int(r2.scalar_one())
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, 1)"
        ),
        {"c": child_id, "p": parent_id},
    )
    await db.commit()
    try:
        yield {
            "parent_id": parent_id, "parent_uid": parent_uid,
            "child_id": child_id, "child_uid": child_uid,
        }
    finally:
        await db.execute(
            text("DELETE FROM course_parents WHERE course_id=:c"), {"c": child_id}
        )
        await db.execute(
            text("DELETE FROM courses WHERE id = ANY(:ids)"),
            {"ids": [child_id, parent_id]},
        )
        await db.commit()


@pytest.mark.asyncio
async def test_by_code_child_reports_its_parent(db, client, parent_and_child):
    """Подкурс отдаёт НЕПУСТОЙ parent_course_ids (падал до tsk-261)."""
    user_id, token = await _student_session(db)
    client.cookies.set("session", token)
    try:
        resp = await client.get(
            f"/api/v1/courses/by-code/{parent_and_child['child_uid']}"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == parent_and_child["child_id"]
        assert body["parent_course_ids"] == [parent_and_child["parent_id"]], (
            "подкурс обязан сообщать своего родителя, иначе потребитель считает "
            f"корнем любой курс; получено {body.get('parent_course_ids')}"
        )
    finally:
        client.cookies.clear()
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_by_code_root_reports_no_parents(db, client, parent_and_child):
    """У корня parent_course_ids пуст — проверка «это корень» осмысленна."""
    user_id, token = await _student_session(db)
    client.cookies.set("session", token)
    try:
        resp = await client.get(
            f"/api/v1/courses/by-code/{parent_and_child['parent_uid']}"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == parent_and_child["parent_id"]
        assert body["parent_course_ids"] == [], body
    finally:
        client.cookies.clear()
        await _cleanup_user(db, user_id)
