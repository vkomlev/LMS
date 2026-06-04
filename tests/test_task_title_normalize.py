"""tsk-107: TaskContent.title — пустая/пробельная строка нормализуется в None.

Причина: D4-конвейер ContentBackbone (kompege/yandex/polyakov/sdamgia) писал
title="", из-за чего фронт SPW (`tc.title ?? "Задача #N"`) не подставлял
автоподпись — задание выглядело безымянным. Валидатор на границе LMS приводит
"" и пробельные строки к None, обеспечивая единый вид списка заданий.
"""
from __future__ import annotations

import pytest

from app.schemas.task_content import TaskContent


def _title(value: object) -> object:
    return TaskContent(type="SA_COM", stem="x", title=value).title  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["", "   ", "\t", "\n  \n"])
def test_empty_or_whitespace_title_becomes_none(raw: str) -> None:
    """Пустая или пробельная строка названия → None."""
    assert _title(raw) is None


def test_none_title_stays_none() -> None:
    """Явный None остаётся None."""
    assert _title(None) is None


def test_absent_title_defaults_none() -> None:
    """Отсутствие ключа title → дефолт None."""
    assert TaskContent(type="SA_COM", stem="x").title is None


def test_real_title_preserved() -> None:
    """Непустое название сохраняется без изменений (включая внешние пробелы не трогаем по содержимому)."""
    assert _title("Задание 9.1 — агрегатные функции") == "Задание 9.1 — агрегатные функции"
