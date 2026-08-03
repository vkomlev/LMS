"""tsk-545 — сгенерировать РЕАЛЬНЫЙ magic-link токен для victor.v.komlev@gmail.com
(user_id=142) для живой проверки, без похода через email (нет доступа к почте).

Использует `app.services.auth.magic_link_service.create_magic_link` — ТОТ ЖЕ
сервисный путь, что реальная ручка "войти по email" вызывает перед отправкой
письма через Resend; здесь просто пропускается шаг отправки (получаем raw-токен
напрямую и открываем `/auth/magic-link/consume?token=...` живьём в браузере —
сервер сам погасит magic_link, создаст user_session и выставит httpOnly cookie
`session` штатным Set-Cookie в ответе на переход, без инъекции cookie через JS).

Запуск (DSN прод-роли из .mcp.json):
    DBCHECK_OK=1 python scripts/tsk545_mint_magic_link_user142.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EMAIL = "victor.v.komlev@gmail.com"


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

    from app.services.auth.magic_link_service import create_magic_link

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-545: mint magic-link for {EMAIL} — {mode} ===\n")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        token = await create_magic_link(db, EMAIL)
        if apply:
            await db.commit()
            print(f"COMMIT. TOKEN={token}")
            print(f"CONSUME_URL=https://learn.victor-komlev.ru/auth/magic-link/consume?token={token}")
        else:
            await db.rollback()
            print("ROLLBACK — dry-run, токен не сохранён.")

        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
