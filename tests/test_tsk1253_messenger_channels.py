"""tsk-1253, этап Б: каналы уведомлений ВКонтакте и MAX.

- канал без ключей не показывается и ссылку не выдаёт;
- привязка по событию сервиса (ВК `message_new` с `ref`, MAX `bot_started`
  с `payload`) — один раз, токен одноразовый;
- событие с чужим секретом отклоняется;
- отписка: слово «стоп», `message_deny`, `bot_stopped`, кнопка в кабинете;
- отправка только при включённом рубильнике канала; отказ ВК «нельзя писать»
  отключает привязку; текст — без содержимого сообщения, со ссылкой.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.models.users import Users
from app.services import messenger_channel_service as svc
from app.services.auth import identity_link_service

VK_SECRET = "vk-secret-test"
MAX_SECRET = "max-secret-test"


@pytest.fixture
def channels_env(monkeypatch):
    """Оба канала настроены, отправка перехвачена."""
    monkeypatch.setenv("VK_GROUP_ID", "123456")
    monkeypatch.setenv("VK_GROUP_TOKEN", "vk-token")
    monkeypatch.setenv("VK_CALLBACK_SECRET", VK_SECRET)
    monkeypatch.setenv("VK_CALLBACK_CONFIRMATION", "conf-string")
    monkeypatch.setenv("MAX_BOT_TOKEN", "max-token")
    monkeypatch.setenv("MAX_BOT_USERNAME", "school_bot")
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", MAX_SECRET)
    sent: list[tuple[str, str, str]] = []

    async def fake_send(channel, external_id, message, settings=None):
        sent.append((channel, external_id, message))

    monkeypatch.setattr(svc, "send_text", fake_send)
    return sent


async def _student(db) -> tuple[int, str]:
    from app.services.auth.session_service import create_session

    email = f"tsk1253b-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk1253b", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.execute(text(
        "INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE name = 'student'"
    ), {"u": u.id})
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _cleanup(db, user_id: int) -> None:
    await db.rollback()
    # Пользователя не удаляем: audit_event — только дозапись (как в этапе А).
    await db.execute(text("DELETE FROM messenger_link WHERE user_id = :u"), {"u": user_id})
    await db.commit()


def _ext() -> str:
    return str(random.randint(10**8, 10**9))


async def _link_row(db, user_id: int, channel: str):
    return (await db.execute(text(
        "SELECT external_id, disabled_at, disabled_reason FROM messenger_link "
        "WHERE user_id = :u AND channel = :c"
    ), {"u": user_id, "c": channel})).first()


@pytest.mark.asyncio
async def test_not_configured_hides_channels(client, db, monkeypatch):
    for k in ("VK_GROUP_TOKEN", "MAX_BOT_TOKEN"):
        monkeypatch.setenv(k, "")
    student, token = await _student(db)
    try:
        r = await client.get("/api/v1/me/messengers", cookies={"session": token})
        assert r.status_code == 200, r.text
        assert all(not c["available"] for c in r.json())
        r = await client.post("/api/v1/me/messengers/max/link", cookies={"session": token})
        assert r.status_code == 404
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_vk_link_via_callback_once(client, db, channels_env):
    student, token = await _student(db)
    vk_id = _ext()
    try:
        r = await client.post("/api/v1/me/messengers/vk/link", cookies={"session": token})
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert url.startswith("https://vk.me/club123456?ref=")
        ref = url.split("ref=", 1)[1]

        event = {"type": "message_new", "group_id": 123456, "secret": VK_SECRET,
                 "object": {"message": {"from_id": int(vk_id), "text": "Начать", "ref": ref}}}
        r = await client.post("/api/v1/integrations/vk/callback", json=event)
        assert r.status_code == 200 and r.text == "ok"
        row = await _link_row(db, student, "vk")
        assert row is not None and row[0] == vk_id and row[1] is None
        assert channels_env[-1][:2] == ("vk", vk_id) and "стоп" in channels_env[-1][2]

        r = await client.get("/api/v1/me/messengers", cookies={"session": token})
        assert {c["channel"]: c["linked"] for c in r.json()} == {"vk": True, "max": False, "tg": False}

        # Повтор той же ссылки — токен погашен, привязка не меняется.
        r = await client.post("/api/v1/integrations/vk/callback", json=event)
        assert r.text == "ok" and channels_env[-1][2] == svc.EXPIRED_TEXT
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_vk_callback_rejects_wrong_secret_and_confirms(client, channels_env):
    r = await client.post("/api/v1/integrations/vk/callback",
                          json={"type": "confirmation", "group_id": 123456, "secret": "nope"})
    assert r.status_code == 403
    r = await client.post("/api/v1/integrations/vk/callback",
                          json={"type": "confirmation", "group_id": 999, "secret": VK_SECRET})
    assert r.status_code == 403, "чужое сообщество"
    r = await client.post("/api/v1/integrations/vk/callback",
                          json={"type": "confirmation", "group_id": 123456, "secret": VK_SECRET})
    assert r.status_code == 200 and r.text == "conf-string"


@pytest.mark.asyncio
async def test_max_link_and_stop_word(client, db, channels_env):
    student, token = await _student(db)
    max_id = _ext()
    headers = {"X-Max-Bot-Api-Secret": MAX_SECRET}
    try:
        r = await client.post("/api/v1/me/messengers/max/link", cookies={"session": token})
        url = r.json()["url"]
        assert url.startswith("https://max.ru/school_bot?start=")
        payload = url.split("start=", 1)[1]
        assert len(payload) <= 128, "предел MAX на параметр запуска"

        started = {"update_type": "bot_started", "user": {"user_id": int(max_id)}, "payload": payload}
        r = await client.post("/api/v1/integrations/max/webhook", json=started)
        assert r.status_code == 403, "без секрета"
        r = await client.post("/api/v1/integrations/max/webhook", json=started, headers=headers)
        assert r.status_code == 200
        assert (await _link_row(db, student, "max"))[1] is None

        stop = {"update_type": "message_created",
                "message": {"sender": {"user_id": int(max_id)}, "body": {"text": "Стоп"}}}
        r = await client.post("/api/v1/integrations/max/webhook", json=stop, headers=headers)
        row = await _link_row(db, student, "max")
        assert row[1] is not None and row[2] == "stop_word"
        assert channels_env[-1][2] == svc.STOPPED_TEXT
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_unsubscribe_events_and_cabinet(client, db, channels_env):
    student, token = await _student(db)
    vk_id, max_id = _ext(), _ext()
    await db.execute(text(
        "INSERT INTO messenger_link (user_id, channel, external_id) VALUES (:u, 'vk', :v), (:u, 'max', :m)"
    ), {"u": student, "v": vk_id, "m": max_id})
    await db.commit()
    try:
        r = await client.post("/api/v1/integrations/vk/callback", json={
            "type": "message_deny", "group_id": 123456, "secret": VK_SECRET,
            "object": {"user_id": int(vk_id)}})
        assert r.text == "ok"
        assert (await _link_row(db, student, "vk"))[2] == "message_deny"

        r = await client.delete("/api/v1/me/messengers/max", cookies={"session": token})
        assert r.status_code == 204
        assert (await _link_row(db, student, "max"))[2] == "cabinet"
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_notify_respects_switches_and_refusal(db, channels_env, monkeypatch):
    student, _ = await _student(db)
    vk_id, max_id = _ext(), _ext()
    await db.execute(text(
        "INSERT INTO messenger_link (user_id, channel, external_id) VALUES (:u, 'vk', :v), (:u, 'max', :m)"
    ), {"u": student, "v": vk_id, "m": max_id})
    await db.commit()
    try:
        # Оба рубильника выключены по умолчанию — ничего не уходит.
        n = await svc.notify_user(db, user_id=student, kind=svc.KIND_MESSAGE, url="https://x/me/messages")
        assert n == 0 and channels_env == []

        settings_store.apply_local("notify_max_enabled", True)
        n = await svc.notify_user(db, user_id=student, kind=svc.KIND_MESSAGE, url="https://x/me/messages")
        assert n == 1 and channels_env[0][:2] == ("max", max_id)
        assert "https://x/me/messages" in channels_env[0][2]

        # ВК отказал «нельзя писать» — привязка отключается.
        settings_store.apply_local("notify_vk_enabled", True)

        async def refuse(channel, external_id, message, settings=None):
            if channel == "vk":
                raise svc.SendRefusedError("vk 901")

        monkeypatch.setattr(svc, "send_text", refuse)
        n = await svc.notify_user(db, user_id=student, kind=svc.KIND_REENGAGE, url="https://x/me/continue")
        assert n == 1
        assert (await _link_row(db, student, "vk"))[2] == "refused"
    finally:
        settings_store.forget_local("notify_max_enabled")
        settings_store.forget_local("notify_vk_enabled")
        await _cleanup(db, student)


def test_switches_off_by_default():
    from app.core.settings_registry import SETTINGS  # noqa: N811

    keys = {d.key: d for d in SETTINGS}
    assert keys["notify_vk_enabled"].default is False
    assert keys["notify_max_enabled"].default is False


def test_stop_words():
    assert svc.is_stop_word(" Стоп! ")
    assert svc.is_stop_word("/stop")
    assert not svc.is_stop_word("стоп-кран это что")


def test_max_ssl_context_trusts_mincifry_root():
    """API MAX подписан корнем Минцифры, которого нет в certifi (проверено на проде 21.10)."""
    svc.max_ssl_context.cache_clear()
    ctx = svc.max_ssl_context()
    names = {dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()}
    assert "Russian Trusted Root CA" in names
