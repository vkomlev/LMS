# tests/test_tsk899_code_ast_cpp_operators.py
"""
tsk-899: `code_ast` разбирает только Python. На C-подобном коде (Arduino/C++)
`ast.parse` падает, сравнение откатывается на `strip_punctuation` — а тот
стирал операторы сравнения/логики (`<`, `>`, `==`, `!=`, `&&`, `||`) как
пунктуацию. `urovenj > 90` и `urovenj < 90` после нормализации становились
одним и тем же «urovenj 90», и код с ПОЛНОСТЬЮ ОБРАТНОЙ логикой сравнения
засчитывался как верный.

Фикс: в откате `code_ast` (и только там — остальные 2000+ заданий без этого
шага не затронуты) операторы заменяются на именованные плейсхолдеры ДО
`strip_punctuation`, поэтому переживают его и остаются различимыми.
"""

from app.services.checking_service import CheckingService

STEPS = ["trim", "strip_punctuation", "collapse_spaces", "code_ast"]

ARDUINO_REFERENCE = (
    'if (urovenj > 90) {\n'
    '  Serial.println("Перелив");\n'
    '} else if (urovenj > 70) {\n'
    '  Serial.println("Заполнено");\n'
    '} else {\n'
    '  Serial.println("Норма");\n'
    '}'
)


def _match(answer: str, reference: str = ARDUINO_REFERENCE, steps=None) -> bool:
    return CheckingService._matches_short_answer(answer, reference, steps or STEPS)


# ---------- Корневой случай: реверс логики принимался как верный ----------

def test_reference_matches_itself():
    assert _match(ARDUINO_REFERENCE, ARDUINO_REFERENCE)


def test_reversed_comparison_is_rejected():
    """Задание 9614 (tsk-899): '>' -> '<' — полностью обратная логика сигнализации."""
    reversed_code = ARDUINO_REFERENCE.replace(">", "<")
    assert not _match(reversed_code)


def test_off_by_boundary_operator_is_rejected():
    """'>' -> '>=' — другая граница включения, другая логика."""
    off_by_one = ARDUINO_REFERENCE.replace("urovenj > 90", "urovenj >= 90")
    assert not _match(off_by_one)


def test_equality_swapped_for_not_equal_is_rejected():
    assert not _match(
        "if (x == 1) { Serial.println(\"a\"); }",
        "if (x != 1) { Serial.println(\"a\"); }",
    )


def test_and_swapped_for_or_is_rejected():
    assert not _match(
        "if (a && b) { Serial.println(\"a\"); }",
        "if (a || b) { Serial.println(\"a\"); }",
    )


# ---------- Верный ответ по-прежнему проходит ----------

def test_operator_without_surrounding_spaces_still_matches():
    """Ученик пишет `urovenj>90` без пробелов — логика та же, зачёт сохраняется."""
    tight = ARDUINO_REFERENCE.replace("urovenj > 90", "urovenj>90").replace(
        "urovenj > 70", "urovenj>70"
    )
    assert _match(tight)


def test_real_student_attempt_from_9614_stays_rejected_for_the_real_reason():
    """
    Реальная сдача ученика 4509 (id=20102) по заданию 9614: код верный, но
    написал «Затоплено» вместо «Заполнено» из условия — это НЕ дефект
    чекера (учитель зачёл вручную по своему усмотрению). Фикс не должен ни
    исправлять, ни ломать эту причину отказа — она про слово, не про оператор.
    """
    student_attempt = ARDUINO_REFERENCE.replace("Заполнено", "Затоплено")
    assert not _match(student_attempt)


# ---------- Остальные 2000+ заданий без code_ast не затронуты ----------

def test_non_code_ast_task_unaffected_by_operator_protection():
    """Без шага 'code_ast' в normalization операторы по-прежнему обычная пунктуация."""
    steps_without_code_ast = ["trim", "strip_punctuation", "collapse_spaces"]
    # '>' и '<' стираются как раньше — вне code_ast защиты нет и не должно быть.
    assert CheckingService._normalize_text(
        "x > 90", steps_without_code_ast
    ) == CheckingService._normalize_text("x < 90", steps_without_code_ast)


def test_protect_code_operators_is_noop_without_operators():
    assert CheckingService._protect_code_operators("print(a)") == "print(a)"
