"""tsk-1468: тесты POST /api/v1/poligon/register (регистрация ученика полигона).

Покрывает:
- 201 happy path: user создан, student-роль назначена, identity_link есть
- 409 при повторной регистрации с тем же email (без 500)
- 409 не зависит от регистра букв email
- 422 на невалидный email / пустое имя (валидация схемы)
- Незасеянный дефект-реестр НЕ воспроизводится: ответ 409, не 500
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


def _email() -> str:
    return f"tsk1468-{uuid.uuid4().hex[:16]}@example.com"


async def _cleanup(db, user_ids: list[int]):
    if not user_ids:
        return
    await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    # audit_event: FK user_id ON DELETE SET NULL → SET NULL падает на append-only
    # триггере. Отключаем триггер в той же транзакции (тот же приём, что в
    # conftest._sweep_test_artifacts).
    await db.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_modify"))
    await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_modify"))
    await db.commit()


@pytest.mark.asyncio
async def test_register_happy_path(db, client):
    email = _email()
    created: list[int] = []
    try:
        resp = await client.post(
            "/api/v1/poligon/register",
            json={"full_name": "Ученик Полигона", "email": email},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["user_id"] > 0
        assert "успешно" in body["message"].lower()
        created.append(body["user_id"])

        row = (
            await db.execute(
                text("SELECT full_name FROM users WHERE id = :id"), {"id": body["user_id"]}
            )
        ).one()
        assert row[0] == "Ученик Полигона"

        # student-роль назначена (id роли смотрим по имени, не магическим числом)
        role_cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM user_roles ur "
                    "JOIN roles r ON r.id = ur.role_id "
                    "WHERE ur.user_id = :id AND r.name = 'student'"
                ),
                {"id": body["user_id"]},
            )
        ).scalar()
        assert role_cnt == 1

        # identity_link 'email'
        link_cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM identity_link "
                    "WHERE user_id = :id AND kind = 'email'"
                ),
                {"id": body["user_id"]},
            )
        ).scalar()
        assert link_cnt == 1
    finally:
        await _cleanup(db, created)


@pytest.mark.asyncio
async def test_register_duplicate_email_409(db, client):
    email = _email()
    created: list[int] = []
    try:
        resp1 = await client.post(
            "/api/v1/poligon/register",
            json={"full_name": "Первый", "email": email},
        )
        assert resp1.status_code == 201, resp1.text
        created.append(resp1.json()["user_id"])

        resp2 = await client.post(
            "/api/v1/poligon/register",
            json={"full_name": "Второй", "email": email},
        )
        assert resp2.status_code == 409, resp2.text
        assert "уже зарегистрирован" in resp2.json()["detail"]
    finally:
        await _cleanup(db, created)


@pytest.mark.asyncio
async def test_register_duplicate_email_case_insensitive(db, client):
    email = _email()
    created: list[int] = []
    try:
        resp1 = await client.post(
            "/api/v1/poligon/register",
            json={"full_name": "Первый", "email": email},
        )
        assert resp1.status_code == 201, resp1.text
        created.append(resp1.json()["user_id"])

        resp2 = await client.post(
            "/api/v1/poligon/register",
            json={"full_name": "Второй", "email": email.upper()},
        )
        assert resp2.status_code == 409, resp2.text
    finally:
        await _cleanup(db, created)


@pytest.mark.asyncio
async def test_register_invalid_email_422(db, client):
    resp = await client.post(
        "/api/v1/poligon/register",
        json={"full_name": "Имя", "email": "not-an-email"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_empty_name_422(db, client):
    resp = await client.post(
        "/api/v1/poligon/register",
        json={"full_name": "  ", "email": _email()},
    )
    assert resp.status_code == 422
