# -*- coding: utf-8 -*-
"""tsk-741: вернуть профиль аккаунта 2 после живой проверки вопроса про класс.

Живой прогон на проде прошёл по всему пути: полоса в кабинете → клик «11 класс»
→ `PATCH /me` 200 → в базе `category='school_student'`, `school_grade=11`. Это
данные теста, а не человека: аккаунт 2 — Виктор Комлев, не школьник. Оставить их
нельзя — карточка у методиста и будущий расчёт объёма ДЗ (фаза 2) читают ровно
эти поля.

Возвращаем ИСХОДНОЕ состояние, снятое до прогона: `category IS NULL`,
`school_grade IS NULL`, `school_grade_declined_at IS NULL`. После этого вопрос
в кабинете снова открыт — как и был до проверки; ответит на него сам человек.

Протокол `/db-check`: печать текущего состояния → выполнение в транзакции →
сверка после. Правится ровно одна строка по первичному ключу.

Запуск:
    python scripts/tsk741_revert_test_answer_user2.py              # сухой прогон
    DBCHECK_OK=1 python scripts/tsk741_revert_test_answer_user2.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Аккаунт, на котором шла живая проверка (Виктор Комлев, персонал + student).
TARGET_USER_ID = 2


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


_READ_SQL = """
    SELECT id, full_name, category, school_grade, school_grade_declined_at
      FROM users
     WHERE id = :uid
"""

_WRITE_SQL = """
    UPDATE users
       SET category = NULL,
           school_grade = NULL,
           school_grade_declined_at = NULL
     WHERE id = :uid
"""


async def main(apply: bool) -> int:
    import os

    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        before = (
            await db.execute(text(_READ_SQL), {"uid": TARGET_USER_ID})
        ).one_or_none()
        if before is None:
            print(f"Пользователя {TARGET_USER_ID} нет — нечего возвращать.")
            await engine.dispose()
            return 2

        print("ДО:", dict(before._mapping))

        if not apply:
            print("Сухой прогон: запись не выполнялась. Повтор с --apply.")
            await engine.dispose()
            return 0

        result = await db.execute(text(_WRITE_SQL), {"uid": TARGET_USER_ID})
        if result.rowcount != 1:
            await db.rollback()
            print(f"Ожидалась 1 строка, затронуто {result.rowcount} — откат.")
            await engine.dispose()
            return 3
        await db.commit()

        after = (await db.execute(text(_READ_SQL), {"uid": TARGET_USER_ID})).one()
        print("ПОСЛЕ:", dict(after._mapping))
        ok = (
            after.category is None
            and after.school_grade is None
            and after.school_grade_declined_at is None
        )
        print("Сверка:", "исходное состояние восстановлено" if ok else "НЕ СОШЛОСЬ")
        await engine.dispose()
        return 0 if ok else 4


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-741: откат тестового ответа про класс")
    ap.add_argument("--apply", action="store_true", help="выполнить запись")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
