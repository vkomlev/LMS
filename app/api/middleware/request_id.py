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
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
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
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _request_id_ctx.reset(token)


class RequestIDFilter(logging.Filter):
    """Filter: инжектит request_id в record (используется в logger.py dictConfig)."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "request_id") or getattr(record, "request_id", None) is None:
            record.request_id = get_request_id()
        return True
