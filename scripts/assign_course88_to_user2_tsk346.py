"""tsk-346 — доназначить курс 88 «Python для ЕГЭ» пользователю id=2 для живого прогона.

Контекст: живая проверка курса 111 «Условные конструкции» (после перетега/реордера
контрольных вопросов) требует enrollment через родительский курс 88 (иерархия
курсов — прямой записи на дочерний 111 не бывает, см. tsk-346 / user_courses).
Текущая Chrome-сессия резолвится в user_id=2 («Виктор Комлев», teacher/admin),
у которого 0 строк в user_courses кроме курса 112 (ЕГЭ-навигатор, не Python-ветка).
Оператор явно попросил назначить доступ, если его нет.

Идёт через `UserCoursesService.assign_course_with_order` (сервисный путь, НЕ прямой
INSERT) — он же дёргает `course_dependencies_enrollment_service.ensure_dependencies_assigned`
(у курса 88 зависимостей нет — проверено отдельным SELECT перед этим скриптом, но
сервис всё равно самодостаточен на будущее). order_number не передаём — триггер БД
проставит автоматически.

Обратимо: DELETE FROM user_courses WHERE user_id=2 AND course_id=88.

Запуск (подключение к прод-БД, DSN из .mcp.json подставляется в DATABASE_URL):
    python scripts/assign_course88_to_user2_tsk346.py                  # dry-run (ROLLBACK)
    DBCHECK_OK=1 python scripts/assign_course88_to_user2_tsk346.py --apply   # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_ID = 2
COURSE_ID = 88


def load_prod_dsn_asyncpg_style() -> str:
    """DSN роли lms_prod из .mcp.json, в формате postgresql+asyncpg:// для SQLAlchemy."""
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
    print(f"=== tsk-346: assign course {COURSE_ID} -> user {USER_ID} — {mode} ===\n")

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

        print(f"BEFORE: строки user_courses для user_id={USER_ID}: "
              f"{[dict(r) for r in (await db.execute(text('SELECT * FROM user_courses WHERE user_id = :u'), {'u': USER_ID})).mappings()]}")

        service = UserCoursesService()
        if apply:
            created = await service.assign_course_with_order(db, user_id=USER_ID, course_id=COURSE_ID)
            print(f"\nСоздано (COMMIT сервисом внутри create): {created.__dict__}")
        else:
            print("\nDRY-RUN: сервис сам коммитит внутри create() — "
                  "имитируем INSERT в отдельной транзакции с ROLLBACK, не вызывая сервис.")
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
            print("\nROLLBACK — dry-run, изменения откатаны.")

        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить назначение (COMMIT).")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
