"""tsk-545 — временно назначить курс 1425 «Excel — от основ до автоматизации»
аккаунту 142 для живой проверки фикса (второй, финальный выбор дерева).

Первая попытка (курс 1283 «Тестировщик ПО», см. `tsk545_assign_qa_course_to_
user142.py`, уже отменена) оказалась непригодна: «Глава 1» там — не лист, а
контейнер с 66 заданиями + 33 материалами по всему поддереву (полный подсчёт
`_collect_courses_in_order`, а не только прямые дети) — непрактично пройти
живьём. Курс 1427 «Тема 1.1. Что такое электронные таблицы» в дереве 1425 —
самый маленький неоконченный `required_course_id` в реальных `course_dependencies`
на проде (7 заданий + 5 материалов), а его зависимый курс 1270 «Интерфейс Excel
и виды данных» — РОДНОЙ СИБЛИНГ 1427 (оба — дети «Глава 1. Основы» = 1426,
order_number 1 и 2) — весь тест умещается на ОДНОЙ странице курса 1425, без
перехода между курсами вообще.

Обратимо: `scripts/tsk545_unassign_excel_course_from_user142.py`.

Запуск (DSN прод-роли из .mcp.json):
    DBCHECK_OK=1 python scripts/tsk545_assign_excel_course_to_user142.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_ID = 142
COURSE_ID = 1425


def load_prod_dsn_asyncpg_style() -> str:
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(apply: bool) -> int:
    import os

    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    sys.path.insert(0, str(PROJECT_ROOT))
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    from app.services.user_courses_service import UserCoursesService

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-545: assign course {COURSE_ID} -> user {USER_ID} — {mode} ===\n")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        existing = (await db.execute(
            text("SELECT * FROM user_courses WHERE user_id = :u AND course_id = :c"),
            {"u": USER_ID, "c": COURSE_ID},
        )).mappings().first()
        if existing:
            print(f"Уже назначено: {dict(existing)} — ничего не делаю.")
            await engine.dispose()
            return 0

        service = UserCoursesService()
        if apply:
            created = await service.assign_course_with_order(db, user_id=USER_ID, course_id=COURSE_ID)
            print(f"\nСоздано (COMMIT сервисом внутри create): {created.__dict__}")
        else:
            print("DRY-RUN: имитируем INSERT в отдельной транзакции с ROLLBACK.")
            await db.execute(
                text(
                    "INSERT INTO user_courses (user_id, course_id, added_at, is_active) "
                    "VALUES (:u, :c, now(), true)"
                ),
                {"u": USER_ID, "c": COURSE_ID},
            )
            after = (await db.execute(
                text("SELECT * FROM user_courses WHERE user_id = :u AND course_id = :c"),
                {"u": USER_ID, "c": COURSE_ID},
            )).mappings().first()
            print(f"AFTER (в транзакции, будет откачено): {dict(after) if after else None}")
            await db.rollback()
            print("ROLLBACK — dry-run, изменения откатаны.")

        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить назначение (COMMIT).")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
