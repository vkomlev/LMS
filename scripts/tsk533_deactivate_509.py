# -*- coding: utf-8 -*-
"""tsk-533 п.10: материал 509 «Срезы, кортежи, операторы списков» (курс 109).

Живая проверка (claude-in-chrome, 2026-08-02) показала: видео
https://vk.com/video-53400615_456239811 УДАЛЕНО правообладателем
("Это видео изъято по обращению правообладателя"), не просто не по теме.
Заменить нечем (нет доступа к загрузке нового видео) — деактивируем материал
(is_active=false), чтобы не показывать студентам мёртвую ссылку. Направление
безопасно по конструкции (required-> не активен снижает знаменатель
compute_course_state, откат прогресса недостижим, см. memory
project_material_order_triggers_and_activation_risk). 16 студентов уже имеют
historical student_material_progress по 509 — не трогается, только текущая
выдача новым/непройденным ученикам отключается.

Запуск: dry-run по умолчанию;
  python scripts/tsk533_deactivate_509.py
  DBCHECK_OK=1 python scripts/tsk533_deactivate_509.py --apply
"""
from __future__ import annotations

import argparse
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
MATERIAL_ID = 509


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT id, title, is_active, content_provenance FROM materials WHERE id = $1",
            MATERIAL_ID,
        )
        print(f"Материал {MATERIAL_ID}: «{row['title']}», is_active={row['is_active']}")

        if not apply:
            print("DRY-RUN: ничего не записано. Повтор с --apply.")
            return

        async with conn.transaction():
            provenance = {
                "source": "manual_web",
                "edited_at": "2026-08-02",
                "edited_by": "tsk-533",
                "fields": ["is_active"],
                "note": "видео удалено правообладателем (живая проверка claude-in-chrome), заменить нечем",
            }
            await conn.execute(
                "UPDATE materials SET is_active = false, content_provenance = $1::jsonb WHERE id = $2",
                json.dumps(provenance), MATERIAL_ID,
            )
            check = await conn.fetchrow(
                "SELECT is_active FROM materials WHERE id = $1", MATERIAL_ID
            )
            print(f"Верификация: is_active={check['is_active']} (ожидание False)")
            if check["is_active"] is not False:
                raise RuntimeError("Деактивация не сошлась — ROLLBACK.")
        print("COMMIT выполнен.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-533 п.10: деактивировать материал 509 (видео удалено)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
