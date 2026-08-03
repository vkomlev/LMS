"""tsk-545 — выпустить тестовую сессию для аккаунта 142 (живая проверка).

Контекст: живая проверка фикса требует УЧЕНИЧЕСКОГО вида (`/courses/{uid}`)
под аккаунтом 142, а реальная Chrome-сессия оператора (и `mcp__claude-in-chrome`,
и `.claude-live-profile`) в моменте резолвится в id=2 («Виктор Комлев», teacher/
admin, 0 enrollments) — сессия дрейфует между аккаунтами (см. memory
project_prod_live_testing). Магик-линк на victor.v.komlev@gmail.com недоступен
(нет доступа к почте), а `magic_link.token_hash` хранит только хэш — токен из
БД не восстановить.

Санкционированный в CLAUDE.md воркэраунд («выпуск тестового токена в БД»):
через `app.services.auth.session_service.create_session` (тот же сервисный
путь, что дёргает реальная verify-магик-линк ручка) минтится настоящая строка
`user_session` для user_id=142 — не сырой INSERT мимо сервиса, TTL/hash те же,
что у обычного логина. Токен ставится в браузер как cookie `session`
(`COOKIE_DOMAIN=learn.victor-komlev.ru` на проде, host-only).

Обратимо: `revoke_session(db, session_id)` сразу после живой проверки
(см. `scripts/tsk545_revoke_test_session_user142.py`).

Запуск (DSN прод-роли из .mcp.json):
    DBCHECK_OK=1 python scripts/tsk545_mint_test_session_user142.py --apply
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

    from app.services.auth.session_service import create_session

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-545: mint test session for user {USER_ID} — {mode} ===\n")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        user = (await db.execute(
            text("SELECT id, full_name, email FROM users WHERE id = :u"),
            {"u": USER_ID},
        )).mappings().first()
        if not user:
            print(f"user_id={USER_ID} не найден.")
            await engine.dispose()
            return 1
        print(f"Пользователь: {dict(user)}")

        access_token, refresh_token, session = await create_session(
            db, user_id=USER_ID, ua_fingerprint="tsk545-live-check"
        )
        if apply:
            await db.commit()
            print(f"\nCOMMIT. session_id={session.id}")
            print(f"ACCESS_TOKEN={access_token}")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, сессия не создана.")

        await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить (COMMIT) и напечатать токен.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
