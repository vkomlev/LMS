"""Человекочитаемый заголовок задания для интерфейсов преподавателя (tsk-298 follow-up).

Задания в LMS почти никогда не имеют осмысленного `task_content.title`
(см. tsk-107: непустой title лишь у ~105 из 7001), зато `stem` (условие)
заполнен у всех. Раньше teacher-портал показывал сырой `external_uid`
(`authored:vstupitelnye-it-vuz:...#q4`) — неинтуитивно для преподавателя.

Этот модуль строит подпись по приоритету:
    curated title → очищенный stem → external_uid → «Задание #id».
Так преподаватель видит начало условия задачи, а не технический слаг.
"""
from __future__ import annotations

import html
import re
from typing import Optional

# Снятие HTML-тегов: у ~3000 заданий stem приходит с разметкой
# (`<html><body>`, `<p>`, `<pre><code>` из sdamgia/wp-импорта).
_TAG_RE = re.compile(r"<[^>]+>")
# Схлопывание любых пробельных последовательностей (включая переносы строк и
# неразрывный пробел \xa0 из &nbsp;) в один пробел.
_WS_RE = re.compile(r"\s+")

# Максимальная длина подписи в UI. stem бывает до сотен КБ (программные задачи) —
# без обрезки в поле заголовка попал бы весь текст условия.
TITLE_MAX_LEN = 80


def _clean_stem(stem: Optional[str]) -> str:
    """Привести stem к однострочному тексту: снять HTML-теги, раскодировать
    HTML-сущности (`&quot;`, `&lt;` и т.п.), схлопнуть пробелы.

    Порядок важен: сперва снимаем теги на сырой разметке, затем раскодируем
    сущности — иначе литеральные `&lt;`, экранированные автором, превратились бы
    в теги и были бы съедены.
    """
    if not stem:
        return ""
    no_tags = _TAG_RE.sub(" ", stem)
    unescaped = html.unescape(no_tags)
    return _WS_RE.sub(" ", unescaped).strip()


def humanize_task_title(
    task_id: int,
    title: Optional[str],
    stem: Optional[str],
    external_uid: Optional[str],
    max_len: int = TITLE_MAX_LEN,
) -> str:
    """Собрать человекочитаемый заголовок задания.

    :param task_id: ID задания (для крайнего fallback «Задание #id»).
    :param title: `task_content->>'title'` (обычно пусто/NULL).
    :param stem: `task_content->>'stem'` — условие задачи (заполнено всегда).
    :param external_uid: технический слаг задания.
    :param max_len: длина обрезки для title/stem-подписи.
    :returns: непустую строку-подпись.

    Приоритет: непустой curated `title` → очищенный `stem` → `external_uid`
    → «Задание #id». `external_uid` не обрезается (это идентификатор), крайний
    fallback короткий.
    """
    label: Optional[str] = None
    if title and title.strip():
        label = _WS_RE.sub(" ", title.strip())
    else:
        cleaned = _clean_stem(stem)
        if cleaned:
            label = cleaned
    if label is None:
        if external_uid:
            return external_uid
        return f"Задание #{task_id}"
    if len(label) > max_len:
        return label[:max_len].rstrip() + "…"
    return label
