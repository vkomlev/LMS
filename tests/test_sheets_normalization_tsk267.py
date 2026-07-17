"""
tsk-267: колонка `normalization` в листе Tasks шаблона Sheets-импорта.

До этой задачи парсер хардкодил ["trim","lower"] для ЛЮБОГО SA/SA_COM: задание
с ответом-кодом получало ложные незачёты (лишний пробел) и засчитывало
`IMPORT RANDOM` за `import random`. Вид ответа теперь объявляет автор.
"""
from __future__ import annotations

import pytest

from app.services.sheets_parser_service import SheetsParserService
from app.utils.exceptions import DomainError


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "external_uid": "TEST-SA-267",
        "type": "SA",
        "stem": "Как привести слово к нижнему регистру?",
        "accepted_answers": "print(slovo.lower())",
        "max_score": "10",
    }
    row.update(overrides)
    return row


def _normalization(row: dict[str, str]) -> list[str]:
    _, rules, _ = SheetsParserService().parse_task_row(row)
    assert rules.short_answer is not None
    return list(rules.short_answer.normalization)


# ---------- Дефолт: пустая колонка не меняет поведение старых таблиц ----------

def test_empty_column_keeps_pre_tsk267_default():
    assert _normalization(_row()) == ["trim", "lower"]


def test_column_absent_keeps_pre_tsk267_default():
    row = _row()
    row.pop("normalization", None)
    assert _normalization(row) == ["trim", "lower"]


# ---------- Алиасы по таблице выбора assignment-rules.md § 4b ----------

@pytest.mark.parametrize("alias", ["code", "код", "CODE", " Код "])
def test_code_alias_gives_ast_without_lower(alias: str):
    steps = _normalization(_row(normalization=alias))
    assert steps == ["trim", "strip_punctuation", "collapse_spaces", "code_ast"]
    # Регистр в Python значим: с lower `IMPORT RANDOM` прошёл бы за `import random`.
    assert "lower" not in steps


@pytest.mark.parametrize("alias", ["text", "текст", "TEXT"])
def test_text_alias_gives_full_text_steps(alias: str):
    assert _normalization(_row(normalization=alias)) == [
        "trim",
        "lower",
        "strip_punctuation",
        "collapse_spaces",
    ]


# ---------- Явный список шагов ----------

def test_explicit_list_comma_separated():
    assert _normalization(_row(normalization="trim, code_ast")) == ["trim", "code_ast"]


def test_explicit_list_pipe_separated_preserves_author_order():
    assert _normalization(_row(normalization="trim | collapse_spaces | lower")) == [
        "trim",
        "collapse_spaces",
        "lower",
    ]


# ---------- Опечатка отклоняет строку, а не проверяется по дефолту ----------

def test_typo_rejects_row():
    with pytest.raises(DomainError) as exc:
        _normalization(_row(normalization="trim, code-ast"))
    assert "normalization_invalid" in str(exc.value.detail)
    assert "code-ast" in str(exc.value.detail)
    assert exc.value.status_code == 400


def test_unknown_alias_rejects_row():
    with pytest.raises(DomainError):
        _normalization(_row(normalization="python"))


def test_separator_only_value_rejects_row():
    with pytest.raises(DomainError):
        _normalization(_row(normalization=" , | "))
