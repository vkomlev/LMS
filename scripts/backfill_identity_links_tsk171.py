"""
Backfill identity_link для orphan-пользователей (tsk-171).

Проблема: пользователи, созданные через `POST /api/v1/users/` до фикса tsk-171
(в частности преподаватели из ботов TG_LMS), имеют заполненный `users.email`
и/или `users.tg_id`, но НЕ имеют соответствующих записей в `identity_link`.
Для auth-флоу SPW (magic-link, VK) такой пользователь — «orphan»: вход по email
падает с 409 «email в нестандартном состоянии», а вообще без identity-записи
пользователь не может войти в SPW никаким способом.

Скрипт идемпотентно создаёт недостающие identity_link:
- kind='email', value=lower(users.email) — для всех users с непустым email
- kind='tg',    value=users.tg_id::text  — для всех users с непустым tg_id

Уже существующие связи не трогаются (ON CONFLICT (kind, value) DO NOTHING).
Повторный запуск безопасен. Требуется только DATABASE_URL (env или .env).

Запуск:
    python scripts/backfill_identity_links_tsk171.py            # применить
    python scripts/backfill_identity_links_tsk171.py --dry-run  # только показать
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# Кандидаты на бэкфилл: users с email/tg_id, у которых нет identity_link.
_COUNT_SQL = text("""
    SELECT
        (SELECT COUNT(*) FROM users u
         WHERE u.email IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM identity_link il
               WHERE il.kind = 'email' AND il.value = lower(u.email)
           )) AS missing_email,
        (SELECT COUNT(*) FROM users u
         WHERE u.tg_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM identity_link il
               WHERE il.kind = 'tg' AND il.value = u.tg_id::text
           )) AS missing_tg
""")

_INSERT_EMAIL_SQL = text("""
    INSERT INTO identity_link (user_id, kind, value, created_at)
    SELECT u.id, 'email', lower(u.email), now()
    FROM users u
    WHERE u.email IS NOT NULL
    ON CONFLICT (kind, value) DO NOTHING
    RETURNING id
""")

_INSERT_TG_SQL = text("""
    INSERT INTO identity_link (user_id, kind, value, created_at)
    SELECT u.id, 'tg', u.tg_id::text, now()
    FROM users u
    WHERE u.tg_id IS NOT NULL
    ON CONFLICT (kind, value) DO NOTHING
    RETURNING id
""")


async def run_backfill(db: AsyncSession, *, dry_run: bool) -> tuple[int, int]:
    """Создать недостающие identity_link. Возвращает (email_inserted, tg_inserted).

    В режиме dry_run возвращает КОЛИЧЕСТВО КАНДИДАТОВ (что было бы вставлено) и
    откатывает транзакцию.
    """
    counts = (await db.execute(_COUNT_SQL)).one()
    missing_email, missing_tg = int(counts.missing_email), int(counts.missing_tg)
    logger.info("Кандидаты: email=%s, tg=%s", missing_email, missing_tg)

    if dry_run:
        logger.info("dry-run: изменения не применяются")
        await db.rollback()
        return missing_email, missing_tg

    email_inserted = len((await db.execute(_INSERT_EMAIL_SQL)).all())
    tg_inserted = len((await db.execute(_INSERT_TG_SQL)).all())
    await db.commit()
    logger.info("Вставлено identity_link: email=%s, tg=%s", email_inserted, tg_inserted)
    return email_inserted, tg_inserted


async def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill identity_link (tsk-171)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать кандидатов, не применять")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("Требуется переменная окружения DATABASE_URL")
        return 1

    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with async_session() as session:
            await run_backfill(session, dry_run=args.dry_run)
        return 0
    except Exception as e:
        logger.exception("Backfill failed: %s", e)
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
