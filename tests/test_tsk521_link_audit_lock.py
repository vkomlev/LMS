"""tsk-521: защита проверки ссылок от двойного запуска (advisory-lock).

Вынесено из `test_tsk521_link_audit.py` в отдельный модуль намеренно: здесь
нужны два НЕЗАВИСИМЫХ соединения к базе, то есть собственный engine. Общая
тестовая фикстура держит одно соединение внутри savepoint — два параллельных
тика подрались бы на уровне SQLAlchemy, не дойдя до advisory-lock, и проверяли
бы обвязку теста, а не механизм. Модуль объявлен в
`SELF_MANAGED_CONNECTION_MODULES` (tests/conftest.py) и убирает за собой сам.

Зачем механизм: на проде приложение крутится несколькими worker'ами, тик
заведён в каждом. Без блокировки одну и ту же проверку разом делали бы все,
а методист получил бы столько же одинаковых уведомлений.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services import link_audit_service


@pytest.mark.asyncio
async def test_second_worker_backs_off(monkeypatch):
    """Пока один worker держит блокировку, второй отступает и работу не дублирует."""
    # Своё в порядке, чужие домены вне охвата — тик не должен ходить в сеть.
    monkeypatch.setenv("LINK_AUDIT_OWN_HOSTS", "")

    async def _exists(_name: str) -> bool:
        return True

    monkeypatch.setattr(
        link_audit_service.material_files_storage, "material_file_exists", _exists
    )
    monkeypatch.setattr(link_audit_service, "_cas_media_exists", _exists)

    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first, second = await asyncio.gather(
            link_audit_service.link_audit_tick(factory),
            link_audit_service.link_audit_tick(factory),
        )
        locked = [s["locked"] for s in (first, second)]
        assert locked.count(True) == 1, (first, second)
        assert locked.count(False) == 1, (first, second)

        # Отступивший worker не делает работу: ни проверок, ни уведомлений.
        backed_off = first if first["locked"] is False else second
        assert backed_off["checked"] == 0, backed_off
        assert backed_off["notified"] == 0, backed_off
    finally:
        # Модуль без транзакционной изоляции — убираем за собой руками.
        async with factory() as db:
            await db.execute(
                text("DELETE FROM notifications WHERE kind = :k"),
                {"k": link_audit_service.NOTIFICATION_KIND},
            )
            await db.commit()
        await engine.dispose()
