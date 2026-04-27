"""VK ID 2.0 OAuth flow: обмен code → tokens, извлечение user_id."""
import logging

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

_VK_TOKEN_URL = "https://id.vk.com/oauth2/auth"
_VK_USERINFO_URL = "https://id.vk.com/oauth2/user_info"


async def exchange_code(
    code: str,
    code_verifier: str,
    device_id: str,
    settings: Settings,
) -> dict:
    """
    Обменять authorization_code (PKCE) на access+refresh токены VK ID 2.0.
    Возвращает dict с access_token, refresh_token, expires_in, user_id.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _VK_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": settings.vk_id_client_id,
                "device_id": device_id,
                "redirect_uri": settings.vk_id_redirect_uri,
            },
        )
    if resp.status_code != 200:
        logger.error("VK token exchange error %s: %s", resp.status_code, resp.text)
        raise ValueError("VK token exchange failed")

    data = resp.json()
    if "error" in data:
        raise ValueError(f"VK error: {data['error']}")

    return data


async def get_vk_user_id(access_token: str) -> str:
    """Получить VK user_id через /user_info."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _VK_USERINFO_URL,
            data={"access_token": access_token},
        )
    if resp.status_code != 200:
        raise ValueError("VK userinfo failed")
    data = resp.json()
    user = data.get("user", {})
    uid = user.get("user_id") or user.get("id")
    if not uid:
        raise ValueError("VK user_id not found in userinfo response")
    return str(uid)
