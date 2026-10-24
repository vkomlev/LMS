"""tsk-1407: заявка из бота записи с лендинга через служебный вход `/integrations/leads`.

Проверяем:
- метки лендинга попадают в `leads.attribution`, страница — в согласие;
- маркетологи получают уведомление «Новая заявка с сайта» — один раз, на новый лид;
- повтор того же человека не меняет метки первого касания и не шлёт уведомление;
- заявки Авито уведомлений не создают.
"""
from __future__ import annotations

from sqlalchemy import text

from tests.test_tsk718_integrations_leads import _api_key, _cleanup, _external_id, _new_user, _payload

_ATTR = {
    "utm_source": "victor-komlev.ru",
    "utm_medium": "landing",
    "utm_campaign": "landing-chat-boty",
    "page": "landing-chat-boty",
    "evil": "x",
}


def _bot_payload(external_id: str, **over) -> dict:
    body = _payload(
        external_id,
        external_source="tg_landing_bot",
        source_code="website",
        contact="@pytest_tsk1407",
        full_name="Тест Бот",
        source_detail="landing-chat-boty",
        note=None,
        attribution=_ATTR,
        consent=True,
        filler_role="parent",
        page="landing-chat-boty",
    )
    body.update(over)
    return body


async def _notifications(db, lead_id: int) -> int:
    return (
        await db.execute(
            text(
                "SELECT count(*) FROM notifications WHERE kind = 'lead_created' "
                "AND (payload->>'lead_id')::int = :l"
            ),
            {"l": lead_id},
        )
    ).scalar_one()


async def _marketers(db) -> int:
    return (
        await db.execute(
            text(
                "SELECT count(DISTINCT ur.user_id) FROM user_roles ur "
                "JOIN roles r ON r.id = ur.role_id WHERE r.name = 'marketer'"
            )
        )
    ).scalar_one()


async def test_bot_lead_attribution_consent_and_notification(db, client):
    await _new_user(db, role="marketer", name="mk")  # уведомлению есть кому прийти
    external_id = _external_id()
    try:
        resp = await client.post(
            "/api/v1/integrations/leads",
            json=_bot_payload(external_id),
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        lead_id = resp.json()["lead_id"]
        row = (
            await db.execute(
                text(
                    "SELECT l.attribution, s.code FROM leads l "
                    "JOIN lead_source s ON s.id = l.source_id WHERE l.id = :l"
                ),
                {"l": lead_id},
            )
        ).one()
        assert row.code == "website"
        assert row.attribution == {k: v for k, v in _ATTR.items() if k != "evil"}
        page = (
            await db.execute(text("SELECT page FROM pd_consents WHERE lead_id = :l"), {"l": lead_id})
        ).scalar_one()
        assert page == "landing-chat-boty"
        assert await _marketers(db) >= 1
        assert await _notifications(db, lead_id) == await _marketers(db)

        again = await client.post(
            "/api/v1/integrations/leads",
            json=_bot_payload(external_id, attribution={"utm_source": "other"}),
            headers={"X-API-Key": _api_key()},
        )
        assert again.json() == {"lead_id": lead_id, "created": False}
        attribution = (
            await db.execute(text("SELECT attribution FROM leads WHERE id = :l"), {"l": lead_id})
        ).scalar_one()
        assert attribution["utm_source"] == "victor-komlev.ru"
        assert await _notifications(db, lead_id) == await _marketers(db)
    finally:
        await db.execute(
            text(
                "DELETE FROM notifications WHERE kind = 'lead_created' AND payload->>'lead_id' IN "
                "(SELECT lead_id::text FROM lead_external_ref WHERE external_id = :e)"
            ),
            {"e": external_id},
        )
        await db.execute(
            text(
                "DELETE FROM pd_consents WHERE lead_id IN "
                "(SELECT lead_id FROM lead_external_ref WHERE external_id = :e)"
            ),
            {"e": external_id},
        )
        await db.commit()
        await _cleanup(db, [external_id])


async def test_avito_lead_does_not_notify(db, client):
    external_id = _external_id()
    try:
        resp = await client.post(
            "/api/v1/integrations/leads",
            json=_payload(external_id),
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        assert await _notifications(db, resp.json()["lead_id"]) == 0
    finally:
        await _cleanup(db, [external_id])
