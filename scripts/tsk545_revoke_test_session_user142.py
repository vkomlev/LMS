"""tsk-545 — отозвать тестовую сессию user_id=142, выпущенную
`tsk545_mint_test_session_user142.py` для живой проверки.

Запуск (DSN прод-роли из .mcp.json):
    DBCHECK_OK=1 python scripts/tsk545_revoke_test_session_user142.py --apply <session_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


async def main(apply: bool, session_id: str) -> int:
    import os

    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    sys.path.insert(0, str(PROJECT_ROOT))
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    from app.services.auth.session_service import revoke_session

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-545: revoke test session {session_id} — {mode} ===\n")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        await revoke_session(db, UUID(session_id))
        if apply:
            await db.commit()
            print("COMMIT — сессия отозвана.")
        else:
            await db.rollback()
            print("ROLLBACK — dry-run.")
        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("session_id")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply, session_id=args.session_id)))
