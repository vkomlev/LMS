"""tsk-1398, этап 2: уведомления кабинета во внешние каналы.

- оплата, ответ на заявку о помощи, оценка — уходят во внешние каналы;
- только после коммита: откат транзакции — ничего наружу;
- вид вне списка (например, `lesson_reminder`) наружу не уходит;
- Telegram — по ключу ученического бота и своему рубильнику, на `users.tg_id`.
"""
from __future__ import annotations

import asyncio
import random

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.models.users import Users
from app.services import inbox_service
from app.services import messenger_channel_service as svc


@pytest.fixture
def captured(monkeypatch):
    """Перехват notify_user: что и кому ушло бы наружу."""
    calls: list[tuple[int, str, str]] = []

    async def fake_notify(db, *, user_id, kind, url):
        calls.append((user_id, kind, url))
        return 1

    monkeypatch.setattr(svc, "notify_user", fake_notify)
    settings_store.apply_local("notify_max_enabled", True)
    yield calls
    settings_store.forget_local("notify_max_enabled")


async def _student(db) -> int:
    u = Users(email=f"tsk1398-{random.randint(10**8, 10**10)}@example.com",
              password_hash=None, full_name="tsk1398", tg_id=None)
    db.add(u)
    await db.commit()
    return u.id


async def _drain() -> None:
    await asyncio.gather(*list(svc._background))


async def _cleanup(db, user_id: int) -> None:
    await db.rollback()
    await db.execute(text("DELETE FROM notifications WHERE user_id = :u"), {"u": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_inbox_kinds_go_out_after_commit(db, captured):
    student = await _student(db)
    try:
        for kind in ("payment_overdue", "help_request_replied", "sa_com_graded", "lesson_reminder"):
            await inbox_service.create_for_user(
                db, user_id=student, kind=kind, title="t", content="c", payload={}, created_by=None,
            )
        assert captured == [], "до коммита наружу ничего"
        await db.commit()
        await _drain()
        kinds = [k for _, k, _ in captured]
        assert kinds == [svc.KIND_PAYMENT, svc.KIND_HELP, svc.KIND_REVIEW]
        assert captured[0][2].endswith("/me/payments")
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_rollback_sends_nothing(db, captured):
    student = await _student(db)
    try:
        await inbox_service.create_for_user(
            db, user_id=student, kind="payment_overdue", title="t", content="c", payload={}, created_by=None,
        )
        await db.rollback()
        await db.commit()
        await _drain()
        assert captured == []
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_all_switches_off_nothing_queued(db, monkeypatch):
    student = await _student(db)
    calls: list = []
    monkeypatch.setattr(svc, "_dispatch", lambda *a: calls.append(a))
    try:
        await inbox_service.create_for_user(
            db, user_id=student, kind="payment_overdue", title="t", content="c", payload={}, created_by=None,
        )
        await db.commit()
        assert calls == []
    finally:
        await _cleanup(db, student)


@pytest.mark.asyncio
async def test_telegram_needs_token_and_switch(db, monkeypatch):
    sent: list[dict] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **k):
            sent.append({"url": url, **json})
            return _Resp()

    monkeypatch.setattr(svc.httpx, "AsyncClient", _Client)
    u = Users(email=f"tsk1398-tg-{random.randint(10**8, 10**10)}@example.com",
              password_hash=None, full_name="tsk1398", tg_id=random.randint(10**9, 10**10))
    db.add(u)
    await db.commit()
    try:
        monkeypatch.setenv("STUDENT_BOT_TOKEN", "")
        settings_store.apply_local(svc.TG_SETTING_KEY, True)
        assert await svc.notify_user(db, user_id=u.id, kind=svc.KIND_REVIEW, url="https://x/me") == 0

        monkeypatch.setenv("STUDENT_BOT_TOKEN", "123:abc")
        settings_store.forget_local(svc.TG_SETTING_KEY)
        assert await svc.notify_user(db, user_id=u.id, kind=svc.KIND_REVIEW, url="https://x/me") == 0

        settings_store.apply_local(svc.TG_SETTING_KEY, True)
        assert await svc.notify_user(db, user_id=u.id, kind=svc.KIND_REVIEW, url="https://x/me") == 1
        assert sent[-1]["chat_id"] == u.tg_id and "https://x/me" in sent[-1]["text"]
    finally:
        settings_store.forget_local(svc.TG_SETTING_KEY)


def test_texts_have_no_placeholders_left():
    for kind in svc.INBOX_KIND_MAP.values():
        assert "{url}" in svc._TEXTS[kind[0]]
