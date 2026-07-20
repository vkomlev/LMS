"""Общие фикстуры для тестов Phase Y-1+."""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# Windows: asyncpg несовместим с ProactorEventLoop по умолчанию
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import Settings
from app.api.main import app
from app.db.session import get_async_db

_settings = Settings()
_logger = logging.getLogger(__name__)

# id «реальных» пользователей dev-БД, которых тестовый sweep не трогает
# (см. scripts/cleanup_test_artifacts.py)
_REAL_USER_IDS: tuple[int, ...] = (2, 3, 142)


async def _snapshot_ts() -> datetime:
    """Зафиксировать серверный timestamp PG для последующего sweep'а."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT now()"))
            return row.scalar()
    finally:
        await engine.dispose()


async def _sweep_test_artifacts(snapshot_ts: datetime) -> dict[str, int]:
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    real_csv = ",".join(str(i) for i in _REAL_USER_IDS)
    counts = {"users": 0, "audit_event": 0, "magic_link": 0, "guest_session": 0}
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"UPDATE notifications SET modified_by = NULL "
                    f"WHERE modified_by IS NOT NULL "
                    f"AND modified_by NOT IN ({real_csv}) "
                    f"AND modified_by IN ("
                    f"  SELECT id FROM users "
                    f"  WHERE created_at >= :ts "
                    f"  AND (email IS NULL OR email ILIKE '%@example.%')"
                    f")"
                ),
                {"ts": snapshot_ts},
            )
            await conn.execute(
                text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_modify")
            )
            counts["audit_event"] = (
                await conn.execute(
                    text(
                        f"DELETE FROM audit_event "
                        f"WHERE ts >= :ts "
                        f"AND (user_id IS NULL OR user_id NOT IN ({real_csv}))"
                    ),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            counts["magic_link"] = (
                await conn.execute(
                    text(
                        "DELETE FROM magic_link "
                        "WHERE created_at >= :ts "
                        "AND (email IS NULL OR email ILIKE '%@example.%')"
                    ),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            counts["guest_session"] = (
                await conn.execute(
                    text("DELETE FROM guest_session WHERE created_at >= :ts"),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            counts["users"] = (
                await conn.execute(
                    text(
                        f"DELETE FROM users "
                        f"WHERE created_at >= :ts "
                        f"AND id NOT IN ({real_csv}) "
                        f"AND (email IS NULL OR email ILIKE '%@example.%')"
                    ),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            await conn.execute(
                text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_modify")
            )
        return counts
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_artifacts():
    """Session-scoped sweep тестовых артефактов в dev-БД (Learn.public).

    Снимает мусор, который тесты успели закоммитить за прогон pytest:
    users с email `@example.*` или без email, плюс непривязанные к
    «реальным» (id 2,3,142) audit_event / magic_link / guest_session
    созданные за время прогона. CASCADE от `users` подбирает остальное
    (user_roles, identity_link, user_session, attempts, task_results,
    learning_events, notifications, teacher_courses, user_courses,
    student_*, access_requests, social_posts, user_achievements,
    help_requests + replies, messages.recipient).

    Технические нюансы:
    - `audit_event` имеет триггер `audit_event_no_modify` (append-only)
      и FK с `SET NULL` → DELETE на users падает. Триггер временно
      отключается в той же транзакции и возвращается обратно.
    - `notifications.modified_by` FK с `NO ACTION` → обнуляется заранее.
    - Два слоя защиты от случайного удаления real users:
        1. фильтр по email `ILIKE '%@example.%'` (real users имеют
           реальные домены mail.ru/list.ru/gmail.com)
        2. allow-list `_REAL_USER_IDS` (2,3,142)
    - Фикстура синхронная: внутри `asyncio.run` создаёт изолированный
      event loop, поэтому не конфликтует с function-scoped event loop
      теста (pytest-asyncio 1.x).
    - Snapshot — `now()` PG-сервера на старте сессии; sweep удаляет
      строки `created_at >= snapshot`. Это позволяет работать с
      таблицами, где `id` UUID (например, `guest_session`).
    """
    snapshot_ts = asyncio.run(_snapshot_ts())
    _logger.info("test-artifacts sweep snapshot_ts: %s", snapshot_ts)
    yield
    try:
        counts = asyncio.run(_sweep_test_artifacts(snapshot_ts))
        _logger.info(
            "test-artifacts sweep done: users=%d audit_event=%d "
            "magic_link=%d guest_session=%d",
            counts["users"],
            counts["audit_event"],
            counts["magic_link"],
            counts["guest_session"],
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception(
            "test-artifacts sweep failed (manual cleanup may be needed): %s",
            exc,
        )


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """NullPool-движок, привязанный к event loop текущего теста.

    Нужен отдельно от `db` тем тестам, которые дёргают сервисный код,
    открывающий собственную сессию (например, `escalation_cron_tick`):
    им передаётся фабрика поверх этого движка вместо глобального
    `app.db.session.async_session_factory` с QueuePool — иначе
    соединение из пула, оставшееся от предыдущего теста, переиспользуется
    в новом loop и asyncpg падает с «attached to a different loop».
    """
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


# Модули, которые сами открывают движок/соединение к БД (`create_async_engine`)
# в своих фикстурах. Они несовместимы с общей откатываемой транзакцией:
# данные теста лежат в НЕЗАКОММИЧЕННОЙ транзакции одного соединения, а уборка
# идёт ДРУГИМ соединением — её `DELETE` встаёт в блокировку и ждёт транзакцию,
# которая закончится только после теста. Тест ждёт DELETE, DELETE ждёт тест:
# взаимный клинч, прогон висит без ошибки (tsk-333).
#
# Такие модули работают по-старому: реальные коммиты + уборка за собой.
# Список сверяется тестом `test_tx_isolation_optout.py` — при добавлении
# `create_async_engine` в новый тестовый модуль он подскажет, что делать.
SELF_MANAGED_CONNECTION_MODULES: frozenset[str] = frozenset(
    {
        "test_attempt_cancel_stage35.py",
        "test_attempts_enrollment_hole_tsk272.py",
        "test_attempts_integration_stage4.py",
        "test_attempts_limit_enforced_tsk269.py",
        "test_attempts_limit_race_tsk273.py",
        "test_attempts_null_solution_rules_tsk325.py",
        "test_attempts_root_course_tsk264.py",
        "test_hint_events_stage36.py",
        "test_last_attempt_stage6.py",
        "test_learning_engine_service.py",
        "test_materials_bulk_upsert.py",
        "test_migrations.py",
        "test_repos_smoke.py",
        "test_tasks_order_position_api.py",
        "test_tasks_reorder_api.py",
        "test_teacher_courses_triggers_smoke.py",
        "test_teacher_help_requests_overdue_tsk312.py",
        "test_teacher_help_requests_stage38.py",
        "test_teacher_help_requests_stage381.py",
        "test_teacher_next_modes_stage39.py",
        "test_triggers_smoke.py",
        "test_tsk088_task_content_hints_preserved.py",
    }
)


# Модули вне изоляции по ДРУГИМ причинам (не свой движок). Список ручной,
# сторожем не проверяется — причина у каждого своя, автоматически её не вывести.
OTHER_OPTOUT_MODULES: dict[str, str] = {
    # Синхронный тест-скрипт: внутри поднимает свой event loop через
    # `asyncio.run(...)`, поэтому соединение общей транзакции (созданное в
    # loop'е pytest-asyncio) в нём непригодно — asyncpg падает с
    # «attached to a different loop».
    "test_hints_stage5.py": "свой event loop через asyncio.run",
}


def pytest_collection_modifyitems(items) -> None:
    """Проставить `no_tx_isolation` модулям, несовместимым с общей транзакцией."""
    optout = SELF_MANAGED_CONNECTION_MODULES | set(OTHER_OPTOUT_MODULES)
    for item in items:
        if Path(str(item.fspath)).name in optout:
            item.add_marker(pytest.mark.no_tx_isolation)


@pytest_asyncio.fixture(scope="function")
async def db_conn(db_engine, request):
    """Соединение теста с внешней транзакцией, которая всегда откатывается (tsk-333).

    Тест и ASGI-приложение работают поверх ОДНОГО соединения: сессии
    открываются с `join_transaction_mode="create_savepoint"`, поэтому их
    `commit()` закрывает SAVEPOINT, а не внешнюю транзакцию. В конце теста
    внешняя транзакция откатывается — в БД не остаётся ничего, даже если
    тест упал до своего `finally: _cleanup(...)`.

    Тесты с маркером `no_tx_isolation` получают `None` и работают по-старому
    (реальные коммиты + ручная чистка) — это нужно там, где проверяется
    поведение НЕСКОЛЬКИХ параллельных соединений: гонки, блокировки,
    видимость между транзакциями. Одно соединение такую проверку обнуляет.
    """
    if request.node.get_closest_marker("no_tx_isolation"):
        yield None
        return

    conn = await db_engine.connect()
    trans = await conn.begin()
    try:
        yield conn
    finally:
        await trans.rollback()
        await conn.close()


@pytest_asyncio.fixture(scope="function")
async def db(db_engine, db_conn):
    """Асинхронная сессия к БД; по умолчанию — внутри откатываемой транзакции.

    NullPool гарантирует, что каждый тест получает свежее соединение,
    не связанное с event loop предыдущего теста.
    """
    if db_conn is None:
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            yield session
            await session.rollback()
        return

    async with AsyncSession(
        db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    ) as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def db_session_factory(db_engine, db_conn):
    """Фабрика сессий для сервисного кода, который открывает сессию сам.

    Например `escalation_cron_tick(session_factory=...)`. Фабрика привязана к
    тому же соединению, что и `db`, — иначе сервис ходит другим соединением и
    НЕ ВИДИТ данные теста, лежащие в незакоммиченной транзакции.

    Вне изоляции (`no_tx_isolation`) — фабрика поверх NullPool-движка текущего
    event loop'а, как было сделано в tsk-330.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    if db_conn is None:
        yield async_sessionmaker(bind=db_engine, expire_on_commit=False)
        return

    yield async_sessionmaker(
        bind=db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _override_app_db(db_conn):
    """Посадить ASGI-запросы на то же соединение, что и фикстура `db`.

    Без этого данные теста не видны приложению (разные соединения), и тест
    вынужден коммитить в общую БД — источник кросс-тестового загрязнения.
    Для `no_tx_isolation` поведение прежнее: свой NullPool-движок на запрос.
    """

    async def override_shared_conn() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSession(
            db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session

    async def override_own_engine() -> AsyncGenerator[AsyncSession, None]:
        engine = create_async_engine(_settings.database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session
        finally:
            await engine.dispose()

    override_get_db = override_own_engine if db_conn is None else override_shared_conn

    previous_override = app.dependency_overrides.get(get_async_db)
    app.dependency_overrides[get_async_db] = override_get_db
    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_async_db, None)
        else:
            app.dependency_overrides[get_async_db] = previous_override


@pytest_asyncio.fixture(scope="function")
async def client():
    """HTTP-клиент для ASGI-тестов.

    Переопределяет get_async_db чтобы использовать NullPool —
    иначе глобальный QueuePool хранит соединения из предыдущих event loop'ов.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
