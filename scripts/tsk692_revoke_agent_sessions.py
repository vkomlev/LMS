# -*- coding: utf-8 -*-
"""tsk-692: отозвать временные сессии, заведённые агентом для живой проверки.

Для проверки правила глазами нужен вход именно под учеником: экран ученика
отличается от экрана преподавателя, а живая сессия профиля резолвится в аккаунт
преподавателя. Сессии выпускались штатным magic-link
(`scripts/tsk690_mint_magic_link_student.py`) и после проверки должны быть
погашены — оставлять живой токен чужого аккаунта нельзя.

Отзываются ТОЛЬКО перечисленные `--session-id` — собственные сессии учеников,
заведённые ими самими, не трогаются. Список берётся из `user_session` по времени
создания прогона и сверяется до записи.

Запуск:
    python scripts/tsk692_revoke_agent_sessions.py --session-id <UUID> ...
    DBCHECK_OK=1 python scripts/tsk692_revoke_agent_sessions.py --session-id <UUID> ... --apply
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


async def main(session_ids: list[str], apply: bool) -> int:
    import os

    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    try:
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, user_id, created_at, revoked_at FROM user_session "
                        "WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY created_at"
                    ),
                    {"ids": session_ids},
                )
            ).mappings().all()

            print("== состояние до ==")
            for r in rows:
                mark = "уже отозвана" if r["revoked_at"] else "активна"
                print(f"  {r['id']} ученик {r['user_id']} {r['created_at']} — {mark}")
            missing = set(session_ids) - {str(r["id"]) for r in rows}
            if missing:
                print(f"  НЕ НАЙДЕНЫ: {sorted(missing)}")

            live = [r for r in rows if r["revoked_at"] is None]
            if not live:
                print("\nОтзывать нечего.")
                return 0

            if not apply:
                print(f"\nЭто предпросмотр: будет отозвано {len(live)}. Для записи — --apply.")
                return 0

            updated = (
                await conn.execute(
                    text(
                        "UPDATE user_session SET revoked_at = now() "
                        "WHERE id = ANY(CAST(:ids AS uuid[])) AND revoked_at IS NULL"
                    ),
                    {"ids": session_ids},
                )
            ).rowcount
            print(f"\n== запись ==\n  отозвано: {updated}")

            after = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM user_session "
                        "WHERE id = ANY(CAST(:ids AS uuid[])) AND revoked_at IS NULL"
                    ),
                    {"ids": session_ids},
                )
            ).scalar()
            print(f"== состояние после ==\n  активных из списка осталось: {after}")
            if after:
                raise RuntimeError("Часть сессий осталась активной — откатываю транзакцию")
    finally:
        await engine.dispose()

    print("\nГотово, транзакция зафиксирована.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", action="append", required=True, dest="session_ids")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.session_ids, args.apply)))
