"""tsk-545 — откат временного назначения курса 1425 пользователю 142
(см. `scripts/tsk545_assign_excel_course_to_user142.py`) после живой проверки.

Запуск (DSN прод-роли из .mcp.json):
    DBCHECK_OK=1 python scripts/tsk545_unassign_excel_course_from_user142.py --apply
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

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-545: unassign course {COURSE_ID} <- user {USER_ID} — {mode} ===\n")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        existing = (await db.execute(
            text("SELECT * FROM user_courses WHERE user_id = :u AND course_id = :c"),
            {"u": USER_ID, "c": COURSE_ID},
        )).mappings().first()
        if not existing:
            print("Уже не назначено — нечего откатывать.")
            await engine.dispose()
            return 0

        print(f"BEFORE: {dict(existing)}")
        result = await db.execute(
            text("DELETE FROM user_courses WHERE user_id = :u AND course_id = :c"),
            {"u": USER_ID, "c": COURSE_ID},
        )
        print(f"DELETE rowcount={result.rowcount}")
        if apply:
            await db.commit()
            print("COMMIT.")
        else:
            await db.rollback()
            print("ROLLBACK — dry-run, изменения откатаны.")

        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
