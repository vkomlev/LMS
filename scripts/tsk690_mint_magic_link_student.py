# -*- coding: utf-8 -*-
"""tsk-690: выпустить РЕАЛЬНЫЙ magic-link для живой проверки под учеником.

Зачем: после уборки материалов-«Вопросов» и перевода Turtle в рекомендуемые надо
увидеть глазами, что у ученика, который ушёл дальше, долга больше нет. Экран
ученика отличается от экрана преподавателя, а живая сессия профиля резолвится в
аккаунт 2 (преподаватель) — нужен вход именно под учеником.

Путь штатный: `magic_link_service.create_magic_link` — та же функция, которую
зовёт ручка «войти по email» перед отправкой письма; пропускается только шаг
отправки. Переход по `/auth/magic-link/consume?token=...` сервер обрабатывает
сам: гасит токен, создаёт `user_session`, ставит httpOnly cookie. Прямых INSERT
в auth-таблицы и подмены cookie нет.

Ни email, ни токен в отчёты не попадают — печатаются только в консоль запуска.

Запуск: `DBCHECK_OK=1 python scripts/tsk690_mint_magic_link_student.py --user-id 4507 --apply`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_prod_dsn_asyncpg_style() -> str:
    """DSN прод-роли из `.mcp.json` в формате SQLAlchemy (секрет не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(user_id: int, apply: bool) -> int:
    import os

    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.services.auth.magic_link_service import create_magic_link

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        row = (
            await db.execute(
                text(
                    "SELECT u.full_name, il.value AS email FROM users u "
                    "JOIN identity_link il ON il.user_id = u.id AND il.kind = 'email' "
                    "WHERE u.id = :uid"
                ),
                {"uid": user_id},
            )
        ).first()
        if row is None:
            print(f"У пользователя {user_id} нет привязки email — вход по ссылке невозможен.")
            await engine.dispose()
            return 2

        print(f"Аккаунт {user_id}: {row.full_name}")
        token = await create_magic_link(db, row.email)
        if apply:
            await db.commit()
            print(f"CONSUME_URL=https://learn.victor-komlev.ru/auth/magic-link/consume?token={token}")
        else:
            await db.rollback()
            print("ROLLBACK — dry-run, токен не сохранён.")
        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-690: magic-link для живой проверки")
    ap.add_argument("--user-id", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(user_id=args.user_id, apply=args.apply)))
