"""tsk-1123: единое правило поиска людей по имени.

Запрос режется на слова; каждое слово должно встретиться в имени (подстрокой,
без учёта регистра), условия через AND — порядок слов неважен: «ксения
скударнова» находит «скударнова ксения алексеевна». Буква «ё» приравнена к «е»
с обеих сторон. `%`/`_`/`\\` экранируются (tsk-565/566).

Одно правило для всех мест поиска людей: ORM (`name_match_clause`) и сырой SQL
(`name_match_sql`). Зеркало на клиенте — SPW `lib/people-search.ts`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.sql.elements import ColumnElement

from app.utils.ilike import escape_ilike


def normalize_name_text(raw: str) -> str:
    """Привести текст к виду для сравнения: нижний регистр, «ё» → «е»."""
    return raw.lower().replace("ё", "е")


def split_search_words(q: str | None) -> list[str]:
    """Разбить запрос на нормализованные слова (пустые отброшены)."""
    return normalize_name_text(q or "").split()


def name_match_clause(column: Any, q: str | None) -> ColumnElement[bool] | None:
    """ORM-условие «каждое слово запроса есть в колонке». None — если слов нет."""
    words = split_search_words(q)
    if not words:
        return None
    folded = func.translate(column, "ёЁ", "еЕ")
    return and_(*(folded.ilike(f"%{escape_ilike(w)}%", escape="\\") for w in words))


def name_match_sql(column_sql: str, q: str | None, param_prefix: str) -> tuple[str, dict[str, str]]:
    """Кусок сырого SQL (без ведущего AND) и параметры для него.

    Пустой запрос → `("TRUE", {})`. `column_sql` — доверенное имя колонки из
    кода, не пользовательский ввод.
    """
    words = split_search_words(q)
    if not words:
        return "TRUE", {}
    parts: list[str] = []
    params: dict[str, str] = {}
    for i, word in enumerate(words):
        name = f"{param_prefix}_{i}"
        parts.append(f"translate({column_sql}, 'ёЁ', 'еЕ') ILIKE :{name} ESCAPE '\\'")
        params[name] = f"%{escape_ilike(word)}%"
    return "(" + " AND ".join(parts) + ")", params
