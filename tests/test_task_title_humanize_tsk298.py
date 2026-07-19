"""tsk-298 follow-up: humanize_task_title — человекочитаемый заголовок задания.

Раньше teacher-портал показывал сырой external_uid
(`authored:vstupitelnye-it-vuz:...#q4`). Хелпер строит подпись по приоритету:
curated title → очищенный stem (снятие HTML, декодирование сущностей, обрезка)
→ external_uid → «Задание #id».
"""
from __future__ import annotations

import pytest

from app.utils.task_title import TITLE_MAX_LEN, humanize_task_title


def test_curated_title_wins() -> None:
    """Непустой title имеет высший приоритет."""
    assert humanize_task_title(1, "Округление вниз", "какой-то stem", "wp:task:x") == "Округление вниз"


def test_empty_title_falls_to_stem() -> None:
    """Пустой/пробельный title игнорируется — берётся stem."""
    assert humanize_task_title(1, "", "Какого цикла не существует?", "uid") == "Какого цикла не существует?"
    assert humanize_task_title(1, "   ", "Какого цикла не существует?", "uid") == "Какого цикла не существует?"


def test_none_title_falls_to_stem() -> None:
    """title=None → stem."""
    assert humanize_task_title(1, None, "Условие задачи", "uid") == "Условие задачи"


def test_stem_html_stripped() -> None:
    """HTML-теги снимаются, остаётся чистый текст."""
    stem = '<html><body><p class="left_margin">В файле приведён фрагмент базы данных</p></body></html>'
    assert humanize_task_title(1, None, stem, "uid") == "В файле приведён фрагмент базы данных"


def test_stem_entities_unescaped() -> None:
    """HTML-сущности раскодируются."""
    stem = "imya = &quot;Алекс&quot; что делает строка?"
    assert humanize_task_title(1, None, stem, "uid") == 'imya = "Алекс" что делает строка?'


def test_stem_whitespace_collapsed() -> None:
    """Переносы строк и множественные пробелы схлопываются в один пробел."""
    stem = "Напишите программу,\n   которая\t\tзапрашивает   число"
    assert humanize_task_title(1, None, stem, "uid") == "Напишите программу, которая запрашивает число"


def test_long_stem_truncated_with_ellipsis() -> None:
    """Длинный stem обрезается до max_len + многоточие."""
    stem = "а" * 500
    result = humanize_task_title(1, None, stem, "uid")
    assert len(result) == TITLE_MAX_LEN + 1  # +1 символ многоточия
    assert result.endswith("…")
    assert result == "а" * TITLE_MAX_LEN + "…"


def test_short_stem_not_truncated() -> None:
    """Короткий stem не обрезается и не получает многоточие."""
    stem = "Короткое условие"
    assert humanize_task_title(1, None, stem, "uid") == "Короткое условие"
    assert "…" not in humanize_task_title(1, None, stem, "uid")


def test_pure_html_stem_falls_to_external_uid() -> None:
    """stem без текстового содержимого (только теги) → external_uid."""
    assert humanize_task_title(1, None, "<br><hr>", "wp:task:x") == "wp:task:x"


def test_empty_stem_falls_to_external_uid() -> None:
    """Пустой stem + есть external_uid → external_uid (не обрезается)."""
    assert humanize_task_title(1, None, "", "authored:long-slug#q4") == "authored:long-slug#q4"
    assert humanize_task_title(1, None, None, "authored:long-slug#q4") == "authored:long-slug#q4"


def test_no_title_no_stem_no_uid_falls_to_id() -> None:
    """Всё пусто → «Задание #id»."""
    assert humanize_task_title(42, None, None, None) == "Задание #42"
    assert humanize_task_title(42, "", "", "") == "Задание #42"


def test_real_feedback_case() -> None:
    """Кейс из фидбэка оператора: вместо external_uid — текст условия."""
    external = "authored:vstupitelnye-it-vuz:1-2-harakteristiki-komponentov#q4"
    stem = "Какой узел компьютера характеризуют тактовой частотой и числом ядер? Впиши одно слово в именительном падеже."
    result = humanize_task_title(7418, None, stem, external)
    assert result != external
    assert result.startswith("Какой узел компьютера")


@pytest.mark.parametrize("max_len", [10, 40, 80])
def test_custom_max_len(max_len: int) -> None:
    """max_len настраивается."""
    stem = "с" * 200
    result = humanize_task_title(1, None, stem, "uid", max_len=max_len)
    assert result == "с" * max_len + "…"
