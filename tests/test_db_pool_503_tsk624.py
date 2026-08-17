"""tsk-624: исчерпание пула подключений отдаёт 503 с `Retry-After`, а не 500.

Покрытие:
  1. retry_after_within_window        — пауза лежит между половиной ожидания и полным
  2. retry_after_has_jitter           — есть разброс: подряд идущие значения не одинаковы
  3. retry_after_never_below_one      — крошечное ожидание не даёт паузу 0 секунд
  4. pool_timeout_returns_503         — отказ пула → 503 + заголовок Retry-After
  5. retry_after_header_matches_body  — заголовок и поле тела согласованы
  6. other_db_error_still_500         — прочие ошибки базы ведут себя как раньше (500)
  7. log_keeps_queuepool_marker       — в логе остаётся подстрока QueuePool (по ней в
     разборе аварии считали масштаб и время волны)
  8. real_app_registers_handler       — обработчик подключён к боевому приложению
  9. real_app_exposes_retry_after     — CORS отдаёт заголовок наружу, иначе браузер
     его не увидит и отступить не сможет
 10. audit_actor_reraises_pool_timeout — метка аудита больше не глотает отказ пула
     (иначе запрос ждёт подключение дважды и ответ приходит через два таймаута)
 11. audit_actor_still_soft_fails      — прочие сбои простановки метки по-прежнему
     не роняют мутацию
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeout

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.api.error_handlers import db_pool_timeout_handler, retry_after_seconds

#: Текст, который SQLAlchemy кладёт в исключение при исчерпании пула —
#: снят с боевого лога 17.08.2026 и подтверждён прогоном на dev-БД.
POOL_TIMEOUT_MESSAGE = (
    "QueuePool limit of size 10 overflow 20 reached, "
    "connection timed out, timeout 30.00"
)


# ─── расчёт паузы ─────────────────────────────────────────────────────────────

def test_retry_after_within_window():
    """Пауза не короче половины ожидания и не длиннее полного ожидания."""
    for _ in range(200):
        value = retry_after_seconds(pool_timeout=30)
        assert 15 <= value <= 30, value


def test_retry_after_has_jitter():
    """Без разброса все клиенты вернулись бы одновременно и устроили вторую волну."""
    values = {retry_after_seconds(pool_timeout=30) for _ in range(200)}
    assert len(values) > 1, values


def test_retry_after_never_below_one():
    """Крошечное ожидание (тестовый стенд) не должно давать паузу 0 секунд."""
    for pool_timeout in (0.5, 1, 2):
        assert retry_after_seconds(pool_timeout=pool_timeout) >= 1


# ─── поведение обработчика на изолированном приложении ────────────────────────

def _build_test_app() -> FastAPI:
    """Мини-приложение с тем же обработчиком, без роутов LMS."""
    app = FastAPI()

    @app.get("/pool-timeout")
    async def _pool_timeout():
        raise SQLAlchemyPoolTimeout(POOL_TIMEOUT_MESSAGE)

    @app.get("/other-db-error")
    async def _other_db_error():
        raise OperationalError("SELECT 1", {}, Exception("соединение разорвано"))

    app.add_exception_handler(SQLAlchemyPoolTimeout, db_pool_timeout_handler)
    return app


@pytest.fixture()
def test_app() -> FastAPI:
    return _build_test_app()


@pytest.mark.asyncio
async def test_pool_timeout_returns_503(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        r = await c.get("/pool-timeout")
    assert r.status_code == 503, r.text
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1
    assert r.json()["error"] == "service_unavailable"


@pytest.mark.asyncio
async def test_retry_after_header_matches_body(test_app):
    """Клиент может взять паузу и из заголовка, и из тела — значения обязаны совпадать."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        r = await c.get("/pool-timeout")
    assert int(r.headers["Retry-After"]) == r.json()["retry_after"]


@pytest.mark.asyncio
async def test_other_db_error_still_500(test_app):
    """Прочие ошибки базы обработчик не перехватывает — поведение прежнее."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        r = await c.get("/other-db-error")
    assert r.status_code == 500, r.text
    assert "Retry-After" not in r.headers


@pytest.mark.asyncio
async def test_log_keeps_queuepool_marker(test_app, caplog):
    """След в логе терять нельзя: по подстроке QueuePool искали волну отказов."""
    with caplog.at_level(logging.ERROR, logger="api.error_handlers"):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as c:
            await c.get("/pool-timeout")

    records = [r for r in caplog.records if "QueuePool" in r.getMessage()]
    assert records, [r.getMessage() for r in caplog.records]
    message = records[0].getMessage()
    assert "/pool-timeout" in message
    assert "retry_after=" in message


# ─── боевое приложение ────────────────────────────────────────────────────────

def test_real_app_registers_handler():
    from app.api.main import app

    assert SQLAlchemyPoolTimeout in app.exception_handlers
    assert app.exception_handlers[SQLAlchemyPoolTimeout] is db_pool_timeout_handler


@pytest.mark.asyncio
async def test_audit_actor_reraises_pool_timeout():
    """Отказ пула должен пролетать наружу, а не гаситься как сбой метки аудита."""
    from app.db.audit_context import set_audit_actor

    class _DeadSession:
        async def execute(self, *args, **kwargs):
            raise SQLAlchemyPoolTimeout(POOL_TIMEOUT_MESSAGE)

    with pytest.raises(SQLAlchemyPoolTimeout):
        await set_audit_actor(_DeadSession(), "service:api_key")


@pytest.mark.asyncio
async def test_audit_actor_still_soft_fails(caplog):
    """Прочие сбои метки по-прежнему только пишутся в лог и не роняют запрос."""
    from app.db.audit_context import set_audit_actor

    class _BrokenSession:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("триггер аудита недоступен")

    with caplog.at_level(logging.WARNING, logger="app.db.audit_context"):
        await set_audit_actor(_BrokenSession(), "service:api_key")

    assert any("app.audit_actor" in r.getMessage() for r in caplog.records)


def test_real_app_exposes_retry_after():
    """Браузер не отдаёт странице заголовок, не объявленный в expose_headers."""
    from starlette.middleware.cors import CORSMiddleware

    from app.api.main import app

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert cors, "CORSMiddleware не подключён"
    assert "Retry-After" in cors[0].kwargs["expose_headers"]
