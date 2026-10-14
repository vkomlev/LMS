"""tsk-1335 (152-ФЗ): журнал согласий на обработку ПДн `pd_consents`.

Проверяем:
- запись согласия при приёме заявки (квиз-воронка, лендинг) в той же транзакции;
- magic-link: новый адрес — согласие с email, на verify проставляется user_id;
  существующий адрес — не требуется и не пишется;
- VK: новый пользователь — запись; подросток — отказ регистрации;
- under_14 → 422 pd_under_14; рубильник PD_CONSENT_REQUIRED вкл/выкл;
- client_ip: X-Forwarded-For учитывается только за локальным прокси.
"""
from __future__ import annotations

import hashlib
import os
import random
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.v1 import public_leads as public_leads_module
from app.api.v1.auth import magic_link as magic_link_module
from app.api.v1.auth import vk as vk_module
from app.services import pd_consent_service

# Сид и помощники квиза-воронки (autouse-фикстуры подключаются импортом).
from tests.test_tsk1139_quiz_funnel import (  # noqa: F401
    _BASE as _FUNNEL_BASE,
    _answer_until,
    _begin,
    _funnel_on,
    _seed,
)

_PAGE = "pytest:tsk1335"
_CONSENT = {"consent": True, "filler_role": "parent"}


async def _never(*_args, **_kwargs) -> bool:
    return False


@pytest.fixture(autouse=True)
def _no_limits(monkeypatch):
    """Лимиты частоты и отправка писем тестам не мешают; рубильник выключен."""
    for module in (public_leads_module, magic_link_module, vk_module):
        monkeypatch.setattr(module, "is_rate_limited", _never)
    monkeypatch.setattr(magic_link_module._settings, "resend_api_key", "")
    monkeypatch.setattr(pd_consent_service._settings, "pd_consent_required", False)


def _required(monkeypatch, value: bool = True) -> None:
    monkeypatch.setattr(pd_consent_service._settings, "pd_consent_required", value)


async def _consents(db, where: str, params: dict) -> list:
    return (
        await db.execute(text(f"SELECT * FROM pd_consents WHERE {where} ORDER BY id"), params)
    ).all()


def _email() -> str:
    return f"tsk1335-{random.randint(10**8, 10**10)}@example.com"


# ── client_ip ──────────────────────────────────────────────────────────────

def _req(host: str | None, xff: str | None = None, real_ip: str | None = None):
    headers = {}
    if xff:
        headers["x-forwarded-for"] = xff
    if real_ip:
        headers["x-real-ip"] = real_ip
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if host else None, headers=headers
    )


def test_client_ip_spoofed_first_xff_ignored_real_ip_used():
    """Клиент подставил первый адрес XFF — в журнал идёт X-Real-IP от nginx."""
    req = _req("127.0.0.1", xff="1.2.3.4, 203.0.113.7", real_ip="203.0.113.7")
    assert pd_consent_service.client_ip(req) == "203.0.113.7"


def test_client_ip_local_proxy_without_real_ip_takes_last_xff():
    assert pd_consent_service.client_ip(_req("::1", xff="1.2.3.4, 2001:db8::1")) == "2001:db8::1"


def test_client_ip_ignores_headers_from_non_proxy():
    req = _req("198.51.100.2", xff="203.0.113.7", real_ip="203.0.113.8")
    assert pd_consent_service.client_ip(req) == "198.51.100.2"


def test_client_ip_garbage_gives_none():
    assert pd_consent_service.client_ip(_req("127.0.0.1", real_ip="not-an-ip")) is None
    assert pd_consent_service.client_ip(_req(None)) is None


def test_public_leads_rate_limit_key_uses_trusted_ip():
    req = _req("127.0.0.1", xff="1.2.3.4, 203.0.113.7", real_ip="203.0.113.7")
    assert public_leads_module._client_ip(req) == "203.0.113.7"


# ── лендинг ────────────────────────────────────────────────────────────────

