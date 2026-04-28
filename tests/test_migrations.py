"""
Тест upgrade/downgrade roundtrip для миграций M1–M6 Phase Y-1 / Y-1.5.

Запускается против реальной БД (alembic использует DATABASE_URL из .env).
Требует: alembic head = 20260428_060000_m6_tg_sync до запуска.
"""
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[1]
HEAD_REV = "20260428_060000_m6_tg_sync"


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "alembic"] + list(args)
    result = subprocess.run(
        cmd, cwd=str(project_root), capture_output=True, text=True, encoding="utf-8"
    )
    return result


def test_alembic_head_is_m6():
    """Текущий head должен быть M6 (Phase Y-1.5 backfill миграция применена)."""
    result = _run_alembic("current")
    assert result.returncode == 0, f"alembic current failed:\n{result.stderr}"
    assert HEAD_REV in result.stdout or "m6_tg_sync" in result.stdout, (
        f"Expected {HEAD_REV} as head, got:\n{result.stdout}"
    )


def test_alembic_downgrade_m6_then_upgrade():
    """Откатить M6 и снова накатить."""
    down = _run_alembic("downgrade", "20260428_050000_m5_guest")
    assert down.returncode == 0, f"downgrade M6 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M6 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout or "m6_tg_sync" in current.stdout


def test_alembic_downgrade_m5_then_upgrade():
    """Откатить M5 и снова накатить — должно пройти без ошибок."""
    down = _run_alembic("downgrade", "20260428_040000_m4_audit")
    assert down.returncode == 0, f"downgrade M5 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M5 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout or "m6_tg_sync" in current.stdout


def test_alembic_downgrade_m4_then_upgrade():
    """Откатить до M3, затем накатить обратно."""
    down = _run_alembic("downgrade", "20260428_030000_m3_sessions")
    assert down.returncode == 0, f"downgrade M4 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M4 downgrade failed:\n{up.stderr}"


def test_M6_backfill_fills_users_tg_id_from_identity_link():
    """Pre-existing identity_link kind='tg' для users.tg_id=NULL → upgrade fills users.tg_id.

    Сценарий: на baseline (M5) вставляем identity_link с tg, в users.tg_id=NULL.
    После downgrade до M5 + INSERT + upgrade до M6 — users.tg_id заполнен.
    """
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", encoding="utf-8-sig")
    import asyncio
    import os
    import random
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    async def _scenario():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        try:
            down = _run_alembic("downgrade", "20260428_050000_m5_guest")
            assert down.returncode == 0, f"downgrade to M5 failed:\n{down.stderr}"
            tg_id = random.SystemRandom().randint(10**12, 10**14)
            async with AsyncSession(engine) as db:
                user_id = (await db.execute(text(
                    "INSERT INTO users (email, password_hash, full_name, tg_id) "
                    "VALUES (NULL, NULL, 'M6-test', NULL) RETURNING id"
                ))).scalar_one()
                await db.execute(text(
                    "INSERT INTO identity_link (user_id, kind, value) "
                    "VALUES (:u, 'tg', :v)"
                ), {"u": user_id, "v": str(tg_id)})
                await db.commit()

            up = _run_alembic("upgrade", "head")
            assert up.returncode == 0, f"upgrade to M6 failed:\n{up.stderr}"

            async with AsyncSession(engine) as db:
                fetched = (await db.execute(
                    text("SELECT tg_id FROM users WHERE id = :id"), {"id": user_id}
                )).scalar_one()
                assert fetched == tg_id, f"M6 не заполнил users.tg_id: ожидал {tg_id}, получил {fetched}"
                await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
                await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await db.commit()
        finally:
            await engine.dispose()
            _run_alembic("upgrade", "head")

    asyncio.run(_scenario())


def test_M6_backfill_creates_identity_link_for_legacy_users_tg_id():
    """Pre-existing users.tg_id без identity_link → upgrade создаёт identity_link."""
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", encoding="utf-8-sig")
    import asyncio
    import os
    import random
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    async def _scenario():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        try:
            down = _run_alembic("downgrade", "20260428_050000_m5_guest")
            assert down.returncode == 0, f"downgrade to M5 failed:\n{down.stderr}"
            tg_id = random.SystemRandom().randint(10**12, 10**14)
            async with AsyncSession(engine) as db:
                user_id = (await db.execute(text(
                    "INSERT INTO users (email, password_hash, full_name, tg_id) "
                    "VALUES (NULL, NULL, 'M6-legacy', :tg) RETURNING id"
                ), {"tg": tg_id})).scalar_one()
                await db.commit()

            up = _run_alembic("upgrade", "head")
            assert up.returncode == 0, f"upgrade to M6 failed:\n{up.stderr}"

            async with AsyncSession(engine) as db:
                count = (await db.execute(text(
                    "SELECT COUNT(*) FROM identity_link WHERE kind='tg' AND value=:v AND user_id=:u"
                ), {"v": str(tg_id), "u": user_id})).scalar()
                assert count == 1, f"M6 не создал identity_link для legacy users.tg_id"
                await db.execute(text(
                    "DELETE FROM identity_link WHERE user_id=:u"
                ), {"u": user_id})
                await db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
                await db.commit()
        finally:
            await engine.dispose()
            _run_alembic("upgrade", "head")

    asyncio.run(_scenario())
