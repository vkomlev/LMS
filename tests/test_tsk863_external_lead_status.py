"""tsk-863 (служебное чтение: дошёл человек с площадки или потерялся).

Покрывает то, ради чего чтение заведено. На Авито разговор переезжает в
мессенджер, и площадка после этого молчит: по её переписке не отличить того,
кто уже занимается, от того, кто пропал. Признак различия ровно один —
привязан ли к лиду ученик, и знает о нём только LMS.

Проверяем:

- вход служебный: человеку 403, как и у соседнего `POST /leads`;
- признак `linked` меняется вслед за привязкой ученика — иначе напоминалка
  тревожила бы уже дошедших;
- окно `days` отсекает старые обращения;
- чужой источник в ответ не попадает.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()
_TAG = "tsk863"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


def _external_id() -> str:
    return f"{_TAG}-{random.randint(10**9, 10**12)}"


def _payload(external_id: str, **over) -> dict:
    body = {
        "external_source": "avito_messenger",
        "external_id": external_id,
        "source_code": "avito",
        "contact": "https://www.avito.ru/profile/messenger/channel/u2i-tsk863",
        "full_name": "Собеседник tsk863",
        "note": "Первое сообщение с Авито",
    }
    body.update(over)
    return body


async def _cleanup(db, external_ids: list[str]) -> None:
    await db.execute(
        text(
            "DELETE FROM leads WHERE id IN ("
            "SELECT lead_id FROM lead_external_ref WHERE external_id = ANY(:ids))"
        ),
        {"ids": external_ids},
    )
    await db.commit()


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :role "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _ingest(client, external_id: str) -> int:
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(external_id),
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["lead_id"]


def _row(payload: dict, external_id: str) -> dict | None:
    for item in payload["leads"]:
        if item["external_id"] == external_id:
            return item
    return None


@pytest.mark.parametrize("role", ["marketer", "admin", "student", None])
async def test_service_only(db, client, role):
    """Человеку сюда не нужно: признак привязки виден в его кабинете."""
    _, token = await _new_user(db, role=role, name=f"gate-{role}")
    resp = await client.get(
        "/api/v1/integrations/leads/external",
        params={"source": "avito_messenger"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


async def test_linked_follows_the_student_link(db, client):
    """Пока ученика нет — `linked` ложно; появился — истинно.

    Это единственный признак, по которому соседняя система решает, напоминать
    о человеке или молчать. Ошибись он в любую сторону — либо тревожим
    занимающегося, либо теряем пропавшего.
    """
    external_id = _external_id()
    try:
        lead_id = await _ingest(client, external_id)

        before = await client.get(
            "/api/v1/integrations/leads/external",
            params={"source": "avito_messenger", "days": 14},
            headers={"X-API-Key": _api_key()},
        )
        assert before.status_code == 200, before.text
        row = _row(before.json(), external_id)
        assert row is not None, "свежее обращение обязано попасть в окно"
        assert row["linked"] is False
        assert row["lead_id"] == lead_id

        student_id, _ = await _new_user(db, role="student", name="linked")
        await db.execute(
            text("UPDATE leads SET linked_student_id = :s WHERE id = :id"),
            {"s": student_id, "id": lead_id},
        )
        await db.commit()

        after = await client.get(
            "/api/v1/integrations/leads/external",
            params={"source": "avito_messenger", "days": 14},
            headers={"X-API-Key": _api_key()},
        )
        assert _row(after.json(), external_id)["linked"] is True
    finally:
        await _cleanup(db, [external_id])


async def test_window_cuts_off_old_requests(db, client):
    """Обращение старше окна не возвращается — разговор остыл."""
    external_id = _external_id()
    try:
        lead_id = await _ingest(client, external_id)
        await db.execute(
            text(
                "UPDATE leads SET created_at = now() - interval '40 days' "
                "WHERE id = :id"
            ),
            {"id": lead_id},
        )
        await db.commit()

        narrow = await client.get(
            "/api/v1/integrations/leads/external",
            params={"source": "avito_messenger", "days": 14},
            headers={"X-API-Key": _api_key()},
        )
        assert _row(narrow.json(), external_id) is None

        wide = await client.get(
            "/api/v1/integrations/leads/external",
            params={"source": "avito_messenger", "days": 90},
            headers={"X-API-Key": _api_key()},
        )
        assert _row(wide.json(), external_id) is not None
    finally:
        await _cleanup(db, [external_id])


async def test_other_source_is_not_returned(db, client):
    """Чужой источник не примешивается: у каждого своя нумерация людей."""
    external_id = _external_id()
    try:
        await _ingest(client, external_id)
        resp = await client.get(
            "/api/v1/integrations/leads/external",
            params={"source": "some_other_system", "days": 14},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        assert _row(resp.json(), external_id) is None
    finally:
        await _cleanup(db, [external_id])