async def _landing(client, **extra):
    return await client.post(
        "/api/v1/public/leads",
        json={"full_name": "Тест", "contact": "+79000000000", "page": _PAGE, **extra},
    )


@pytest.mark.asyncio
async def test_landing_lead_records_consent(client, db):
    resp = await _landing(client, **_CONSENT)
    assert resp.status_code == 201, resp.text
    lead_id = resp.json()["lead_id"]
    rows = await _consents(db, "lead_id = :l", {"l": lead_id})
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "landing" and row.page == _PAGE and row.filler_role == "parent"
    assert row.doc_consent_version == "2026-10-13" and row.doc_policy_version == "2026-10-13"


@pytest.mark.asyncio
async def test_landing_under_14_rejected_without_lead(client, db):
    before = (await db.execute(text("SELECT count(*) FROM leads"))).scalar_one()
    resp = await _landing(client, consent=True, filler_role="under_14")
    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "code": "pd_under_14",
        "message": "Попроси родителя заполнить форму — нужен его контакт и согласие.",
    }
    assert (await db.execute(text("SELECT count(*) FROM leads"))).scalar_one() == before


@pytest.mark.asyncio
async def test_landing_without_consent_flag_off_passes_without_record(client, db):
    resp = await _landing(client)
    assert resp.status_code == 201
    assert await _consents(db, "lead_id = :l", {"l": resp.json()["lead_id"]}) == []


@pytest.mark.asyncio
async def test_landing_without_consent_flag_on_rejected(client, monkeypatch):
    _required(monkeypatch)
    resp = await _landing(client, consent=False, filler_role="adult")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "pd_consent_required"


def test_widget_has_consent_controls():
    html = public_leads_module._WIDGET_HTML_TEMPLATE
    assert 'id="consent"' in html and 'id="filler_role"' in html


# ── квиз-воронка ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_funnel_lead_records_consent_with_guest_session(client, db):
    await _begin(client, branch="parent")
    await _answer_until(client, pick=-1)
    resp = await client.post(
        f"{_FUNNEL_BASE}/lead", json={"contact": "+79000000000", **_CONSENT}
    )
    assert resp.status_code == 201, resp.text
    lead_id = resp.json()["lead_id"]
    rows = await _consents(db, "lead_id = :l", {"l": lead_id})
    assert len(rows) == 1
    gs = (await db.execute(text("SELECT guest_session_id FROM leads WHERE id = :l"),
                           {"l": lead_id})).scalar_one()
    assert rows[0].source == "quiz_funnel"
    assert rows[0].guest_session_id == gs


