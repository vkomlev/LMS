"""tsk-718 (служебный вход лидов для соседних систем).

Покрывает то, ради чего вход и заведён:

- пускает только сервисный ключ, человеку — 403 (у него есть кабинет);
- повторное обращение того же человека **не создаёт второго лида** — это
  главное свойство, ради которого появилась таблица связей;
- разные внешние номера — разные лиды (дедуп не склеивает чужих);
- источник (объявление, город, линейка) доезжает до карточки лида;
- незнакомый канал — понятный отказ, а не ошибка сервера.
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
_TAG = "tsk718"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


def _external_id() -> str:
    return f"{_TAG}-{random.randint(10**9, 10**12)}"


def _payload(external_id: str, **over) -> dict:
    body = {
        "external_source": "avito_messenger",
        "external_id": external_id,
        "source_code": "avito",
        "contact": "https://www.avito.ru/profile/messenger/channel/u2i-test",
        "full_name": "Тестовый Собеседник",
        "source_detail": (
            "Авито, переписка; объявление «Репетитор по информатике»; "
            "город Казань; линейка ЕГЭ"
        ),
        "note": "Первое сообщение с Авито: сколько стоит?",
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


async def _new_user(db, *, role: str | None, name: str) -> str:
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
    return token


@pytest.mark.parametrize("role", ["marketer", "admin", "student", None])
async def test_service_only(db, client, role):
    """Человек сюда не ходит — даже маркетолог: у него есть кабинет."""
    token = await _new_user(db, role=role, name=f"gate-{role}")
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(_external_id()),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


async def test_repeat_call_does_not_duplicate_lead(db, client):
    """Повторное обращение того же человека — тот же лид, а не второй.

    Ровно то, из-за чего в базе соседнего проекта завёлся дубль на дубле:
    ключ дедупа должен срабатывать всегда, а не «обычно».
    """
    external_id = _external_id()
    try:
        first = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id),
            headers={"X-API-Key": _api_key()},
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True

        second = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id, note="Написал ещё раз по другому объявлению"),
            headers={"X-API-Key": _api_key()},
        )
        assert second.status_code == 200, second.text
        assert second.json()["created"] is False
        assert second.json()["lead_id"] == first.json()["lead_id"]

        total = (
            await db.execute(
                text(
                    "SELECT count(*) FROM lead_external_ref "
                    "WHERE external_id = :e AND source = 'avito_messenger'"
                ),
                {"e": external_id},
            )
        ).scalar()
        assert total == 1
    finally:
        await _cleanup(db, [external_id])


async def test_different_people_get_different_leads(db, client):
    """Разные собеседники — разные лиды: дедуп не склеивает чужих."""
    one, two = _external_id(), _external_id()
    try:
        first = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(one),
            headers={"X-API-Key": _api_key()},
        )
        second = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(two),
            headers={"X-API-Key": _api_key()},
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["lead_id"] != second.json()["lead_id"]
        assert second.json()["created"] is True
    finally:
        await _cleanup(db, [one, two])


async def test_source_reaches_lead_card(db, client):
    """Объявление, город и линейка доезжают до карточки — иначе воронку не разобрать."""
    external_id = _external_id()
    try:
        resp = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id),
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        row = (
            await db.execute(
                text(
                    "SELECT l.source_detail, l.contact, l.note, s.code "
                    "FROM leads l JOIN lead_source s ON s.id = l.source_id "
                    "WHERE l.id = :id"
                ),
                {"id": resp.json()["lead_id"]},
            )
        ).first()
        assert row is not None
        assert row.code == "avito"
        assert "линейка ЕГЭ" in row.source_detail
        assert "город Казань" in row.source_detail
        assert row.contact.startswith("https://www.avito.ru/")
        assert "сколько стоит" in row.note
    finally:
        await _cleanup(db, [external_id])


async def test_unknown_source_code_is_explained(client):
    """Незнакомый канал — понятный отказ, а не ошибка сервера."""
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(_external_id(), source_code="нет-такого-канала"),
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 404, resp.text
    assert "не найден" in resp.json()["detail"]


@pytest.mark.parametrize("bad", ["", "   "])
async def test_blank_external_id_rejected(client, bad):
    """Пустой и пробельный внешний номер — отказ на входе.

    Пустой ключ склейки — тот самый случай, когда дедуп молча перестаёт
    работать. Строка из пробелов ничем не лучше: она непустая для проверки
    длины, но склеивает всех подряд. До базы такое доезжать не должно.
    """
    resp = await client.post(
        "/api/v1/integrations/leads",
        json=_payload(bad),
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 422, resp.text
