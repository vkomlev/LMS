"""Content-Type enforcement middleware (ADR-tsk-161, P0-фикс).

Проблема: FastAPI/Starlette парсят JSON-тело запроса (`Request.json()` →
`json.loads(await self.body())`) независимо от заголовка `Content-Type`.
Это значит, что запрос с `Content-Type: text/plain` (CORS-"простой" заголовок,
не требующий preflight) и JSON-содержимым внутри всё равно будет успешно
распознан как JSON и обработан эндпоинтом — CORS `allow_origins` в этом случае
не защищает вообще, так как preflight не происходит.

Практическое следствие (tsk-161): widescoped cookie (`Domain=victor-komlev.ru`)
доступна для запросов, инициированных JS с любого поддомена того же сайта
(`SameSite=Lax` не блокирует same-site запросы) — включая WordPress на
`www.victor-komlev.ru`. Комбинация "widescoped cookie + отсутствие Content-Type
enforcement" даёт реально эксплуатируемый CSRF-путь (session-riding) уже сейчас.

Фикс: для запросов с телом (POST/PUT/PATCH, Content-Length > 0) требуем
`Content-Type` строго из allowlist:
  - `application/json`      — обычные JSON-эндпоинты (подавляющее большинство)
  - `multipart/form-data`   — file-upload эндпоинты (attempts/materials/messages)
Всё остальное (включая `text/plain`, `application/x-www-form-urlencoded`,
отсутствие заголовка) — `415 Unsupported Media Type` до того, как тело вообще
будет прочитано/распарсено.

`multipart/form-data` тоже технически CORS-"простой" тип, но его парсинг
(`Request.form()`) уже сам по себе Content-Type-aware в Starlette — эта
middleware закрывает только конкретный обходной путь через JSON-парсинг,
не полную защиту от CSRF (см. tsk-161 план, Q2 — CSRF-токен как отдельный,
не блокирующий рубеж).
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("api.content_type_guard")

_METHODS_WITH_BODY = frozenset({"POST", "PUT", "PATCH"})
_ALLOWED_CONTENT_TYPES = ("application/json", "multipart/form-data")


class ContentTypeGuardMiddleware(BaseHTTPMiddleware):
    """Отклоняет тела запросов с Content-Type вне allowlist (415)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _METHODS_WITH_BODY:
            # Transfer-Encoding: chunked не даёт Content-Length, но тело есть —
            # без этой проверки chunked-запрос обходил бы guard целиком.
            is_chunked = "chunked" in request.headers.get("transfer-encoding", "").lower()
            try:
                content_length = int(request.headers.get("content-length", "0"))
            except ValueError:
                # Мусорное значение заголовка — не доверяем, считаем что тело может быть.
                content_length = 1
            has_body = is_chunked or content_length > 0
            if has_body:
                content_type = request.headers.get("content-type", "")
                if not content_type.lower().startswith(_ALLOWED_CONTENT_TYPES):
                    logger.warning(
                        "content_type_guard: отклонён запрос method=%s path=%s content-type=%r",
                        request.method, request.url.path, content_type,
                    )
                    return JSONResponse(
                        status_code=415,
                        content={
                            "detail": (
                                "Unsupported Media Type. Ожидается "
                                "application/json или multipart/form-data."
                            )
                        },
                    )
        return await call_next(request)