@pytest.mark.asyncio
async def test_funnel_lead_under_14_rejected(client):
    await _begin(client, branch="parent")
    await _answer_until(client, pick=-1)
    resp = await client.post(
        f"{_FUNNEL_BASE}/lead",
        json={"contact": "+79000000000", "consent": True, "filler_role": "under_14"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "pd_under_14"


# ── magic-link ─────────────────────────────────────────────────────────────

async def _send(client, email: str, **extra):
    return await client.post("/api/v1/auth/magic-link/send", json={"email": email, **extra})


async def _verify_last(client, db, email: str):
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
    return await client.post("/api/v1/auth/magic-link/verify", json={"token": raw.hex()})


@pytest.mark.asyncio
async def test_magic_link_new_email_consent_then_user_attached(client, db):
    email = _email()
    resp = await _send(client, email, consent=True, filler_role="adult")
    assert resp.status_code == 202, resp.text
    rows = await _consents(db, "email = :e", {"e": email})
    assert len(rows) == 1 and rows[0].source == "magic_link" and rows[0].user_id is None
    link_consent = (await db.execute(
        text("SELECT pd_consent_id FROM magic_link WHERE email = :e"), {"e": email}
    )).scalar_one()
    assert link_consent == rows[0].id

    verify = await _verify_last(client, db, email)
    assert verify.status_code == 200, verify.text
    user_id = (await db.execute(
        text("SELECT id FROM users WHERE lower(email) = :e"), {"e": email}
    )).scalar_one()
    rows = await _consents(db, "email = :e", {"e": email})
    assert rows[0].user_id == user_id


@pytest.mark.asyncio
async def test_magic_link_new_email_minor_rejected(client, db):
    email = _email()
    resp = await _send(client, email, consent=True, filler_role="teen_14_17")
    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "code": "pd_minor_registration",
        "message": (
            "Ученик младше 18 регистрируется после договора и согласия родителя. "
            "Оставь заявку — мы свяжемся с родителем."
        ),
    }
    assert (await db.execute(
        text("SELECT count(*) FROM magic_link WHERE email = :e"), {"e": email}
    )).scalar_one() == 0


@pytest.mark.asyncio
async def test_magic_link_new_email_required_flag(client, monkeypatch):
    _required(monkeypatch)
    resp = await _send(client, _email())
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "pd_consent_required"


@pytest.mark.asyncio
async def test_magic_link_existing_user_not_blocked(client, db, monkeypatch):
    from app.models.users import Users
    from app.services.auth import identity_link_service

    _required(monkeypatch)
    email = _email()
    user = Users(email=email, password_hash=None, full_name="Есть Уже", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    await db.commit()

    resp = await _send(client, email, filler_role="teen_14_17")
    assert resp.status_code == 202, resp.text
    assert await _consents(db, "email = :e", {"e": email}) == []


# ── VK ─────────────────────────────────────────────────────────────────────

def _fake_vk(monkeypatch, vk_user_id: str, email: str) -> None:
    async def _exchange(*_a, **_k):
        return {"access_token": "at", "refresh_token": None, "expires_in": 3600}

    async def _userinfo(*_a, **_k):
        return {"user_id": vk_user_id, "email": email, "full_name": "Вк Новый"}

    monkeypatch.setattr(vk_module, "exchange_code", _exchange)
    monkeypatch.setattr(vk_module, "fetch_vk_userinfo", _userinfo)


_VK_BODY = {"code": "c", "code_verifier": "v", "device_id": "d"}


@pytest.mark.asyncio
async def test_vk_new_user_records_consent(client, db, monkeypatch):
    email = _email()
    _fake_vk(monkeypatch, str(random.randint(10**9, 10**10)), email)
    resp = await client.post("/api/v1/auth/vk/callback", json={**_VK_BODY, **_CONSENT})
    assert resp.status_code == 200, resp.text
    user_id = (await db.execute(
        text("SELECT id FROM users WHERE lower(email) = :e"), {"e": email}
    )).scalar_one()
    rows = await _consents(db, "user_id = :u", {"u": user_id})
    assert len(rows) == 1 and rows[0].source == "vk"


@pytest.mark.asyncio
async def test_vk_new_minor_rejected_and_user_not_created(client, db, monkeypatch):
    email = _email()
    _fake_vk(monkeypatch, str(random.randint(10**9, 10**10)), email)
    resp = await client.post(
        "/api/v1/auth/vk/callback",
        json={**_VK_BODY, "consent": True, "filler_role": "under_14"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "pd_minor_registration"
    assert (await db.execute(
        text("SELECT count(*) FROM users WHERE lower(email) = :e"), {"e": email}
    )).scalar_one() == 0


# ── сервис ─────────────────────────────────────────────────────────────────

def test_check_consent_flag_off_skips_flag_on_rejects(monkeypatch):
    from app.schemas.pd_consent import PdConsentFields

    assert pd_consent_service.check_consent(PdConsentFields(), source="landing") is False
    assert pd_consent_service.check_consent(
        PdConsentFields(consent=True, filler_role="adult"), source="landing"
    ) is True
    _required(monkeypatch)
    with pytest.raises(pd_consent_service.PdConsentError) as exc:
        pd_consent_service.check_consent(PdConsentFields(consent=True), source="landing")
    assert exc.value.detail["code"] == "pd_consent_required"
    # tg: не блокирует даже при включённом рубильнике.
    assert pd_consent_service.check_consent(
        PdConsentFields(filler_role="under_14"), source="tg", enforce=False
    ) is False
