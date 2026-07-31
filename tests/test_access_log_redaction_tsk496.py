"""tsk-496: маскировка боевого api_key в access-логе uvicorn.

Рецидив tsk-402 (TG_LMS, httpx-логгер) на серверной стороне: `uvicorn.access` —
отдельный логгер (`propagate=False`, свой обработчик в stdout), который наш
`dictConfig` (app/core/logger.py::setup_logging) не настраивает и не видит.
На VPS `lms.service` перенаправляет stdout процесса в `/var/log/lms/app.log` —
без фильтра боевой сервисный ключ (legacy `?api_key=`-авторизация ботов
TG_LMS) уходит туда открытым текстом в каждой строке request line.

Покрытие:
  1. api_key в query-строке маскируется независимо от точного значения секрета
  2. точное значение секрета из VALID_API_KEYS маскируется даже без "api_key="
  3. запросы без api_key не мутируются
  4. критично: реальный uvicorn.logging.AccessFormatter распаковывает
     record.args как кортеж из 5 позиционных элементов — фильтр обязан
     сохранять длину/порядок args, иначе formatMessage падает с ValueError
     и access-лог перестаёт писаться вовсе (было воспроизведено при разборе)
"""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from uvicorn.logging import AccessFormatter

from app.core.logger import AccessLogRedactingFilter


def _make_access_logger(name: str, secrets: tuple[str, ...] = ()) -> tuple[logging.Logger, io.StringIO]:
    """Собрать логгер с той же парой (AccessFormatter + наш фильтр), что uvicorn.access в проде."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.filters.clear()
    logger.handlers.clear()
    logger.addFilter(AccessLogRedactingFilter(secrets=secrets))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    logger.addHandler(handler)
    return logger, stream


def _emit_access_record(logger: logging.Logger, path: str, status: int = 200) -> None:
    """Точная сигнатура вызова из uvicorn/protocols/http/h11_impl.py:RequestResponseCycle.send."""
    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:12345",
        "GET",
        path,
        "1.1",
        status,
    )


def test_api_key_query_param_masked():
    logger, stream = _make_access_logger("test.tsk496.query_key")
    _emit_access_record(logger, "/api/v1/teacher/help-requests/pending-count?api_key=prod-secret-xyz")

    out = stream.getvalue()
    assert "prod-secret-xyz" not in out
    assert "api_key=***REDACTED***" in out


def test_exact_secret_value_masked_even_without_query_marker():
    """Защита от новых транспортов секрета (заголовок в пути, form-data и т.п.) — не только query."""
    logger, stream = _make_access_logger("test.tsk496.exact_secret", secrets=("prod-secret-xyz",))
    _emit_access_record(logger, "/api/v1/users/?token=prod-secret-xyz")

    out = stream.getvalue()
    assert "prod-secret-xyz" not in out
    assert "***REDACTED***" in out


def test_request_without_api_key_not_mutated():
    logger, stream = _make_access_logger("test.tsk496.no_key", secrets=("prod-secret-xyz",))
    _emit_access_record(logger, "/health")

    out = stream.getvalue()
    assert out == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200 OK\n'


def test_real_access_formatter_does_not_crash_on_five_tuple_unpack():
    """Регресс: обнуление record.args (как в httpx-варианте tsk-402) ломает
    AccessFormatter.formatMessage — он делает
    `client_addr, method, full_path, http_version, status_code = record.args`.
    Фильтр обязан редактировать элементы args на месте, сохраняя 5-tuple.
    """
    logger, stream = _make_access_logger("test.tsk496.five_tuple", secrets=("prod-secret-xyz",))
    _emit_access_record(logger, "/api/v1/checking/?api_key=prod-secret-xyz", status=403)

    out = stream.getvalue()
    assert "--- Logging error ---" not in out
    assert "ValueError" not in out
    assert "prod-secret-xyz" not in out
    assert '"GET /api/v1/checking/?api_key=***REDACTED*** HTTP/1.1" 403' in out


def test_status_code_and_method_survive_redaction():
    """Побочный элемент кортежа (метод, статус) не должен пострадать от подмены пути."""
    logger, stream = _make_access_logger("test.tsk496.other_fields", secrets=("prod-secret-xyz",))
    _emit_access_record(logger, "/api/v1/attempts/?api_key=prod-secret-xyz", status=500)

    out = stream.getvalue()
    assert out.startswith('127.0.0.1:12345 - "GET ')
    assert "500" in out
