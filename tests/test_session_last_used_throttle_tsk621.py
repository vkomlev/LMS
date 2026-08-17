"""tsk-621: отметка последней активности сессии пишется не на каждом запросе.

17.08.2026 прод волнами отдавал 500: `validate_session` обновляла
`user_session.last_used_at` при КАЖДОМ обращении, а `UPDATE` держит блокировку
строки до конца транзакции запроса. Кабинет открывает дерево курса десятками
параллельных запросов под одной сессией — они выстраивались в очередь друг за
другом, очередь перерастала таймаут пула подключений, и 500 получали уже все
пользователи. В снимке прода 14 подключений из 15 ждали именно эту строку.

Проверяется:
- повторная проверка сессии внутри минуты не трогает строку (нет записи);
- по истечении интервала отметка всё же обновляется — экран «мои устройства»
  продолжает показывать актуальное время;
- значение без часового пояса считается устаревшим и не роняет сравнение.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth import session_service

pytestmark = pytest.mark.asyncio


async def _get_existing_user_id(db) -> int:
    uid = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if uid is None:
        pytest.skip("Нет пользователей в БД")
    return uid


@pytest_asyncio.fixture()
async def user_id(db) -> int:
    return await _get_existing_user_id(db)


async def _last_used(db, session_id) -> datetime:
    return (
        await db.execute(
            text("SELECT last_used_at FROM user_session WHERE id = :sid"),
            {"sid": str(session_id)},
        )
    ).scalar_one()


async def test_povtornaya_proverka_ne_pishet_otmetku(db, user_id: int) -> None:
    """Вторая проверка сессии подряд не порождает запись в строку сессии."""
    access, _refresh, session = await session_service.create_session(db, user_id=user_id)
    await db.commit()

    await session_service.validate_session(db, access)
    await db.commit()
    after_first = await _last_used(db, session.id)

    await session_service.validate_session(db, access)
    await db.commit()
    after_second = await _last_used(db, session.id)

    assert after_second == after_first, (
        "Отметка обновилась повторно внутри минуты — блокировка строки снова "
        "сериализует параллельные запросы одного пользователя"
    )


async def test_posle_intervala_otmetka_obnovlyaetsya(db, user_id: int) -> None:
    """Когда отметка устарела, она обновляется — данные экрана устройств живые."""
    access, _refresh, session = await session_service.create_session(db, user_id=user_id)
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db.execute(
        text("UPDATE user_session SET last_used_at = :ts WHERE id = :sid"),
        {"ts": stale, "sid": str(session.id)},
    )
    await db.commit()
    db.expire_all()

    await session_service.validate_session(db, access)
    await db.commit()

    assert await _last_used(db, session.id) > stale


async def test_znachenie_bez_chasovogo_poyasa_schitaetsya_ustarevshim() -> None:
    """Naive-время не роняет сравнение и трактуется как «пора обновить»."""
    now = datetime.now(timezone.utc)

    assert session_service._last_used_is_stale(None, now=now) is True
    assert session_service._last_used_is_stale(datetime.now(), now=now) is True
    assert session_service._last_used_is_stale(now, now=now) is False
