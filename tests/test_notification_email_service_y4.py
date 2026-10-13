"""Unit-тесты NotificationEmailService (Phase Y-4) — best-effort wrapper над Resend."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services import notification_email_service


class _FakeSettings:
    """Минимальные поля для теста."""
    def __init__(self, *, api_key: str = "test-key", base: str = "http://localhost:3000"):
        self.resend_api_key = api_key
        self.smtp_from = "noreply@victor-komlev.ru"
        self.public_base_url = base
        self.email_backend = "resend"
        self.smtp_host = "127.0.0.1"
        self.smtp_port = 25


@pytest.mark.asyncio
async def test_send_returns_false_when_no_api_key():
    """RESEND_API_KEY пуст → возвращает False (dev-fallback), не raises."""
    settings = _FakeSettings(api_key="")
    ok = await notification_email_service.send_sa_com_graded(
        recipient_email="t@example.com",
        task_title="Задача 1",
        score=7, max_score=10,
        comment="Хорошо",
        settings=settings,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_send_success_returns_true():
    """200 от Resend → True."""
    settings = _FakeSettings()
    fake_response = httpx.Response(200, json={"id": "msg-1"})

    async def _mock_post(self, *a, **kw):
        return fake_response

    with patch.object(httpx.AsyncClient, "post", _mock_post):
        ok = await notification_email_service.send_sa_com_graded(
            recipient_email="t@example.com",
            task_title="Задача 1",
            score=8, max_score=10,
            comment=None,
            settings=settings,
        )
    assert ok is True


@pytest.mark.asyncio
async def test_send_4xx_returns_false():
    """4xx от Resend → False, не raises."""
    settings = _FakeSettings()
    fake_response = httpx.Response(400, json={"error": "bad"})

    async def _mock_post(self, *a, **kw):
        return fake_response

    with patch.object(httpx.AsyncClient, "post", _mock_post):
        ok = await notification_email_service.send_sa_com_graded(
            recipient_email="t@example.com",
            task_title="Задача",
            score=5, max_score=10,
            comment=None,
            settings=settings,
        )
    assert ok is False


@pytest.mark.asyncio
async def test_send_network_error_returns_false():
    """Network error → False, не raises (best-effort)."""
    settings = _FakeSettings()

    async def _mock_post(self, *a, **kw):
        raise httpx.ConnectError("network down")

    with patch.object(httpx.AsyncClient, "post", _mock_post):
        ok = await notification_email_service.send_sa_com_graded(
            recipient_email="t@example.com",
            task_title="Задача",
            score=5, max_score=10,
            comment=None,
            settings=settings,
        )
    assert ok is False


def test_render_history_url_strips_trailing_slash():
    url = notification_email_service._build_history_url("http://localhost:3000/")
    assert url == "http://localhost:3000/me/history"
    url2 = notification_email_service._build_history_url("https://learn.victor-komlev.ru")
    assert url2 == "https://learn.victor-komlev.ru/me/history"


@pytest.mark.asyncio
async def test_smtp_backend_sends_via_smtp(monkeypatch):
    """tsk-1340: EMAIL_BACKEND=smtp → письмо уходит через SMTP, Resend не зовётся."""
    from app.services import email_transport

    settings = _FakeSettings(api_key="")
    settings.email_backend = "smtp"
    sent: list = []
    monkeypatch.setattr(email_transport, "_smtp_send_sync", lambda s, msg: sent.append(msg))
    ok = await notification_email_service.send_sa_com_graded(
        recipient_email="t@example.com",
        task_title="Задача 1",
        score=7, max_score=10,
        comment=None,
        settings=settings,
    )
    assert ok is True
    assert sent and sent[0]["To"] == "t@example.com"
    assert sent[0]["Message-ID"] and sent[0]["Date"]


@pytest.mark.asyncio
async def test_smtp_failure_returns_false(monkeypatch):
    """tsk-1340: SMTP недоступен → False, не raises."""
    from app.services import email_transport

    settings = _FakeSettings(api_key="")
    settings.email_backend = "smtp"

    def _boom(s, msg):
        raise ConnectionRefusedError("нет Postfix")

    monkeypatch.setattr(email_transport, "_smtp_send_sync", _boom)
    ok = await notification_email_service.send_sa_com_graded(
        recipient_email="t@example.com",
        task_title=None,
        score=1, max_score=2,
        comment=None,
        settings=settings,
    )
    assert ok is False
