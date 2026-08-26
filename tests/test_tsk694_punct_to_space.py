# tests/test_tsk694_punct_to_space.py
"""
tsk-694: шаг `strip_punctuation` ставит на место знака ПРОБЕЛ, а не удаляет его.

Раньше знак исчезал, из-за чего пробел вокруг него становился значимым:
`urovenj=map(...)` давало `urovenjmap...`, а `urovenj = map(...)` — `urovenj map...`,
и верный ответ заворачивался. Больнее всего это било по заданиям с кодом, где
`code_ast` не спасает (разбирается только Python).

Два исключения сохраняют старое поведение — там знак не разделитель, а часть
значения: внутри чисто числового куска (`2.5`, `25/12/2024`, `192.168.1.0`)
и внутри слова (`кто-то`, `don't`).
"""

import pytest

from app.services.checking_service import CheckingService

FULL = ["trim", "lower", "strip_punctuation", "collapse_spaces"]


def _n(value: str, steps=None) -> str:
    return CheckingService._normalize_text(value, steps or FULL)


def _match(answer: str, reference: str, steps=None) -> bool:
    return CheckingService._matches_short_answer(answer, reference, steps or FULL)


# ---------- Корневой случай: код без пробелов вокруг знаков ----------

def test_arduino_code_without_spaces_matches_reference():
    """Задание 9598 (курс 1407): ученик писал код без пробелов — ответ верный."""
    assert _match(
        "urovenj=map(syroe,0,1023,0,50);",
        "urovenj = map(syroe, 0, 1023, 0, 50);",
    )


def test_python_call_spacing_is_insignificant():
    assert _match("print( 'привет' )", "print('привет')")


def test_colon_glued_to_value():
    """Задание 9573: «Обороты:1500» и «Обороты: 1500» — один и тот же ответ."""
    assert _match("Обороты:1500", "Обороты: 1500")


def test_comma_separated_list_becomes_words():
    assert _n("создать,читать,обновить,удалить") == "создать читать обновить удалить"


def test_numbered_list_with_dots():
    assert _match("1 apple\n2 banana", "1.apple\n2.banana")


# ---------- Исключение 1: знак внутри числа ----------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("2.5", "25"),
        ("2,5", "25"),
        ("25/12/2024", "25122024"),
        ("192.168.1.0", "19216810"),
        ("1.2.3.4", "1234"),
    ],
)
def test_punctuation_inside_number_is_removed(value: str, expected: str):
    assert _n(value) == expected


def test_space_instead_of_decimal_point_is_still_wrong():
    """Главный риск правки: «2 5» не должно засчитываться за «2.5»."""
    assert not _match("2 5", "2.5")


def test_decimal_comma_still_matches_decimal_point():
    """Ученик пишет по-русски «2,5», эталон «2.5» — как и до правки, зачёт."""
    assert _match("2,5", "2.5")


def test_number_separator_inside_code_stays_a_separator():
    """В куске с буквами запятая — разделитель аргументов, а не разряд числа."""
    assert _n("map(a,0,1023)") == "map a 0 1023"


# ---------- Исключение 2: знак внутри слова ----------

def test_hyphen_inside_word_is_removed():
    assert _n("кто-то") == "ктото"
    assert not _match("кто то", "кто-то")


def test_apostrophe_inside_word_is_removed():
    assert _n("don't") == "dont"


def test_hyphen_between_words_is_a_separator():
    """Тире с пробелами — разделитель, а не часть слова."""
    assert _n("слово — другое") == "слово другое"


# ---------- Проверка не ослабла ----------

def test_wrong_answer_still_wrong():
    assert not _match("print(a)", "print(b)")


def test_missing_argument_still_wrong():
    assert not _match("map(syroe,0,1023)", "map(syroe, 0, 1023, 0, 50)")


def test_step_is_opt_in():
    """Без шага в normalization текст не трогается."""
    assert _n("а, б", ["trim", "lower"]) == "а, б"


# ---------- Шаг самодостаточен ----------

def test_step_collapses_spaces_by_itself():
    """
    Без `collapse_spaces` результат тот же: иначе на месте знаков оставались бы
    рваные двойные пробелы и задание без этого шага стало бы строже.
    """
    assert _n("a , b", ["strip_punctuation"]) == "a b"
    assert _n("a , b", FULL) == "a b"
