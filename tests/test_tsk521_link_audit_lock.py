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
import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services import link_audit_service


@pytest.mark.asyncio
async def test_second_worker_backs_off(monkeypatch):
    """Пока один worker держит блокировку, второй отступает и работу не дублирует.

    Блокировка живёт, пока идёт проход, поэтому держатель ждёт, пока второй тик
    не вернётся: исход не зависит ни от объёма базы, ни от скорости соединений.
    """
    # Своё в порядке, чужие домены вне охвата — тик не должен ходить в сеть.
    monkeypatch.setenv("LINK_AUDIT_OWN_HOSTS", "")
    other_done = asyncio.Event()

    async def _exists(_name: str) -> bool:
        # Держатель блокировки стоит здесь, пока второй тик не отступит. Если
        # блокировку возьмут оба по очереди, ожидание кончится таймаутом.
        await asyncio.wait_for(other_done.wait(), timeout=10)
        return True

    monkeypatch.setattr(
        link_audit_service.material_files_storage, "material_file_exists", _exists
    )
    monkeypatch.setattr(link_audit_service, "_cas_media_exists", _exists)

    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, int] = {}

    async def _tick() -> dict:
        summary = await link_audit_service.link_audit_tick(factory)
        other_done.set()
        return summary

    try:
        # Хотя бы одна цель, чтобы держатель блокировки дошёл до проверки.
        async with factory() as db:
            ids["course"] = (
                await db.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES (:t, 'self_guided') RETURNING id"
                    ),
                    {"t": f"tsk521-lock-{uuid.uuid4().hex[:12]}"},
                )
            ).scalar_one()
            ids["material"] = (
                await db.execute(
                    text(
                        "INSERT INTO materials (title, type, content, course_id, is_active) "
                        "VALUES ('tsk521-lock', 'image', CAST(:c AS jsonb), :cid, true) "
                        "RETURNING id"
                    ),
                    {
                        "c": json.dumps({"sources": [{
                            "url": f"/api/v1/materials/files/tsk521-lock-{uuid.uuid4().hex}.png",
                            "type": "file",
                        }]}),
                        "cid": ids["course"],
                    },
                )
            ).scalar_one()
            await db.commit()

        first, second = await asyncio.gather(_tick(), _tick())
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
            if "material" in ids:
                await db.execute(
                    text("DELETE FROM materials WHERE id = :m"), {"m": ids["material"]}
                )
            if "course" in ids:
                await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
            await db.execute(
                text("DELETE FROM notifications WHERE kind = :k"),
                {"k": link_audit_service.NOTIFICATION_KIND},
            )
            await db.commit()
        await engine.dispose()
