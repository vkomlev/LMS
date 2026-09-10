"""Одноразовый cleanup тестовых email с .test TLD из БД (Y-1.5 dev).

Триггер: ранние Y-1.5 тесты использовали @example.test, который не валиден
для Pydantic EmailStr (RFC 6761 special-use TLD). Очищаем чтобы /users CRUD
не падал на response_model validation.

Отбор идёт по признаку — домену `@example.test`. Признак узкий, но правка
всё равно идёт двумя шагами (tsk-885): без `--apply` скрипт только показывает,
что нашёл. На боевом подключении отказывает.

Использование:
    python scripts/cleanup_test_emails.py            # предпросмотр
    python scripts/cleanup_test_emails.py --apply    # запись
"""
import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.core.db_targets import assert_not_prod  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402

_PATTERN = "%@example.test"


async def main(apply: bool) -> None:
    assert_not_prod(what="очистка тестовых адресов")
    async with async_session_factory() as db:
        users = (
            await db.execute(
                text("SELECT id, email FROM users WHERE email LIKE :p"),
                {"p": _PATTERN},
            )
        ).all()
        links = (
            await db.execute(
                text("SELECT id, user_id, value FROM identity_link WHERE value LIKE :p"),
                {"p": _PATTERN},
            )
        ).all()
        print(f"Найдено users с тестовым адресом: {len(users)}")
        for row in users[:20]:
            print(f"  #{row[0]}  {row[1]}")
        print(f"Найдено привязок: {len(links)}")
        for row in links[:20]:
            print(f"  #{row[0]}  user={row[1]}  {row[2]}")

        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            return

        # users → email=NULL (нельзя DELETE из-за audit_event trigger;
        # NULL валиден для partial unique index)
        r1 = await db.execute(
            text("UPDATE users SET email = NULL WHERE email LIKE :p"), {"p": _PATTERN}
        )
        r2 = await db.execute(
            text("DELETE FROM identity_link WHERE value LIKE :p"), {"p": _PATTERN}
        )
        await db.commit()
        print(f"users.email scrubbed: {r1.rowcount}")
        print(f"identity_link deleted: {r2.rowcount}")

        left = (
            await db.execute(
                text("SELECT count(*) FROM identity_link WHERE value LIKE :p"),
                {"p": _PATTERN},
            )
        ).scalar()
        print(f"Сверка: осталось привязок {left}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    asyncio.run(main(parser.parse_args().apply))
