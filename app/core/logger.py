# app/core/logger.py

import os
import logging
from logging.config import dictConfig
from app.core.config import Settings

def setup_logging() -> None:
    """
    Настройка корневого логгера:
    - консольный вывод;
    - ротация файловых логов в папке 'logs', по 5 МБ, хранить 5 бэкапов.
    Вызывать в startup FastAPI, до первого использования логера.
    """
    settings = Settings()

    # Папка для логов
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")

    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "level": settings.log_level,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "default",
                "level": settings.log_level,
                "filename": log_file,
                "maxBytes": 5 * 1024 * 1024,     # 5 МБ
                "backupCount": 5,                # хранить 5 архивных файлов
                "encoding": "utf-8",
            },
        },
        "root": {
            "handlers": ["console", "file"],
            "level": settings.log_level,
        },
    })

    # Отдельно можно понизить/повысить уровень для SQLAlchemy и прочих библиотек:
    logging.getLogger("sqlalchemy.engine").setLevel(settings.log_level)
