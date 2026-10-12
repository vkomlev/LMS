"""tsk-1306: снятие разметки из условия не съедает шаблоны формата ответа.

Задание 362: «выведите строку формата `<страна>: <столица>`». Прежний
`<[^>]+>` вырезал шаблон как тег, и методист в очереди вычитки подсказок, а
вместе с ним и модель в промпте, видели «формата ` : `».
"""
from __future__ import annotations

import pytest

from app.api.v1.tasks_extra import _stem_preview
from app.services.grading_criteria_draft import clean_stem
from app.utils.task_title import clean_stem_text

STEM_362 = "Для каждой пары выведите строку формата `<страна>: <столица>`."


@pytest.mark.parametrize("clean", [clean_stem, clean_stem_text, _stem_preview])
def test_cyrillic_placeholder_survives(clean) -> None:
    """Шаблон с кириллицей в угловых скобках остаётся в тексте."""
    assert "`<страна>: <столица>`" in clean(STEM_362)


@pytest.mark.parametrize("clean", [clean_stem, clean_stem_text, _stem_preview])
def test_real_tags_still_stripped(clean) -> None:
    """Настоящая разметка, комментарий и doctype по-прежнему снимаются."""
    raw = "<!DOCTYPE html><p>Выведите <code>x</code> в формате <b><имя></b></p><!-- c -->"
    out = clean(raw)
    assert "<p>" not in out and "<code>" not in out and "<b>" not in out
    assert "DOCTYPE" not in out and "<!--" not in out
    assert "<имя>" in out
