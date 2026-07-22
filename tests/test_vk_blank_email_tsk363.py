"""tsk-363: VK вернул email пустой строкой → users.email = '' → 500 на /api/v1/users/.

Прод-инцидент 2026-07-22: VK userinfo отдал email="" (не отсутствие ключа,
а пустая строка). Проверка `if email:` пропускала нормализацию, '' уходила
в БД и роняла весь ответ списка пользователей на валидации EmailStr.
Существующий test_first_time_vk_no_email_creates_user_email_null не ловил
это, потому что подавал email=None напрямую, минуя разбор userinfo.

Покрываем оба конца: разбор ответа VK и схему выдачи.
"""

import httpx
import pytest

from app.schemas.users import UserRead
from app.services.auth import vk_oauth_service


class _FakeResponse:
    """Минимальная замена httpx.Response для мока VK userinfo."""

    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Заглушка httpx.AsyncClient, отдающая заранее заданный ответ VK."""

    payload: dict = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(self.payload)


@pytest.mark.parametrize(
    "raw_email",
    ["", "   ", None],
    ids=["пустая строка", "только пробелы", "ключ отсутствует"],
)
@pytest.mark.asyncio
async def test_fetch_vk_userinfo_blank_email_becomes_none(
    monkeypatch: pytest.MonkeyPatch, raw_email: str | None
) -> None:
    """Пустая почта из VK в любом виде превращается в None, не в ''."""
    user_payload: dict = {"user_id": "446456584", "first_name": "Иван", "last_name": "Петров"}
    if raw_email is not None:
        user_payload["email"] = raw_email
    _FakeClient.payload = {"user": user_payload}
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    info = await vk_oauth_service.fetch_vk_userinfo("token_stub")

    assert info["email"] is None
    assert info["user_id"] == "446456584"
    assert info["full_name"] == "Иван Петров"


@pytest.mark.asyncio
async def test_fetch_vk_userinfo_real_email_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Непустая почта по-прежнему нормализуется: обрезка пробелов + нижний регистр."""
    _FakeClient.payload = {
        "user": {"user_id": "1", "email": "  User@Example.RU ", "first_name": "А", "last_name": "Б"}
    }
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    info = await vk_oauth_service.fetch_vk_userinfo("token_stub")

    assert info["email"] == "user@example.ru"


@pytest.mark.parametrize("stored", ["", "   "], ids=["пустая строка", "только пробелы"])
def test_user_read_blank_email_from_db_does_not_break(stored: str) -> None:
    """Историческая пустая строка в БД читается как None, а не роняет ответ."""
    user = UserRead.model_validate(
        {
            "id": 4505,
            "email": stored,
            "full_name": "Мамедов Джемаль Рафаил оглы",
            "tg_id": None,
            "created_at": "2026-07-22T07:14:17.004Z",
        }
    )

    assert user.email is None


def test_user_read_valid_email_preserved() -> None:
    """Корректная почта не теряется из-за валидатора."""
    user = UserRead.model_validate(
        {
            "id": 1,
            "email": "student@example.com",
            "full_name": "Студент",
            "tg_id": None,
            "created_at": "2026-07-22T07:14:17.004Z",
        }
    )

    assert user.email == "student@example.com"


def test_user_read_invalid_email_still_rejected() -> None:
    """Валидатор гасит только пустоту — мусорный адрес по-прежнему отвергается."""
    with pytest.raises(ValueError):
        UserRead.model_validate(
            {
                "id": 2,
                "email": "не-почта",
                "full_name": "Кто-то",
                "tg_id": None,
                "created_at": "2026-07-22T07:14:17.004Z",
            }
        )
