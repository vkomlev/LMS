"""Unit-тесты me_service.mask_value — маскирование identity values."""
import pytest

from app.services.me_service import mask_value


@pytest.mark.parametrize(
    "value,expected",
    [
        ("victor@gmail.com", "vic***@gmail.com"),
        ("ab@b.com", "***@b.com"),  # local короче 3
        ("a@b", "***@b"),
        ("VICTOR@gmail.com", "VIC***@gmail.com"),  # masking сохраняет регистр (нормализация на уровне БД)
    ],
)
def test_mask_email(value: str, expected: str) -> None:
    assert mask_value("email", value) == expected


def test_mask_email_no_at_fallback() -> None:
    # Edge case: value без @ (corrupted data) — best-effort
    assert mask_value("email", "broken") == "bro***"
    assert mask_value("email", "ab") == "***"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("123456789", "***6789"),
        ("12345", "***2345"),
        ("123", "***123"),
    ],
)
def test_mask_tg(value: str, expected: str) -> None:
    assert mask_value("tg", value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("123456789012", "12345678..."),
        ("12345678", "12345678"),  # ровно 8 — без ...
        ("1234", "1234"),  # короче 8 — как есть
    ],
)
def test_mask_vk(value: str, expected: str) -> None:
    assert mask_value("vk", value) == expected
