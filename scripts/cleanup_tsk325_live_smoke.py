# -*- coding: utf-8 -*-
"""tsk-325: убрать тест-артефакты живого прод-прогона (source_system='tsk325_live').

Живой прогон F1/F5 открывал попытки под юзером 142 (Виктор) и слал ответы, чтобы
проверить приём на боевом API. Эти попытки/результаты помечены source_system=
'tsk325_live' — снимаем их, чтобы не засорять историю. Разовый скрипт.

Запуск: dry-run по умолчанию; --apply при DBCHECK_OK=1.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
MARK = "tsk325_live"
USER = 142


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        for arg in cfg.get("mcpServers", cfg)["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn.")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM attempts WHERE source_system=$1 AND user_id=$2", MARK, USER)]
            print(f"Тест-попыток '{MARK}' у user {USER}: {len(ids)} — {ids}")
            if not ids:
                raise RuntimeError("нечего чистить")
            tr = await conn.execute(
                "DELETE FROM task_results WHERE attempt_id = ANY($1::int[])", ids)
            at = await conn.execute(
                "DELETE FROM attempts WHERE source_system=$1 AND user_id=$2", MARK, USER)
            print(f"Удалено task_results: {tr} | attempts: {at}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (--apply при DBCHECK_OK=1)")
        print("ОЧИЩЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
