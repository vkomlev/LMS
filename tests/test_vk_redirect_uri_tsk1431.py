"""tsk-1431: адрес возврата VK по домену кабинета (learn/study/family), белый список."""
import pytest

from app.core.config import Settings
from app.services.auth import vk_oauth_service
from app.services.auth.vk_oauth_service import resolve_redirect_uri


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Настройки с боевым адресом learn и списком хостов по умолчанию."""
    monkeypatch.setenv("VK_ID_REDIRECT_URI", "https://learn.victor-komlev.ru/auth/vk/callback")
    monkeypatch.delenv("VK_ID_ALLOWED_REDIRECT_HOSTS", raising=False)
    return Settings()


def test_none_falls_back_to_env(settings: Settings) -> None:
    """Старый клиент без поля — прежний адрес из окружения."""
    assert resolve_redirect_uri(None, settings) == settings.vk_id_redirect_uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://learn.victor-komlev.ru/auth/vk/callback",
        "https://study.victor-komlev.ru/auth/vk/callback",
        "https://family.victor-komlev.ru/auth/vk/callback",
        "https://study.victor-komlev.ru/me/identity/vk/link",
    ],
)
def test_allowed(settings: Settings, uri: str) -> None:
    """Каждый домен кабинета и оба пути возврата принимаются как есть."""
    assert resolve_redirect_uri(uri, settings) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example.com/auth/vk/callback",
        "https://study.victor-komlev.ru.evil.com/auth/vk/callback",
        "http://study.victor-komlev.ru/auth/vk/callback",
        "https://study.victor-komlev.ru/other",
        "https://study.victor-komlev.ru/auth/vk/callback?x=1",
        "https://study.victor-komlev.ru:8443/auth/vk/callback",
        "https://user@study.victor-komlev.ru/auth/vk/callback",
    ],
)
def test_rejected(settings: Settings, uri: str) -> None:
    """Чужой хост, http, чужой путь, query, порт, userinfo — отказ."""
    with pytest.raises(ValueError):
        resolve_redirect_uri(uri, settings)


def test_env_override_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Список хостов задаётся переменной; хост VK_ID_REDIRECT_URI разрешён всегда."""
    monkeypatch.setenv("VK_ID_REDIRECT_URI", "https://pilot.victor-komlev.ru/auth/vk/callback")
    monkeypatch.setenv("VK_ID_ALLOWED_REDIRECT_HOSTS", "only.example.ru")
    s = Settings()
    assert resolve_redirect_uri("https://only.example.ru/auth/vk/callback", s)
    assert resolve_redirect_uri("https://pilot.victor-komlev.ru/me/identity/vk/link", s)
    with pytest.raises(ValueError):
        resolve_redirect_uri("https://learn.victor-komlev.ru/auth/vk/callback", s)


async def test_exchange_sends_given_redirect(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """В VK уходит именно переданный адрес, а не адрес из окружения."""
    sent: dict = {}

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "a"}

    class _Client:
        def __init__(self, *a: object, **k: object) -> None: ...

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *a: object) -> None: ...

        async def post(self, url: str, data: dict) -> _Resp:
            sent.update(data)
            return _Resp()

    monkeypatch.setattr(vk_oauth_service.httpx, "AsyncClient", _Client)
    uri = "https://study.victor-komlev.ru/auth/vk/callback"
    await vk_oauth_service.exchange_code("c", "v", "d", settings, uri)
    assert sent["redirect_uri"] == uri
