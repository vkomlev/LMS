# -*- coding: utf-8 -*-
"""tsk-325: коэрция task_content.media = [] → None.

Импортированные ЕГЭ-задания (1080) хранят media как пустой список, а схема ждёт
объект TaskMedia (dict) или null. Без коэрции TaskContent.model_validate падает
на строке attempts.py:446 и роняет приём ответа 500 — раньше, чем срабатывает
F5 по solution_rules. Найдено живым прод-прогоном tsk-325.
"""
from __future__ import annotations

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
from pydantic import ValidationError

from app.schemas.task_content import TaskContent, TaskMedia


def test_empty_list_media_coerced_to_none():
    """media = [] (реальный формат импорта ЕГЭ) → None, валидация проходит."""
    tc = TaskContent.model_validate({"type": "SA_COM", "stem": "x", "media": []})
    assert tc.media is None


def test_absent_media_stays_none():
    tc = TaskContent.model_validate({"type": "SA_COM", "stem": "x"})
    assert tc.media is None


def test_null_media_stays_none():
    tc = TaskContent.model_validate({"type": "SA_COM", "stem": "x", "media": None})
    assert tc.media is None


def test_dict_media_parsed():
    tc = TaskContent.model_validate(
        {"type": "SA_COM", "stem": "x", "media": {"image_url": "http://a/b.png"}}
    )
    assert isinstance(tc.media, TaskMedia)
    assert tc.media.image_url == "http://a/b.png"


def test_nonempty_list_media_still_rejected():
    """Непустой список — реальная потеря данных, коэрция НЕ прячет её (валидация падает)."""
    with pytest.raises(ValidationError):
        TaskContent.model_validate(
            {"type": "SA_COM", "stem": "x", "media": [{"image_url": "http://a/b.png"}]}
        )
