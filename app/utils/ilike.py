"""tsk-565: экранирование спецсимволов ILIKE-паттерна PostgreSQL."""
from __future__ import annotations


def escape_ilike(raw: str) -> str:
    """Экранировать `\\`, `%`, `_` — иначе буквальные % / _ в пользовательском
    запросе сработали бы как wildcard ILIKE, а не как искомый текст.

    Вызывающий сам оборачивает результат в `%...%` и передаёт `escape='\\'`
    в `.ilike()`.
    """
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
