# -*- coding: utf-8 -*-
"""Разбор хвоста «Критерий приёмки / Принято, если» (tsk-947, scripts/tsk947_move_criteria.py)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "tsk947_move_criteria", Path(__file__).resolve().parents[1] / "scripts" / "tsk947_move_criteria.py"
)
mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mod
assert _SPEC.loader is not None
_SPEC.loader.exec_module(mod)


@pytest.mark.parametrize(
    ("stem", "head", "tail"),
    [
        ("Вопрос.\n\nКритерий приёмки: названы две вещи.", "Вопрос.", "Критерий приёмки: названы две вещи."),
        ("Вопрос.\n\nПринято, если: (1) a; (2) b.", "Вопрос.", "Принято, если: (1) a; (2) b."),
        # Без двоеточия — 8 заданий прода, первый прогон их пропустил.
        ("Вопрос.\n\nПринято, если названа активность.", "Вопрос.", "Принято, если названа активность."),
        # Хвост через пробел после точки, а не абзацем (#9245).
        ("Вопрос. Критерий приёмки: принято, если x.", "Вопрос.", "Критерий приёмки: принято, если x."),
    ],
)
def test_split_stem(stem: str, head: str, tail: str) -> None:
    assert mod.split_stem(stem) == (head, tail)


def test_split_stem_without_marker_is_untouched() -> None:
    assert mod.split_stem("Обычное условие. Принято решение — не метка.") == (
        "Обычное условие. Принято решение — не метка.", None,
    )


def test_review_note_drops_label_keeps_body() -> None:
    assert mod.review_note_text("Принято, если: знаки не попали в слова; регистр снят.") == (
        "Знаки не попали в слова; регистр снят."
    )


def test_criteria_from_tail_splits_and_routes_reject() -> None:
    parts = mod.criteria_from_tail(
        "Принято, если: (1) указана пустая проверка; (2) названо ожидание системы. "
        "Не засчитывать: две проверки одной категории."
    )
    assert parts["must"] == ["Указана пустая проверка", "Названо ожидание системы"]
    assert parts["reject"] == ["Не засчитывать: две проверки одной категории"]


def test_criteria_from_tail_splits_long_item_by_sentences() -> None:
    long = "Принято, если: " + " ".join(f"Требование номер {i} описано достаточно подробно, чтобы строка стала длинной." for i in range(8))
    assert len(long) > 500
    parts = mod.criteria_from_tail(long)
    assert all(10 <= len(p) <= 500 for p in parts["must"])
    assert len(parts["must"]) >= 2
