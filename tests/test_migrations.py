"""
Тест upgrade/downgrade roundtrip для миграций M1–M9 Phase Y-1 / Y-1.5 / Y-3 / Y-4 / Y-4.2.

Тесты откатывают схему до M3 и потому РАЗРУШИТЕЛЬНЫ: они выполняются на отдельной
одноразовой БД (`<основная>_migrations_test`), которая создаётся с нуля перед прогоном.
Рабочую БД из DATABASE_URL они не трогают (tsk-169).
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

project_root = Path(__file__).resolve().parents[1]

TEST_DB_SUFFIX = "_migrations_test"


def _alembic_head() -> str:
    """Актуальный head из дерева миграций Alembic, а не из прибитой константы."""
    cfg = Config(str(project_root / "alembic.ini"))
    script_location = cfg.get_main_option("script_location")
    cfg.set_main_option("script_location", str(project_root / script_location))
    return ScriptDirectory.from_config(cfg).get_current_head()


HEAD_REV = _alembic_head()


def _swap_database(dsn: str, db_name: str) -> str:
    """Тот же DSN, но с другим именем базы."""
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


@pytest.fixture(scope="module", autouse=True)
def migrations_test_db() -> str:
    """Создаёт чистую одноразовую БД и направляет в неё alembic на весь модуль.

    Тесты этого файла откатывают схему до M3 — на рабочей БД это снесло бы данные.
    """
    import asyncio

    import asyncpg
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", encoding="utf-8-sig")
    source_dsn = os.environ["DATABASE_URL"]
    source_db = urlsplit(source_dsn).path.lstrip("/")
    test_db = f"{source_db}{TEST_DB_SUFFIX}"

    # asyncpg не понимает префикс диалекта SQLAlchemy
    admin_dsn = _swap_database(source_dsn, "postgres").replace("postgresql+asyncpg://", "postgresql://")

    async def _recreate() -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{test_db}"')
        finally:
            await conn.close()

    asyncio.run(_recreate())

    test_dsn = _swap_database(source_dsn, test_db)
    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = test_dsn

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head на тестовой БД не прошёл:\n{up.stderr}"

    # roles заполняет seed-скрипт, а не миграция: без него m10_role_backfill
    # подставит NULL в user_roles.role_id (tsk-169).
    seed = subprocess.run(
        [sys.executable, "scripts/seed_roles.py"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert seed.returncode == 0, f"seed_roles на тестовой БД не прошёл:\n{seed.stderr}"

    yield test_dsn

    os.environ["DATABASE_URL"] = previous
M9_REV = "m9_zombie_sanitize"
M8_REV = "20260430_010000_m8_inbox"
M7_REV = "20260429_010000_m7_streak_idx"
M6_REV = "20260428_060000_m6_tg_sync"


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "alembic"] + list(args)
    result = subprocess.run(
        cmd,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result


def test_alembic_head_is_current():
    """Цепочка миграций накатывается с нуля ровно до актуального head.

    Head берётся из дерева миграций, а не из константы: тест не устаревает
    при добавлении новой миграции (tsk-169).
    """
    result = _run_alembic("current")
    assert result.returncode == 0, f"alembic current failed:\n{result.stderr}"
    assert HEAD_REV in result.stdout, (
        f"Expected {HEAD_REV} as head, got:\n{result.stdout}"
    )


def test_alembic_downgrade_m10_then_upgrade():
    """M10 (Phase Y-4 pre-S5) roundtrip: downgrade no-op + upgrade обратно."""
    down = _run_alembic("downgrade", M9_REV)
    assert down.returncode == 0, f"downgrade M10 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M10 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout


def test_alembic_downgrade_m9_then_upgrade():
    """M9 (Phase Y-4.2) roundtrip: downgrade no-op + upgrade обратно."""
    down = _run_alembic("downgrade", M8_REV)
    assert down.returncode == 0, f"downgrade M9 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M9 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout


def test_alembic_downgrade_m8_then_upgrade():
    """M8 (Phase Y-4) roundtrip: откатить notifications inbox и снова накатить."""
    down = _run_alembic("downgrade", M7_REV)
    assert down.returncode == 0, f"downgrade M8 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M8 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout


def test_alembic_downgrade_m7_then_upgrade():
    """M7 (Phase Y-3) roundtrip: откатить и снова накатить (индекс drop+create)."""
    down = _run_alembic("downgrade", M6_REV)
    assert down.returncode == 0, f"downgrade M7 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M7 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout


def test_alembic_downgrade_m6_then_upgrade():
    """Откатить M6 и снова накатить."""
    down = _run_alembic("downgrade", "20260428_050000_m5_guest")
    assert down.returncode == 0, f"downgrade M6 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M6 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout


def test_alembic_downgrade_m5_then_upgrade():
    """Откатить M5 и снова накатить — должно пройти без ошибок."""
    down = _run_alembic("downgrade", "20260428_040000_m4_audit")
    assert down.returncode == 0, f"downgrade M5 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M5 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert HEAD_REV in current.stdout


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
            _run_alembic("upgrade", "head")  # restore HEAD to current M10

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
