"""Request-ID middleware + ContextVar + logging Filter.

Сквозная трассировка HTTP-запроса:
1. `RequestIDMiddleware` — на входе берёт `X-Request-ID` header (от клиента
   или upstream-балансера), либо генерит uuid4. Возвращает тот же id в
   response.headers (для клиента и логов nginx).
2. ContextVar `_request_id_ctx` — async-safe доступ к id из любой точки
   обработки запроса (services, repos, audit), не таская параметр явно.
3. `RequestIDFilter` — `logging.Filter`, инжектит `request_id` в каждый
   `LogRecord` → `JsonFormatter` пишет в `logs/app.log`. Контракт ключа
   совпадает с тем что `audit_service.log_event` кладёт в `details`.

После Этапа 4 единый поиск:
    grep '"request_id":"abc-..."' logs/app.log     -- весь HTTP-flow
    SELECT * FROM audit_event WHERE details->>'request_id'='abc-...'
"""
from __future__ import annotations

import contextvars
import logging
import os
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


logger = logging.getLogger(__name__)

_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# tsk-621: порог, после которого запрос попадает в лог как медленный.
# Инцидент 17.08.2026 разбирали вслепую именно потому, что длительность
# запроса нигде не фиксировалась: по логам было видно, что API стоит, но не
# видно, какой обработчик его держит. Значение правится через окружение.
_SLOW_REQUEST_SECONDS = float(os.getenv("SLOW_REQUEST_SECONDS", "3"))


def _log_if_slow(request: Request, elapsed: float) -> None:
    """Записать в лог запрос, который обрабатывался дольше порога."""
    if elapsed < _SLOW_REQUEST_SECONDS:
        return
    logger.warning(
        "slow request %.1fs %s %s",
        elapsed,
        request.method,
        request.url.path,
    )


def get_request_id() -> str | None:
    """Текущий request_id (или None, если вызвано вне HTTP-контекста)."""
    return _request_id_ctx.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware: положить uuid4 или клиентский X-Request-ID в ContextVar."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id")
        rid = incoming if incoming else str(uuid.uuid4())
        token = _request_id_ctx.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _log_if_slow(request, time.perf_counter() - started)
            _request_id_ctx.reset(token)


class RequestIDFilter(logging.Filter):
    """Filter: инжектит request_id в record (используется в logger.py dictConfig)."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "request_id") or getattr(record, "request_id", None) is None:
            record.request_id = get_request_id()
        return True
