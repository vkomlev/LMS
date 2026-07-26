# -*- coding: utf-8 -*-
"""tsk-414 (доделка): "Переместить видео по комментариям после теории по
комментариям" (письмо QA ЧАСТЬ 1, курс 106 "Первая программа на Python.
Основные конструкции").

Видео "Комментарии в Python" (id=464, order_position=30) стояло ДО теории
"Комментарии в Python" (id=243, order_position=35). Точечный фикс — минимальный
сдвиг order_position видео за пределы теории (36), остальные материалы курса
не трогаем (в отличие от курсов 108/111 здесь не нужен полный renumber — только
одна пара меняется местами).

Запуск: dry-run по умолчанию;
  python scripts/tsk414_fix_comments_video_order_course106.py
  DBCHECK_OK=1 python scripts/tsk414_fix_comments_video_order_course106.py --apply
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
COURSE_ID = 106
VIDEO_ID = 464   # "Комментарии в Python" (video)
THEORY_ID = 243  # "Комментарии в Python" (text)
NEW_POSITION = 36


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
        async with conn.transaction():
            video = await conn.fetchrow(
                "SELECT id, order_position, title, type FROM materials WHERE id=$1 AND course_id=$2",
                VIDEO_ID, COURSE_ID,
            )
            theory = await conn.fetchrow(
                "SELECT id, order_position, title, type FROM materials WHERE id=$1 AND course_id=$2",
                THEORY_ID, COURSE_ID,
            )
            assert video is not None and theory is not None, "материалы не найдены"
            assert video["type"] == "video" and theory["type"] == "text", "типы не совпадают с ожиданием"
            assert video["order_position"] < theory["order_position"], (
                f"видео (order_position={video['order_position']}) уже после теории "
                f"(order_position={theory['order_position']}) — фикс не нужен"
            )
            print(f"ДО:    видео id={VIDEO_ID} order_position={video['order_position']}, "
                  f"теория id={THEORY_ID} order_position={theory['order_position']}")
            print(f"ПОСЛЕ: видео id={VIDEO_ID} order_position={NEW_POSITION} (> {theory['order_position']})")

            if apply:
                await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")
                await conn.execute(
                    "UPDATE materials SET order_position = $1 WHERE id = $2",
                    NEW_POSITION, VIDEO_ID,
                )
                await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'false', true)")

                dup = await conn.fetchval(
                    "SELECT count(*) FROM materials WHERE course_id=$1 AND is_active=true AND order_position=$2",
                    COURSE_ID, NEW_POSITION,
                )
                if dup != 1:
                    raise AssertionError(f"после UPDATE на order_position={NEW_POSITION} стоит {dup} строк, ожидалась 1")
                actual = await conn.fetchval("SELECT order_position FROM materials WHERE id=$1", VIDEO_ID)
                if actual != NEW_POSITION:
                    raise AssertionError(f"id={VIDEO_ID}: order_position={actual}, ожидалось {NEW_POSITION}")
                print("\nВерификация внутри транзакции: OK.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
