# app/core/logger.py

import json
import logging
import os
from logging.config import dictConfig
from logging.handlers import RotatingFileHandler

from app.core.config import Settings


# Поля, которые могут попадать в record.extra через `logger.info(..., extra={...})`
# и подлежат включению в JSON-вывод. Совпадает с колонками таблицы `audit_event`
# (`event_type`, `user_id`, `ip`, `user_agent`, `details`) плюс трассировочные
# `request_id` и `audit_id` (FK на запись audit_event при duplication).
_JSON_EXTRA_KEYS: tuple[str, ...] = (
    "event_type",
    "user_id",
    "request_id",
    "audit_id",
    "details",
    "ip",
    "user_agent",
)


class JsonFormatter(logging.Formatter):
    """Структурированный JSON-формат для file-handler.

    Совпадает по именам ключей с таблицей `audit_event` — это позволяет
    единым `grep '"event_type":"teacher.review.graded"'` по `logs/app.log`
    и `SELECT ... FROM audit_event` получать симметричный результат.

    Console-handler намеренно оставлен в текстовом виде (для читаемости
    при локальной разработке).
    """

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: dict[str, object] = {
            "ts": self.formatTime(record, self.default_time_format),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _JSON_EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _WinSafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler без падения на Windows при занятом файле."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # Другой процесс держит app.log открытым — пропускаем ротацию.
            pass


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
            "text": {
                # Локальная читаемая консоль
                "format": "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
            },
            "json": {
                # Структурированный file-log для production-парсинга
                "()": JsonFormatter,
            },
        },
        "filters": {
            "request_id": {
                "()": "app.api.middleware.request_id.RequestIDFilter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "text",
                "filters": ["request_id"],
                "level": settings.log_level,
            },
            "file": {
                "()": _WinSafeRotatingFileHandler,
                "formatter": "json",
                "filters": ["request_id"],
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

    # SQLAlchemy engine на INFO логит каждый BEGIN/ROLLBACK/SELECT — это объёмный шум
    # в дев-БД при нагрузочных тестах (~100 строк/сек). Для prod-сценариев интересны
    # только WARNING+ (медленные, висящие транзакции). Override через env LOG_LEVEL_SQL.
    sql_level = os.environ.get("LMS_LOG_LEVEL_SQL", "WARNING").upper()
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)
