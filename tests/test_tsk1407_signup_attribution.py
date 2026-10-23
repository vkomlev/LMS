"""tsk-1407: метка источника доходит от сайта до пользователя и заявки.

Проверяем:
- гостевая сессия принимает метки при создании, чужие ключи отбрасываются;
- регистрация по magic-link с гостевой сессией → `users.signup_attribution`;
- вход существующего пользователя источник регистрации не пишет;
- заявка с лендинга → `leads.attribution` с utm и слагом страницы;
- согласие без явной страницы получает `page` из заголовка Referer.
"""
from __future__ import annotations

import hashlib
import os

import pytest
from sqlalchemy import text

from app.api.v1 import learning_guest as learning_guest_module
from tests.test_pd_consents_tsk1335 import (  # noqa: F401
    _CONSENT, _consents, _email, _never, _no_limits, _send,
)

@pytest.fixture(autouse=True)
def _no_session_limit(monkeypatch):
    """Лимит создания гостевых сессий тестам не мешает."""
    monkeypatch.setattr(learning_guest_module, "is_rate_limited", _never)


_UTM = {
    "utm_source": "victor-komlev.ru",
    "utm_medium": "landing",
    "utm_campaign": "pytest-tsk1407",
}


async def _new_guest_session(client, attribution: dict) -> str:
    resp = await client.post("/api/v1/learning/guest/session", json={"attribution": attribution})
    assert resp.status_code == 201, resp.text
    return resp.json()["guest_session_id"]


async def _verify(client, db, email: str, guest_session_id: str | None):
    """Подменить хэш последней ссылки на известный токен и пройти verify."""
    raw = os.urandom(32)
    await db.execute(
        text(
            "UPDATE magic_link SET token_hash = :h WHERE id = "
            "(SELECT max(id) FROM magic_link WHERE email = :e)"
        ),
        {"h": hashlib.sha256(raw).digest(), "e": email},
    )
    await db.commit()
    body = {"token": raw.hex()}
    if guest_session_id:
        body["guest_session_id"] = guest_session_id
    return await client.post("/api/v1/auth/magic-link/verify", json=body)


async def _signup_attribution(db, email: str):
    return (
        await db.execute(
            text("SELECT signup_attribution FROM users WHERE lower(email) = :e"), {"e": email}
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_guest_session_keeps_known_marks_only(client, db):
    gs = await _new_guest_session(client, {**_UTM, "page": "/kurs-excel/", "evil": {"x": 1}})
    stored = (
        await db.execute(text("SELECT attribution FROM guest_session WHERE id = :g"), {"g": gs})
    ).scalar_one()
    assert stored == {**_UTM, "page": "/kurs-excel/"}


@pytest.mark.asyncio
async def test_guest_session_without_body_still_created(client):
    resp = await client.post("/api/v1/learning/guest/session")
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_registration_copies_guest_marks_to_user(client, db):
    gs = await _new_guest_session(client, _UTM)
    email = _email()
    assert (await _send(client, email, **_CONSENT)).status_code == 202
    verify = await _verify(client, db, email, gs)
    assert verify.status_code == 200, verify.text
    assert await _signup_attribution(db, email) == {**_UTM, "guest_session_id": gs}


@pytest.mark.asyncio
async def test_existing_user_login_does_not_write_signup_source(client, db):
    email = _email()
    assert (await _send(client, email, **_CONSENT)).status_code == 202
    assert (await _verify(client, db, email, None)).status_code == 200
    gs = await _new_guest_session(client, _UTM)
    assert (await _send(client, email)).status_code == 202
    assert (await _verify(client, db, email, gs)).status_code == 200
    assert await _signup_attribution(db, email) is None


@pytest.mark.asyncio
async def test_website_lead_writes_attribution(client, db):
    resp = await client.post(
        "/api/v1/public/leads",
        json={"full_name": "Тест", "contact": "+79000000000", "page": "pytest:tsk1407", **_UTM},
    )
    assert resp.status_code == 201, resp.text
    lead_id = resp.json()["lead_id"]
    attribution = (
        await db.execute(text("SELECT attribution FROM leads WHERE id = :l"), {"l": lead_id})
    ).scalar_one()
    assert attribution == {**_UTM, "page": "pytest:tsk1407"}
    await db.execute(text("DELETE FROM leads WHERE id = :l"), {"l": lead_id})
    await db.commit()


@pytest.mark.asyncio
async def test_website_lead_with_guest_session_links_it(client, db):
    gs = await _new_guest_session(client, {"utm_source": "victor-komlev.ru", "page": "/quiz/x"})
    resp = await client.post(
        "/api/v1/public/leads",
        json={"full_name": "Тест", "contact": "+79000000000", "page": "pytest:tsk1407",
              "guest_session_id": gs},
    )
    assert resp.status_code == 201, resp.text
    lead_id = resp.json()["lead_id"]
    row = (
        await db.execute(
            text("SELECT attribution, guest_session_id::text AS gs FROM leads WHERE id = :l"),
            {"l": lead_id},
        )
    ).one()
    assert row.gs == gs
    assert row.attribution == {
        "utm_source": "victor-komlev.ru", "page": "pytest:tsk1407", "guest_session_id": gs,
    }
    await db.execute(text("DELETE FROM leads WHERE id = :l"), {"l": lead_id})
    await db.commit()


@pytest.mark.asyncio
async def test_consent_page_from_referer(client, db):
    email = _email()
    resp = await client.post(
        "/api/v1/auth/magic-link/send",
        json={"email": email, **_CONSENT},
        headers={"referer": "https://learn.victor-komlev.ru/quiz/abc?utm_source=victor-komlev.ru"},
    )
    assert resp.status_code == 202, resp.text
    rows = await _consents(db, "email = :e", {"e": email})
    assert rows[0].page == "https://learn.victor-komlev.ru/quiz/abc?utm_source=victor-komlev.ru"


def test_referer_page_drops_secrets_keeps_utm():
    from app.services.pd_consent_service import _referer_page

    assert _referer_page(
        "https://learn.victor-komlev.ru/auth/vk/callback?code=SECRET&state=x&utm_source=s#frag"
    ) == "https://learn.victor-komlev.ru/auth/vk/callback?utm_source=s"
    assert _referer_page("not a url") is None
    assert _referer_page(None) is None
