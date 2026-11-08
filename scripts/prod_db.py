# Read/write доступ к ПРОД-БД LMS (и ContentBackbone). DSN берётся из .mcp.json
# (learn_prod_db / content_backbone_prod_db) — секрет не дублируется.
#
# Чтение — scripts/prod_db_read.py (без гейта). Запись — этот файл, только с --write
# и DBCHECK_OK=1 после полного протокола /db-check (снимок схемы → план → предварительный
# SELECT → подтверждение оператора), в одной транзакции, с верификацией.
# В прод БД нет клонов-изоляторов: любой write сразу виден ученикам.

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

WRITE_RE = re.compile(r"^\s*(insert|update|delete|truncate|alter|drop|create|grant)", re.I)


def prod_dsn(server: str) -> str:
    mcp_path = PROJECT_ROOT / ".mcp.json"
    cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
    try:
        args = cfg["mcpServers"][server]["args"]
    except KeyError:
        sys.exit(f"server '{server}' не найден в .mcp.json")
    dsn = next(a for a in args if a.startswith("postgresql://"))
    # asyncpg не декодирует percent-encoding пароля в URL — декодируем самостоятельно
    from urllib.parse import quote, unquote, urlsplit, urlunsplit

    parts = urlsplit(dsn)
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    host = parts.hostname or ""
    port = parts.port or 5432
    dbname = parts.path.lstrip("/")
    query = parts.query
    return f"postgresql+asyncpg://{quote(user)}:{quote(password)}@{host}:{port}/{dbname}" + (
        f"?{query}" if query else ""
    )


def mask(dsn: str) -> str:
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)


async def run(server: str, sql: str, is_write: bool) -> None:
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", encoding="utf-8-sig")
    dsn = prod_dsn(server)
    print(f"[prod_db] server={server} dsn={mask(dsn)} write={is_write}")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql))
            if is_write:
                await conn.commit()
                print(f"[prod_db] OK, rows affected: {result.rowcount}")
            else:
                await conn.rollback()
                cols = list(result.keys())
                rows = result.fetchall()
                print("[prod_db] columns:", cols)
                for row in rows[:200]:
                    print(row)
                if len(rows) > 200:
                    print(f"[prod_db] ... ещё {len(rows) - 200} строк (LIMIT 200 вывода)")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prod DB access (DSN из .mcp.json)")
    parser.add_argument("sql")
    parser.add_argument("--server", default="learn_prod_db",
                        choices=["learn_prod_db", "content_backbone_prod_db"])
    parser.add_argument("--write", action="store_true", help="DML/DDL (требует DBCHECK_OK=1)")
    args = parser.parse_args()

    if args.write and os.environ.get("DBCHECK_OK") != "1":
        sys.exit("Запись в прод заблокирована: сначала пройди протокол /db-check и запусти с DBCHECK_OK=1")
    if not args.write and WRITE_RE.match(args.sql):
        sys.exit("Похоже на запись без --write. Для DML/DDL используй --write + DBCHECK_OK=1")

    asyncio.run(run(args.server, args.sql, args.write))


if __name__ == "__main__":
    main()
