"""Integration HTTP-тесты PATCH /api/v1/me — доп. поля профиля (tsk-427).

Покрывает:
- happy path: category+school_grade, city (с обрезкой пробелов), timezone;
- partial update: full_name не передан — не трогается;
- кросс-валидация "класс только у школьника" — и когда category передана в
  этом же запросе, и когда берётся текущая из БД;
- каскадный сброс school_grade при смене category на не-школьника;
- 422 на некорректных category/school_grade/timezone.

Образец подъёма user+session — как в test_me_profile_update.py.
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_user_with_session(db):
    email = f"tsk427_{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name="Иванов Иван", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token


async def _cleanup(db, user_id: int) -> None:
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_sets_school_student_with_grade(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"category": "school_student", "school_grade": 9},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["category"] == "school_student"
        assert body["school_grade"] == 9
        # full_name не передавался — не тронут.
        assert body["full_name"] == "Иванов Иван"

        me_resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.json()["school_grade"] == 9
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_city_is_trimmed(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"city": "  Москва  "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["city"] == "Москва"
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_valid_timezone(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"timezone": "Asia/Yekaterinburg"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["timezone"] == "Asia/Yekaterinburg"
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_non_school_categories_without_grade(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        for category in ("university_student", "college_student", "applicant", "adult"):
            resp = await client.patch(
                "/api/v1/me",
                json={"category": category},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["category"] == category
            assert resp.json()["school_grade"] is None
    finally:
        await _cleanup(db, user_id)


# ── каскадный сброс school_grade ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_grade_reset_when_category_changes_away_from_school(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp1 = await client.patch(
            "/api/v1/me",
            json={"category": "school_student", "school_grade": 11},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200, resp1.text
        assert resp1.json()["school_grade"] == 11

        resp2 = await client.patch(
            "/api/v1/me",
            json={"category": "adult"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["category"] == "adult"
        assert resp2.json()["school_grade"] is None

        row = (
            await db.execute(
                text("SELECT school_grade FROM users WHERE id = :id"), {"id": user_id}
            )
        ).scalar_one()
        assert row is None
    finally:
        await _cleanup(db, user_id)


# ── кросс-валидация «класс только у школьника» ──────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_grade_rejected_with_non_school_category_in_same_request(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"category": "adult", "school_grade": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_grade_rejected_when_existing_category_is_not_school(db, client):
    """category не передана этим запросом — сервис берёт эффективную из БД (adult по умолчанию не задана, но тест явно ставит adult первым PATCH)."""
    user_id, token = await _setup_user_with_session(db)
    try:
        setup_resp = await client.patch(
            "/api/v1/me",
            json={"category": "adult"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert setup_resp.status_code == 200, setup_resp.text

        resp = await client.patch(
            "/api/v1/me",
            json={"school_grade": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_grade_accepted_when_existing_category_is_school(db, client):
    """category уже school_student в БД (предыдущий PATCH) — новый запрос шлёт только school_grade."""
    user_id, token = await _setup_user_with_session(db)
    try:
        setup_resp = await client.patch(
            "/api/v1/me",
            json={"category": "school_student", "school_grade": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert setup_resp.status_code == 200, setup_resp.text

        resp = await client.patch(
            "/api/v1/me",
            json={"school_grade": 6},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["school_grade"] == 6
        assert resp.json()["category"] == "school_student"
    finally:
        await _cleanup(db, user_id)


# ── 422 на некорректных значениях ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_rejects_unknown_category(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"category": "astronaut"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_grade", [0, 12, -1])
async def test_patch_me_rejects_out_of_range_grade(db, client, bad_grade):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"category": "school_student", "school_grade": bad_grade},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_rejects_invalid_timezone(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"timezone": "Not/AZone"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)


# ── partial update: full_name не передан ────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_without_full_name_leaves_it_untouched(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"city": "Казань"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["full_name"] == "Иванов Иван"
    finally:
        await _cleanup(db, user_id)
