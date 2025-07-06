# app/core/config.py

import os
from typing import List


class Settings:
    """
    Примитивная конфигурация приложения для MVP:
    всё берётся напрямую из os.environ.
    """
    def __init__(self):
        # обязательные переменные
        try:
            self.database_url: str = os.environ["DATABASE_URL"]
            raw_keys = os.environ["VALID_API_KEYS"]
        except KeyError as e:
            raise RuntimeError(f"Missing required environment variable: {e}")

        # необязательная, с дефолтом
        self.log_level: str = os.environ.get("LOG_LEVEL", "INFO")

        # валидируем и парсим список через запятую
        self.valid_api_keys: List[str] = [
            key.strip() for key in raw_keys.split(",") if key.strip()
        ]

        if not self.valid_api_keys:
            raise RuntimeError("VALID_API_KEYS must contain at least one key")

