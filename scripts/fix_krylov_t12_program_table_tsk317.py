# -*- coding: utf-8 -*-
"""tsk-317 доп. фикс: восстановить таблицу программы МТ у v5t12/v11t12.

Обнаружено при подготовке импорта 57 доп. Крылов-заданий (tsk-319): у ДВУХ уже
записанных заданий №12 (сборник Крылова, тип «исполнитель МТ») в stem
отсутствует таблица «Программа работы исполнителя» — без неё задание
нерешаемо (тот же класс дефекта, что у графа/таблицы истинности в t1/t2,
просто не был замечен в первом проходе). Картинка обрезана с тех же страниц
PDF, залита в CAS/S3 (проверена публичная доступность), <img> вставляется
по тому же анкеру, что и в основном скрипте.

Запуск: dry-run по умолчанию; --apply при DBCHECK_OK=1.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import asyncpg

project_root = Path(__file__).resolve().parents[1]


def _dsn() -> str:
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("prod DSN не найден в .mcp.json")


FIXES = [
    (
        4562,  # crylov:v5t12
        "а к последовательности ячейке. После выполнения программы",
        "а к последовательности ячейке.<br>Программа работы исполнителя:<br>"
        '<img src="/api/v1/media/2c38eca417d92607e5d7a267bdd67092db4a18279678753eca01bb4286795e69.png"/><br>'
        "После выполнения программы",
    ),
    (
        4563,  # crylov:v11t12
        "Программа работы исполнителя:</p>",
        "Программа работы исполнителя:<br>"
        '<img src="/api/v1/media/89d5718a775bcaf482a5d40c1e25360ca2261665ad33f23d5520edfd40a20f14.png"/></p>',
    ),
]


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for tid, anchor, replacement in FIXES:
                stem = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", tid)
                if stem is None:
                    raise RuntimeError(f"id={tid}: не найден")
                cnt = stem.count(anchor)
                if cnt != 1:
                    raise RuntimeError(f"id={tid}: анкер встречается {cnt} раз (нужно 1): {anchor!r}")
                if "<img" in stem:
                    raise RuntimeError(f"id={tid}: stem уже содержит <img> — не дублировать")
                new_stem = stem.replace(anchor, replacement, 1)
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb($2::text)) "
                    "WHERE id = $1",
                    tid, new_stem,
                )
                check = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", tid)
                if "/api/v1/media/" not in check:
                    raise AssertionError(f"id={tid}: <img> не подтвердился")
                print(f"OK id={tid} stem_len={len(check)}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
