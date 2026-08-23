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

import asyncio
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


# tsk-644: сколько записей журнала пишем одновременно. Потолок нужен на случай
# шторма: 18 августа медленным стал КАЖДЫЙ запрос в окне, и без ограничения
# журнал наблюдения сам добавил бы нагрузки ровно там, где и так плохо.
_SLOW_WRITE_LIMIT = int(os.getenv("SLOW_REQUEST_WRITE_LIMIT", "8"))
_SLOW_WRITE_TIMEOUT = float(os.getenv("SLOW_REQUEST_WRITE_TIMEOUT", "5"))
_slow_writes_inflight = 0

#: Ссылки на незавершённые задачи записи. Без них задача, созданная
#: `create_task` и никем не удерживаемая, может быть собрана сборщиком мусора
#: посреди работы — задокументированное поведение asyncio, из-за которого запись
#: пропадала бы редко и невоспроизводимо.
_slow_write_tasks: set[asyncio.Task] = set()


def _route_template(request: Request) -> str:
    """Шаблон роута (`/api/v1/attempts/{attempt_id}/answers`), иначе сам путь.

    Группировать сводку по фактическим путям бесполезно: тысяча попыток даст
    тысячу строк по одному разу вместо одной строки «этот обработчик медленный».
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else request.url.path


async def _record_slow_request(
    method: str, path: str, elapsed: float, status_code: int | None, rid: str | None
) -> None:
    """Дописать строку в `slow_request`. Тихо и мимо ответа пользователю.

    Ошибку писать в лог как warning нельзя: если БД недоступна, каждая такая
    попытка добавит ещё одну строку в лог, который и так уже полон. Пишем
    отладкой — а факт недоступности БД видно по самим запросам.
    """
    global _slow_writes_inflight
    try:
        from sqlalchemy import text

        from app.db.session import async_session_factory

        async def _write() -> None:
            async with async_session_factory() as db:
                await db.execute(
                    text(
                        "INSERT INTO slow_request "
                        "(method, path, duration_ms, status_code, request_id) "
                        "VALUES (:m, :p, :d, :s, :r)"
                    ),
                    {
                        "m": method[:10],
                        "p": path[:300],
                        "d": int(elapsed * 1000),
                        "s": status_code,
                        "r": rid[:64] if rid else None,
                    },
                )
                await db.commit()

        await asyncio.wait_for(_write(), timeout=_SLOW_WRITE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — журнал наблюдения не ломает запрос
        logger.debug("tsk-644: медленный запрос не записан (%s)", type(exc).__name__)
    finally:
        _slow_writes_inflight -= 1


def _log_if_slow(
    request: Request, elapsed: float, status_code: int | None = None
) -> None:
    """Записать запрос, который обрабатывался дольше порога.

    Две записи с разными читателями: строка в `logs/app.log` — для разбора
    руками на боевой машине, строка в таблице `slow_request` — для еженедельной
    сводки (tsk-644). Сводку собирает `scripts/check_slow_requests.py`; она ходит
    с машины оператора, до файла лога не достаёт и читает только БД.
    """
    if elapsed < _SLOW_REQUEST_SECONDS:
        return
    path = _route_template(request)
    logger.warning(
        "slow request %.1fs %s %s",
        elapsed,
        request.method,
        path,
    )
    global _slow_writes_inflight
    if _slow_writes_inflight >= _SLOW_WRITE_LIMIT:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Петли нет (запрос вне обычного контекста) — молча пропускаем: строка
        # журнала не стоит того, чтобы из-за неё падал ответ.
        return
    # Считаем ЗДЕСЬ, а не внутри корутины: та начнёт выполняться только когда до
    # неё дойдёт очередь планировщика, и при шторме потолок пропустил бы разом
    # столько задач, сколько запросов успело завершиться, — то есть не сработал бы.
    _slow_writes_inflight += 1
    task = loop.create_task(
        _record_slow_request(
            request.method, path, elapsed, status_code, get_request_id()
        )
    )
    _slow_write_tasks.add(task)
    task.add_done_callback(_slow_write_tasks.discard)


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
        status_code: int | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _log_if_slow(request, time.perf_counter() - started, status_code)
            _request_id_ctx.reset(token)


class RequestIDFilter(logging.Filter):
    """Filter: инжектит request_id в record (используется в logger.py dictConfig)."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "request_id") or getattr(record, "request_id", None) is None:
            record.request_id = get_request_id()
        return True
