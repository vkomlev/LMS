"""Одноразовый cleanup тестовых email с .test TLD из БД (Y-1.5 dev).

Триггер: ранние Y-1.5 тесты использовали @example.test, который не валиден
для Pydantic EmailStr (RFC 6761 special-use TLD). Очищаем чтобы /users CRUD
не падал на response_model validation.

Использование: python scripts/cleanup_test_emails.py
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402


async def main() -> None:
    async with async_session_factory() as db:
        # users → email=NULL (нельзя DELETE из-за audit_event trigger;
        # NULL валиден для partial unique index)
        r1 = await db.execute(
            text("UPDATE users SET email = NULL WHERE email LIKE '%@example.test'")
        )
        r2 = await db.execute(
            text("DELETE FROM identity_link WHERE value LIKE '%@example.test'")
        )
        await db.commit()
        print(f"users.email scrubbed: {r1.rowcount}")
        print(f"identity_link deleted: {r2.rowcount}")


if __name__ == "__main__":
    asyncio.run(main())
