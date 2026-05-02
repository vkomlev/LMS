"""Y-5.1: ACL для GET /materials/{id} с cookie-auth.

Параллель к test_tasks_acl_post_s5.py. Bug-fix: cookie-auth student'а
получал 403 «Invalid or missing API Key» от `Depends(get_db)` (legacy
service-key gate в CRUD router). Теперь cookie auth + ACL по дереву
`user_courses` + `course_parents` recursive.

Сценарии:
- 401 без auth (нет cookie/X-API-Key)
- 200 student с user_course на root + material на grandchild → access (recursive)
- 403 student с user_course на чужом root + material на nested → deny
- 200 teacher (extended-role) bypass — даже без user_courses
- 200 service-key bypass — для legacy TG_LMS / CB CLI
- 200 methodist bypass
- 404 несуществующий material (для аутентифицированного user)
"""
from __future__ import annotations

import json
import os
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


def _service_api_key() -> str:
    raw = os.environ.get("VALID_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        pytest.skip("VALID_API_KEYS пуст в .env")
    return keys[0]


async def _create_user_with_session(
    db, *, role_name: str | None = None,
) -> tuple[int, str]:
    u = Users(
        email=f"y51m-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y51m", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role_name:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name = :rn "
                "ON CONFLICT (user_id, role_id) DO NOTHING"
            ),
            {"u": u.id, "rn": role_name},
        )
    await db.commit()
    return u.id, token


async def _create_material(db, *, course_id: int) -> int:
    """INSERT material (минимально валидный type=text + content)."""
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES (:t, 'text', CAST(:c AS jsonb), :cid, true) RETURNING id"
        ),
        {
            "t": f"y51m-mat-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"text": "test material"}),
            "cid": course_id,
        },
    )
    mid = res.scalar_one()
    await db.commit()
    return mid


async def _enroll_user_in_course(db, *, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active, order_number) "
            "VALUES (:u, :c, true, 1) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _pick_root_with_grandchild(db) -> tuple[int, int, int]:
    row = (
        await db.execute(
            text(
                """
                SELECT cp_outer.parent_course_id AS root_id,
                       cp_outer.course_id AS child_id,
                       cp_inner.course_id AS grandchild_id
                FROM course_parents cp_outer
                JOIN course_parents cp_inner ON cp_inner.parent_course_id = cp_outer.course_id
                WHERE cp_outer.parent_course_id NOT IN (SELECT course_id FROM course_parents)
                LIMIT 1
                """
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет цепочки root→child→grandchild в БД")
    return int(row[0]), int(row[1]), int(row[2])


async def _pick_other_root(db, exclude_root_id: int) -> int:
    row = (
        await db.execute(
            text(
                "SELECT id FROM courses "
                "WHERE id != :exc "
                "  AND id NOT IN (SELECT course_id FROM course_parents) "
                "LIMIT 1"
            ),
            {"exc": exclude_root_id},
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет другого root-курса")
    return int(row[0])


async def _cleanup(db, *, user_ids: list[int], material_ids: list[int]):
    if material_ids:
        await db.execute(text("DELETE FROM materials WHERE id = ANY(:m)"), {"m": material_ids})
    if user_ids:
        await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.commit()


# ─── 401 без auth ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_material_by_id_requires_auth(db, client):
    _root, _c, gid = await _pick_root_with_grandchild(db)
    mid = await _create_material(db, course_id=gid)
    try:
        resp = await client.get(f"/api/v1/materials/{mid}")
        assert resp.status_code == 401, resp.text
    finally:
        await _cleanup(db, user_ids=[], material_ids=[mid])


# ─── student: ACL по дереву user_courses ──────────────────────────────────

@pytest.mark.asyncio
async def test_student_access_grandchild_material_through_root_enrollment(db, client):
    """Student зачислен в root → видит material на grandchild через recursive."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=root_id)
    mid = await _create_material(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/materials/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == mid
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


@pytest.mark.asyncio
async def test_student_denied_material_in_other_branch(db, client):
    """Student зачислен в чужой root → 403 на material в другой ветке."""
    root_id, _child, gid = await _pick_root_with_grandchild(db)
    other_root = await _pick_other_root(db, exclude_root_id=root_id)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll_user_in_course(db, user_id=uid, course_id=other_root)
    mid = await _create_material(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/materials/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


# ─── extended-role bypass ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teacher_bypass_without_enrollment(db, client):
    _root, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="teacher")
    mid = await _create_material(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/materials/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


@pytest.mark.asyncio
async def test_methodist_bypass_without_enrollment(db, client):
    _root, _c, gid = await _pick_root_with_grandchild(db)
    uid, token = await _create_user_with_session(db, role_name="methodist")
    mid = await _create_material(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/materials/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


# ─── service-key bypass ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_key_bypass(db, client):
    _root, _c, gid = await _pick_root_with_grandchild(db)
    api_key = _service_api_key()
    mid = await _create_material(db, course_id=gid)
    try:
        resp = await client.get(
            f"/api/v1/materials/{mid}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[], material_ids=[mid])


# ─── 404 для несуществующего ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_material_404_for_missing(db, client):
    uid, token = await _create_user_with_session(db, role_name="teacher")
    try:
        resp = await client.get(
            "/api/v1/materials/99999999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[])
