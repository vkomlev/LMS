# -*- coding: utf-8 -*-
"""
tsk-262: сравнение ответа-кода через канон AST (CheckingService._matches_short_answer).

Проверяют три вещи:
1. Валидные записи одной и той же программы засчитываются (кавычки, пробелы
   вокруг синтаксиса, f-строки) — это чинит ложные незачёты.
2. Регистр имён остаётся существенным: print(I) ≠ print(i).
3. Неразбираемый ответ или эталон не даёт ложного незачёта — сравнение
   откатывается на текстовые шаги и ведёт себя ровно как до правки.
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

import pytest

from app.services.checking_service import CheckingService

_matches = CheckingService._matches_short_answer
_canon = CheckingService._canon_code

# Нормализация, которая стоит на код-заданиях прода после tsk-261 (без lower),
# плюс объявленный явно режим кода.
CODE_STEPS = ["trim", "code_ast", "strip_punctuation", "collapse_spaces"]
# Та же нормализация без объявления режима кода — контроль, что без флага
# поведение не меняется.
TEXT_STEPS = ["trim", "strip_punctuation", "collapse_spaces"]


# ---------- Валидные записи одной программы → зачёт ----------

@pytest.mark.parametrize(
    "value, accepted, why",
    [
        ("print(slovo .lower())", "print(slovo.lower())", "пробел перед точкой — доказанный ложный незачёт с прода"),
        ("print(slovo.find('и'))", 'print(slovo.find("и"))', "кавычки эквивалентны в Python"),
        ('print(f"Мне {vozrast } лет!")', 'print(f"Мне {vozrast} лет!")', "пробел внутри фигурных скобок f-строки"),
        ("print( 'привет' )", "print('привет')", "пробелы внутри скобок"),
        ("x=[1,2,3]", "x = [1, 2, 3]", "пробелы вокруг операторов и запятых"),
        ("print(i)  # вывод", "print(i)", "комментарий не влияет на программу"),
        ("  print(i)  ", "print(i)", "обрамляющие пробелы"),
    ],
)
def test_valid_code_variants_accepted(value: str, accepted: str, why: str) -> None:
    assert _matches(value, accepted, CODE_STEPS) is True, why


# ---------- Регистр значим ----------

def test_case_sensitive_variable_name():
    """print(I) — другая переменная, чем print(i). Незачёт (жалоба QA B2)."""
    assert _matches("print(I)", "print(i)", CODE_STEPS) is False


def test_case_sensitive_string_literal():
    """Текст вывода различается регистром — незачёт (жалоба QA B6)."""
    assert _matches('print("ты нашёл")', 'print("Ты нашёл")', CODE_STEPS) is False


def test_different_program_rejected():
    assert _matches("print(slovo.upper())", "print(slovo.lower())", CODE_STEPS) is False


# ---------- Fallback: не парсится → текстовое сравнение, без ложного незачёта ----------

def test_fallback_when_both_sides_are_fragments():
    """'for i in range(10):' — не самостоятельное выражение. Обе стороны не
    парсятся → текстовый путь → точное совпадение засчитывается как раньше."""
    assert _canon("for i in range(10):") is None
    assert _matches("for i in range(10):", "for i in range(10):", CODE_STEPS) is True


def test_fallback_fragment_mismatch_still_rejected():
    assert _matches("for i in range(9):", "for i in range(10):", CODE_STEPS) is False


def test_fallback_when_answer_is_not_python():
    """Эталон '.env' — имя файла, не Python. Верный ответ обязан пройти."""
    assert _canon(".env") is None
    assert _matches(".env", ".env", CODE_STEPS) is True


def test_fallback_when_answer_is_sql():
    sql = "SELECT COUNT(*) FROM enrollments WHERE course_id = 5;"
    assert _canon(sql) is None
    assert _matches(sql, sql, CODE_STEPS) is True


def test_fallback_when_only_reference_is_broken():
    """Эталон не парсится, ответ парсится → AST-путь невозможен, текстовый работает."""
    assert _matches("+=", "+=", CODE_STEPS) is True


def test_unparseable_answer_against_parseable_reference_rejected():
    """Ответ не парсится и по буквам не совпадает с эталоном → незачёт обоими путями."""
    assert _canon("print(slovo.upper()") is None
    assert _matches("print(slovo.upper()", "print(slovo.lower())", CODE_STEPS) is False


def test_known_limit_missing_colon_still_passes():
    """ГРАНИЦА ЗАДАЧИ (зафиксирована намеренно, не баг реализации).

    'def privet()' без двоеточия сейчас засчитывается за 'def privet():':
    strip_punctuation стирает двоеточие с обеих сторон. Подтверждено на проде —
    task_results.id=2083, is_correct=true.

    AST это не чинит: эталон 'def privet():' — фрагмент, он не парсится, поэтому
    AST-путь не включается, а fallback по построению аддитивен (только добавляет
    зачёты, никогда не снимает). Устранение таких ложных ЗАЧЁТОВ — другой класс
    правки (строгий режим без fallback), он меняет вердикты и требует
    отдельного замера и решения оператора. tsk-262 закрывает ложные НЕЗАЧЁТЫ.
    """
    assert _matches("def privet()", "def privet():", CODE_STEPS) is True
    assert _matches("def privet()", "def privet():", TEXT_STEPS) is True  # так было и до правки


# ---------- Без флага поведение не меняется ----------

def test_without_flag_ast_path_is_off():
    """Тот же ложный незачёт остаётся, пока задание не объявило code_ast —
    режим не включается сам по себе."""
    assert _matches("print(slovo .lower())", "print(slovo.lower())", TEXT_STEPS) is False


def test_without_flag_text_comparison_unchanged():
    assert _matches("print(i)", "print(i)", TEXT_STEPS) is True


def test_flag_never_makes_check_stricter():
    """Всё, что проходило текстом, проходит и с code_ast: AST — только
    дополнительный путь к зачёту."""
    pairs = [
        ("print(slovo.find('и'))", 'print(slovo.find("и"))'),
        ("for i in range(10):", "for i in range(10):"),
        (".env", ".env"),
        ("print(i)", "print(i)"),
    ]
    for value, accepted in pairs:
        if _matches(value, accepted, TEXT_STEPS):
            assert _matches(value, accepted, CODE_STEPS) is True, (value, accepted)


# ---------- _canon_code: устойчивость ----------

def test_canon_returns_none_instead_of_raising():
    for bad in ["", ":", "+=", "[50, 60", "21 · (14 6 -5 6)", "def f()", "'"]:
        assert _canon(bad) is None or isinstance(_canon(bad), str)


def test_canon_equivalent_forms():
    assert _canon("x=[1,2]") == _canon("x = [1, 2]")


def test_canon_preserves_case():
    assert _canon("print(I)") != _canon("print(i)")
