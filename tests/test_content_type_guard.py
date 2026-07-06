"""tsk-161 (P0): тесты ContentTypeGuardMiddleware.

Покрытие:
  1. json_content_type_passes        — Content-Type: application/json проходит до роутера
  2. multipart_content_type_passes   — Content-Type: multipart/form-data проходит до роутера
  3. text_plain_with_json_body_rejected — Content-Type: text/plain (CORS-"простой",
     обход preflight) с JSON-телом внутри → 415, ДО того как роутер вообще увидит запрос
  4. form_urlencoded_rejected        — Content-Type: application/x-www-form-urlencoded → 415
  5. no_content_type_with_body_rejected — тело есть, заголовка Content-Type нет → 415
  6. get_request_unaffected          — GET без тела не проверяется вообще
  7. empty_body_post_unaffected      — POST без Content-Length (пустое тело) не проверяется
  8. real_endpoint_json_ok           — реальный эндпоинт (/api/v1/auth/session/refresh)
     с application/json проходит guard (может вернуть 401 дальше по логике — не 415)
  9. real_endpoint_text_plain_bypass_blocked — тот же эндпоинт, но с Content-Type:
     text/plain и валидным JSON-телом внутри → 415, а НЕ обработка эндпоинтом
     (регресс-тест конкретно для CORS-preflight-bypass сценария из tsk-161)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.api.middleware.content_type_guard import ContentTypeGuardMiddleware


# ─── минимальное тестовое ASGI-приложение (изолированно от роутов LMS) ────────

async def _echo_endpoint(request):
    return JSONResponse({"reached": True})


def _build_test_app() -> Starlette:
    app = Starlette(routes=[
        Route("/echo", _echo_endpoint, methods=["POST", "GET"]),
    ])
    app.add_middleware(ContentTypeGuardMiddleware)
    return app


@pytest.fixture()
def test_app() -> Starlette:
    return _build_test_app()


# ─── тесты на изолированном приложении ────────────────────────────────────────

@pytest.mark.asyncio
async def test_json_content_type_passes(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post("/echo", json={"a": 1})
    assert r.status_code == 200
    assert r.json() == {"reached": True}


@pytest.mark.asyncio
async def test_multipart_content_type_passes(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post("/echo", files={"file": ("x.txt", b"data", "text/plain")})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_text_plain_with_json_body_rejected(test_app):
    """CORS-preflight-bypass сценарий: text/plain (простой заголовок) + JSON внутри."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post(
            "/echo",
            content=b'{"a": 1}',
            headers={"content-type": "text/plain"},
        )
    assert r.status_code == 415, r.text


@pytest.mark.asyncio
async def test_form_urlencoded_rejected(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post("/echo", data={"a": "1"})
    assert r.status_code == 415, r.text


@pytest.mark.asyncio
async def test_no_content_type_with_body_rejected(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post(
            "/echo",
            content=b'{"a": 1}',
            headers={"content-type": ""},
        )
    assert r.status_code == 415, r.text


@pytest.mark.asyncio
async def test_get_request_unaffected(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.get("/echo")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_empty_body_post_unaffected(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post("/echo")
    assert r.status_code == 200


# ─── тесты на реальном LMS-приложении (regression) ────────────────────────────

@pytest.mark.asyncio
async def test_real_endpoint_json_ok():
    """application/json на реальный эндпоинт проходит guard (не 415)."""
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/session/refresh",
            json={"refresh_token": "invalid-token-for-guard-test"},
        )
    # Guard пропустил запрос дальше — реальная бизнес-логика вернёт 401
    # (невалидный refresh_token), но НЕ 415.
    assert r.status_code != 415, r.text


@pytest.mark.asyncio
async def test_real_endpoint_text_plain_bypass_blocked():
    """
    Регресс-тест конкретно для найденной уязвимости tsk-161: Content-Type:
    text/plain (CORS-простой, не требует preflight) с валидным JSON внутри —
    должен быть отклонён guard'ом ДО того, как эндпоинт его увидит.
    """
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/session/refresh",
            content=b'{"refresh_token": "invalid-token-for-guard-test"}',
            headers={"content-type": "text/plain"},
        )
    assert r.status_code == 415, r.text
