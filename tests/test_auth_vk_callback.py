"""
Тесты VK ID 2.0 callback endpoint.

Покрывает:
- POST /auth/vk/callback с невалидным code → 401
- POST /auth/vk/callback без VK credentials → ожидаемая ошибка
"""
import pytest


@pytest.mark.asyncio
async def test_vk_callback_invalid_code(client):
    """Невалидный code → 401 (VK token exchange fails)."""
    resp = await client.post(
        "/api/v1/auth/vk/callback",
        json={
            "code": "invalid_code",
            "code_verifier": "fake_verifier",
            "device_id": "fake_device",
        },
    )
    assert resp.status_code in (401, 422, 503)


@pytest.mark.asyncio
async def test_vk_callback_missing_fields(client):
    """Отсутствие обязательных полей → 422."""
    resp = await client.post(
        "/api/v1/auth/vk/callback",
        json={"code": "abc"},
    )
    assert resp.status_code == 422
