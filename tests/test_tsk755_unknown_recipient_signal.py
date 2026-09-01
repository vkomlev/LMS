"""tsk-755: попытка входа на ничей адрес видна оператору, но не видна снаружи.

Две стороны одного решения:
  * человеку по-прежнему отвечаем одинаково на любой адрес — иначе форма входа
    становится способом перебором узнать, кто у нас учится;
  * оператор при этом видит адреса, на которые заказывали ссылку впустую, —
    именно так ловится опечатка в собственном адресе ученика.
"""
import os

import pytest
from sqlalchemy import select

from app.api.v1.auth import magic_link as magic_link_router
from app.models.audit_event import AuditEvent
from app.models.users import Users
from app.services.auth import identity_link_service, magic_link_service


@pytest.fixture(autouse=True)
def _no_real_mail(monkeypatch):
    """Письма из тестов не уходят, и счётчик частоты не мешает.

    Без ключа Resend отправка только пишет ссылку в лог. Ограничение «5 запросов
    за 10 минут с адреса» здесь снимаем: тесты идут с одного адреса и упирались
    бы в него, проверяя при этом совсем другое.
    """
    monkeypatch.setattr(magic_link_router._settings, "resend_api_key", "")

    async def _never_limited(*args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(magic_link_router, "is_rate_limited", _never_limited)


def _email(prefix: str) -> str:
    return f"{prefix}-{os.urandom(4).hex()}@example.com"


async def _last_send_event(db, email: str) -> AuditEvent | None:
    rows = (await db.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "magic_link_sent")
        .order_by(AuditEvent.id.desc())
        .limit(50)
    )).scalars().all()
    for row in rows:
        if (row.details or {}).get("email") == email:
            return row
    return None


@pytest.mark.asyncio
async def test_known_recipient_by_identity(db):
    """Полноценный вход по почте — адрес известен."""
    email = _email("known")
    user = Users(email=email, password_hash=None, full_name="Ученик")
    db.add(user)
    await db.flush()
    await identity_link_service.link_existing_user(db, user.id, "email", email)
    await db.commit()

    assert await magic_link_service.is_known_recipient(db, email) is True


@pytest.mark.asyncio
async def test_known_recipient_by_card_email(db):
    """Адрес только в карточке — тоже известен: вход по нему попадёт в этот аккаунт."""
    email = _email("card")
    db.add(Users(email=email, password_hash=None, full_name="Из импорта"))
    await db.commit()

    assert await magic_link_service.is_known_recipient(db, email) is True


@pytest.mark.asyncio
async def test_unknown_recipient(db):
    """Адрес, которого ни у кого нет."""
    assert await magic_link_service.is_known_recipient(db, _email("nobody")) is False


@pytest.mark.asyncio
async def test_case_and_spacing_do_not_hide_owner(db):
    """Заглавные буквы в адресе не делают из своего ученика чужого."""
    email = _email("Case").lower()
    user = Users(email=email, password_hash=None, full_name="Ученик")
    db.add(user)
    await db.flush()
    await identity_link_service.link_existing_user(db, user.id, "email", email)
    await db.commit()

    assert await magic_link_service.is_known_recipient(db, email.upper()) is True


@pytest.mark.asyncio
async def test_send_answers_the_same_for_unknown_address(client, db):
    """Ответ снаружи одинаков — по нему нельзя понять, есть такой ученик или нет."""
    unknown = _email("outsider")
    known = _email("insider")
    user = Users(email=known, password_hash=None, full_name="Свой")
    db.add(user)
    await db.flush()
    await identity_link_service.link_existing_user(db, user.id, "email", known)
    await db.commit()

    r_unknown = await client.post("/api/v1/auth/magic-link/send", json={"email": unknown})
    r_known = await client.post("/api/v1/auth/magic-link/send", json={"email": known})

    assert r_unknown.status_code == r_known.status_code == 202
    assert r_unknown.json() == r_known.json()


@pytest.mark.asyncio
async def test_send_marks_unknown_recipient_in_journal(client, db):
    """В журнале попытка помечена — из этого и растёт список для оператора."""
    unknown = _email("typo")

    resp = await client.post("/api/v1/auth/magic-link/send", json={"email": unknown})
    assert resp.status_code == 202

    event = await _last_send_event(db, unknown)
    assert event is not None
    assert event.details["recipient_known"] is False


@pytest.mark.asyncio
async def test_send_marks_known_recipient_in_journal(client, db):
    """Свой ученик в этот список не попадает."""
    known = _email("regular")
    user = Users(email=known, password_hash=None, full_name="Свой")
    db.add(user)
    await db.flush()
    await identity_link_service.link_existing_user(db, user.id, "email", known)
    await db.commit()

    resp = await client.post("/api/v1/auth/magic-link/send", json={"email": known})
    assert resp.status_code == 202

    event = await _last_send_event(db, known)
    assert event is not None
    assert event.details["recipient_known"] is True
