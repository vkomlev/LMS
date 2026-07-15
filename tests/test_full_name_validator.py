"""Табличный unit-тест валидатора ФИО (tsk-223, Фаза A).

Проверяет `validate_full_name`: нормализацию (strip + схлопывание пробелов),
валидные кейсы (двусложное имя, отчество, двойная фамилия через дефис) и
невалидные (пусто, пробелы, одно слово, латиница, цифры, подчёркивание,
превышение длины, emoji).
"""
import pytest

from app.services.full_name_validator import (
    MAX_LENGTH,
    validate_full_name,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Иванов Иван", "Иванов Иван"),
        ("Иванова Мария Петровна", "Иванова Мария Петровна"),
        ("Мамин-Сибиряк Дмитрий", "Мамин-Сибиряк Дмитрий"),
        ("Ёлкина Аёна", "Ёлкина Аёна"),
        # Нормализация: краевые + повторные пробелы схлопываются.
        ("  Иванов   Иван  ", "Иванов Иван"),
        ("Иванов\tИван", "Иванов Иван"),
    ],
)
def test_validate_full_name_valid(raw: str, expected: str) -> None:
    """Валидные ФИО возвращаются нормализованными."""
    assert validate_full_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",                       # пусто
        "   ",                    # только пробелы
        "Иван",                   # одно слово (нет фамилии/имени)
        "Viktor Komlev",          # латиница
        "Иванов Иван2",           # цифра
        "Иванов_Иван",            # подчёркивание (и одно слово после нормализации)
        "Иванов Иван!",           # знак препинания
        "Иванов 😀",              # emoji
        "-Иванов Иван",           # токен начинается с дефиса
        "Иванов- Иван",           # висячий дефис
        "И " + "в" * (MAX_LENGTH + 5),  # превышение длины
    ],
)
def test_validate_full_name_invalid(raw: str) -> None:
    """Невалидные ФИО бросают ValueError с русским сообщением."""
    with pytest.raises(ValueError) as exc_info:
        validate_full_name(raw)
    # Сообщение непустое и на русском (кириллица присутствует).
    assert str(exc_info.value)
    assert any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in str(exc_info.value))


def test_validate_full_name_collapses_multiple_spaces() -> None:
    """Явная проверка схлопывания нескольких пробелов между токенами."""
    assert validate_full_name("Иванова     Мария") == "Иванова Мария"


def test_full_name_str_annotated_type_reuses_rule() -> None:
    """Переиспользуемый Pydantic-тип FullNameStr использует то же правило."""
    from pydantic import BaseModel, ValidationError

    from app.services.full_name_validator import FullNameStr

    class _Model(BaseModel):
        full_name: FullNameStr

    assert _Model(full_name="  Иванов  Иван ").full_name == "Иванов Иван"
    with pytest.raises(ValidationError):
        _Model(full_name="Viktor Komlev")
