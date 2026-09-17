# -*- coding: utf-8 -*-
"""
Регрессионные тесты для CheckingService._normalize_text.

Покрывают все 4 токена (trim, lower, strip_punctuation, collapse_spaces),
их комбинации, backward compat (без strip_punctuation — старое поведение
не меняется), Unicode (кириллица), edge-cases (пустая строка, только
пунктуация, NBSP).
"""
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.services.checking_service import CheckingService


_normalize = CheckingService._normalize_text


# ---------- Отдельные токены ----------

def test_trim_only():
    assert _normalize("  hello  ", ["trim"]) == "hello"


def test_lower_only():
    assert _normalize("HeLLo", ["lower"]) == "hello"


def test_collapse_spaces_only():
    assert _normalize("a   b    c", ["collapse_spaces"]) == "a b c"


def test_strip_punctuation_only_ascii():
    assert _normalize("Hello, world!", ["strip_punctuation"]) == "Hello world"


def test_strip_punctuation_only_cyrillic():
    assert _normalize("Да, Нет. Да, да", ["strip_punctuation"]) == "Да Нет Да да"


def test_strip_punctuation_keeps_underscores():
    """\\w включает подчёркивание — оно не удаляется."""
    assert _normalize("a_b_c!", ["strip_punctuation"]) == "a_b_c"


def test_strip_punctuation_keeps_digits():
    assert _normalize("12, 34, 56.", ["strip_punctuation"]) == "12 34 56"


def test_strip_punctuation_russian_dashes_and_quotes():
    """
    Русское тире, кавычки-ёлочки, троеточие — всё пунктуация.

    tsk-694: шаг сам схлопывает пробелы, поэтому здесь один пробел, а не два.
    Раньше на месте знаков пробела не оставалось, и лишний пробел приходил от
    исходной строки; теперь результат не зависит от того, объявлен ли
    `collapse_spaces`.
    """
    assert _normalize("«слово» — и…", ["strip_punctuation"]) == "слово и"


# ---------- Основной use-case Subsystem C ----------

def test_subsystem_c_happy_path():
    steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
    assert _normalize("  Да, Нет. Да, да  ", steps) == "да нет да да"


def test_subsystem_c_matches_reference():
    """Ответ студента и эталон после нормализации совпадают."""
    steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
    student = "Да, Нет. Да, да"
    reference = "Да Нет Да Да"
    assert _normalize(student, steps) == _normalize(reference, steps)


# ---------- Backward compat ----------

def test_legacy_three_tokens_unchanged():
    """Без strip_punctuation поведение идентично старому."""
    steps = ["trim", "lower", "collapse_spaces"]
    assert _normalize("  HeLLo   World  ", steps) == "hello world"


def test_legacy_punctuation_not_stripped_without_token():
    steps = ["trim", "lower", "collapse_spaces"]
    assert _normalize("Да, Нет.", steps) == "да, нет."


# ---------- Edge cases ----------

def test_empty_string():
    steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
    assert _normalize("", steps) == ""


def test_only_punctuation():
    steps = ["strip_punctuation", "collapse_spaces"]
    assert _normalize("!!! ??? ...", steps) == ""


def test_nbsp_handling():
    """NBSP — whitespace; collapse_spaces должен его схлопывать."""
    assert _normalize("a b", ["collapse_spaces"]) == "a b"


def test_unknown_token_ignored():
    """Незнакомый токен не ломает функцию."""
    assert _normalize("Hello", ["trim", "unknown_step"]) == "Hello"


def test_empty_steps_returns_input_as_is():
    assert _normalize("  A, b  ", []) == "  A, b  "


def test_idempotent():
    """Повторное применение не меняет результат."""
    steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
    once = _normalize("  A, b!  ", steps)
    twice = _normalize(once, steps)
    assert once == twice == "a b"


def test_order_strip_before_collapse():
    """Порядок: strip_punctuation до collapse_spaces.
    'a, b' → strip → 'a  b' → collapse → 'a b'.
    Если бы collapse шёл до strip — получили бы 'a, b' (unchanged) → 'a  b' (двойной пробел).
    """
    steps = ["strip_punctuation", "collapse_spaces"]
    assert _normalize("a, b", steps) == "a b"


# ---------- strip_brackets_commas (tsk-977) ----------

def test_strip_brackets_commas_only():
    steps = ["strip_brackets_commas"]
    assert _normalize("[0, 1, 0]", steps) == "0 1 0"


def test_strip_brackets_commas_keeps_minus():
    """Минус — значимая часть эталона (координаты, отметки на доске), не трогается."""
    steps = ["strip_brackets_commas"]
    assert _normalize("[1, 1, 1, 1, -1, 1, 1, 1]", steps) == "1 1 1 1 -1 1 1 1"


def test_strip_brackets_commas_multiline_python_list_matches_reference():
    """Основной use-case tsk-974/id-10367: print(list) построчно против эталона."""
    steps = ["trim", "lower", "strip_brackets_commas", "collapse_spaces"]
    student = (
        "[0, 1, 0, 0, 1, 0, 0, 1]\n[0, 0, 1, 0, 1, 0, 1, 0]\n"
        "[1, 1, 1, 1, -1, 1, 1, 1]"
    )
    reference = (
        "0 1 0 0 1 0 0 1\n0 0 1 0 1 0 1 0\n1 1 1 1 -1 1 1 1\n"
    )
    assert _normalize(student, steps) == _normalize(reference, steps)


def test_strip_brackets_commas_not_applied_without_token():
    """Backward compat: без явного шага скобки/запятые не трогаются."""
    steps = ["trim", "lower", "collapse_spaces"]
    assert _normalize("[0, 1]", steps) == "[0, 1]"


def test_strip_brackets_commas_combined_with_strip_punctuation():
    """Оба шага вместе не ломаются: скобки/запятые снимает первый шаг,
    strip_punctuation после него работает над уже очищенным текстом."""
    steps = ["strip_brackets_commas", "strip_punctuation", "collapse_spaces"]
    assert _normalize("[a, b]!", steps) == "a b"


def test_strip_brackets_commas_only_punctuation_returns_empty():
    steps = ["strip_brackets_commas"]
    assert _normalize("[,]", steps) == ""


def test_strip_brackets_commas_breaks_decimal_comma_with_strip_punctuation():
    """Задокументированная ловушка (tsk-977, боевой прогон нашёл 5 регрессий):

    На дробных эталонах с точкой (`62.11`) при включённом strip_punctuation
    запятая — русский десятичный разделитель, и strip_punctuation нарочно
    склеивает её с цифрами («62,11» → «6211», как и «62.11» → «6211»).
    strip_brackets_commas, поставленный ДО strip_punctuation, успевает
    превратить запятую в пробел раньше — число рвётся на два токена, и
    ответ, который раньше засчитывался, перестаёт совпадать с эталоном.
    Отсюда правило: не подключать этот шаг заданиям с дробным эталоном
    (`ShortAnswerRules.normalization`, docstring `_strip_brackets_commas`).
    """
    steps = ["trim", "lower", "strip_brackets_commas", "strip_punctuation", "collapse_spaces"]
    student = "62,11"
    reference = "62.11"
    # Без strip_brackets_commas эти строки совпали бы (обе → "6211").
    assert _normalize(student, steps) != _normalize(reference, steps)
