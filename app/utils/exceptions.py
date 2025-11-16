# app/utils/exceptions.py
from __future__ import annotations

from typing import Any, Optional


class DomainError(Exception):
    """
    Базовая доменная ошибка приложения.
    Её перехватывает глобальный хэндлер и возвращает предсказуемый HTTP-ответ.
    """

    def __init__(self, detail: str, *, status_code: int = 400, payload: Optional[dict[str, Any]] = None) -> None:
        """
        Args:
            detail: Человеко-понятное описание проблемы (не для отладки).
            status_code: Желаемый HTTP-статус (400 по умолчанию).
            payload: Доп. данные, безопасные к отдаче клиенту (опционально).
        """
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.payload = payload or {}
