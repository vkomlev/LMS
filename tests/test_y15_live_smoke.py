"""Phase Y-1.5 live smoke (gated через CB_LMS_LIVE_SMOKE_Y15).

Эти тесты выполняют реальные external write-paths:
- magic-link → Resend API → реальное письмо на provided email
- /tg/init → реальный Telegram bot_token HMAC verify
- /vk/callback → реальный VK ID 2.0 OAuth code exchange

Запускаются оператором перед production deploy (см. tech-spec Y-1.5 §16).
По умолчанию skip — нужны реальные credentials в `.env`:

| Тест | Что нужно |
|------|-----------|
| `test_live_first_time_magic_link` | RESEND_API_KEY + verified domain или onboarding@resend.dev |
| `test_live_first_time_tg_init` | TG_BOT_TOKEN_FOR_INITDATA + валидная initData строка |
| `test_live_first_time_vk_callback` | VK_ID_CLIENT_ID/SECRET + свежий authorization code |

Запуск:
    set CB_LMS_LIVE_SMOKE_Y15=1
    pytest tests/test_y15_live_smoke.py -v -s

Без `CB_LMS_LIVE_SMOKE_Y15` — skipped автоматически.
"""
import os

import pytest
from httpx import AsyncClient


_GATE = os.getenv("CB_LMS_LIVE_SMOKE_Y15")
_skip_reason = "Live smoke gated; set CB_LMS_LIVE_SMOKE_Y15=1 to enable"


@pytest.mark.asyncio
@pytest.mark.skipif(not _GATE, reason=_skip_reason)
async def test_live_first_time_magic_link(client: AsyncClient) -> None:
    """Real Resend → email отправлен → operator консьюмит токен из письма → /me показывает профиль.

    Operator handoff (см. operator-runbook R-001):
    1. Перед запуском убедиться что RESEND_API_KEY в .env валидный.
    2. SMTP_FROM указывает на verified domain (или sandbox onboarding@resend.dev).
    3. Запустить тест → проверить почтовый ящик SMOKE_TARGET_EMAIL.
    4. Скопировать token из URL ссылки → POST /api/v1/auth/magic-link/verify body={token}.
    5. Использовать access_token → GET /api/v1/me → ожидать {id, email, tg_id:null, is_service:false}.

    Этот тест автоматизирован только до отправки письма; consume — manual operator step.
    """
    target = os.getenv("SMOKE_TARGET_EMAIL")
    if not target:
        pytest.skip("SMOKE_TARGET_EMAIL not set")

    resp = await client.post(
        "/api/v1/auth/magic-link/send",
        json={"email": target},
    )
    assert resp.status_code == 202, resp.text
    print(f"\n[live smoke] magic-link sent to {target}; check inbox.")
    print("[live smoke] consume + /me — manual operator steps (см. docstring).")


@pytest.mark.asyncio
@pytest.mark.skipif(not _GATE, reason=_skip_reason)
async def test_live_first_time_tg_init(client: AsyncClient) -> None:
    """Real TG initData (валидный HMAC) → auto-create user + /me с tg-identity.

    Operator handoff (см. operator-runbook R-003):
    1. Получить валидную initData строку из Telegram WebApp test bot:
       window.Telegram.WebApp.initData (в DevTools открытого Mini App).
    2. Установить env SMOKE_TG_INITDATA=<строка>.
    3. Запустить тест → ожидать 200 + JSON с access_token + Set-Cookie session.
    4. Проверить identity_link kind='tg' создан + users.tg_id заполнен.

    Без SMOKE_TG_INITDATA — skip с инструкцией.
    """
    init_data = os.getenv("SMOKE_TG_INITDATA")
    if not init_data:
        pytest.skip("SMOKE_TG_INITDATA not set; см. docstring для получения initData")

    resp = await client.post(
        "/api/v1/auth/tg/init",
        json={"init_data": init_data},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data and "refresh_token" in data
    print(f"\n[live smoke] tg/init OK; access_token={data['access_token'][:8]}…")


@pytest.mark.asyncio
@pytest.mark.skipif(not _GATE, reason=_skip_reason)
async def test_live_first_time_vk_callback(client: AsyncClient) -> None:
    """Real VK ID 2.0 callback (PKCE) → auto-create user (с/без email scope).

    Operator handoff (см. operator-runbook R-002):
    1. Запустить frontend SPW → нажать «Войти через VK» → дойти до redirect URL.
    2. Перехватить из URL: code, code_verifier (из sessionStorage SPW), device_id.
    3. Установить env: SMOKE_VK_CODE, SMOKE_VK_VERIFIER, SMOKE_VK_DEVICE_ID.
    4. Запустить тест в течение 10 минут (code expires fast).

    Альтернативный mock-flow для CI — через httpx_mock (НЕ покрывает реальный VK API).
    """
    code = os.getenv("SMOKE_VK_CODE")
    verifier = os.getenv("SMOKE_VK_VERIFIER")
    device_id = os.getenv("SMOKE_VK_DEVICE_ID")
    if not all([code, verifier, device_id]):
        pytest.skip(
            "SMOKE_VK_{CODE,VERIFIER,DEVICE_ID} not set; "
            "получить из живого SPW VK callback в течение 10 мин"
        )

    resp = await client.post(
        "/api/v1/auth/vk/callback",
        json={"code": code, "code_verifier": verifier, "device_id": device_id},
    )
    assert resp.status_code in (200, 409), resp.text
    if resp.status_code == 409:
        body = resp.json()
        assert body["detail"]["error"] == "identity_conflict"
        print(f"\n[live smoke] vk/callback returned expected 409: {body['detail']['conflict_kind']}")
    else:
        data = resp.json()
        assert "access_token" in data
        print(f"\n[live smoke] vk/callback OK; access_token={data['access_token'][:8]}…")
