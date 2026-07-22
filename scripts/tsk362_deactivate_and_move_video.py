# -*- coding: utf-8 -*-
"""tsk-362: деактивировать два задания и перенести видеоразбор туда, где он полезен.

РЕШЕНИЕ ОПЕРАТОРА 2026-07-22

**3332** («Задание 17, Апробация 14.05.25») — файла с данными нет и взять его негде:
апробация нигде не опубликована. Без файла задание нерешаемо → деактивировать.

**3455** («Задания 26 ЕГЭ на Unix время. Аналог заданий 26_40742, 26_41001») — это авторский
разбор класса задач с собственными данными, которых тоже нет. Само задание деактивируется,
а **видеоразбор переносится** на задание, к которому он относится по смыслу: 3774
(`wp_nav:26:9d60fd95`, sdamgia:40742) — та самая задача про UNIX-время, и ответ у неё уже
заведён («5000 46»). Разбор перестаёт висеть при мёртвом задании и начинает работать на живое.

Второй адрес из поста — sdamgia:41001 — в LMS отсутствует, прикреплять не к чему; отмечено
в отчёте.

ИДЕМПОТЕНТНОСТЬ
Деактивация обратима. Видео добавляется в список `hints_video` задания 3774, только если его
там ещё нет; прежний список сохраняется (сейчас он пуст). У 3455 видео остаётся на месте —
задание деактивировано, но не обеднено.

Запуск: dry-run по умолчанию;
  python scripts/tsk362_deactivate_and_move_video.py
  DBCHECK_OK=1 python scripts/tsk362_deactivate_and_move_video.py --apply
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

DEACTIVATE = {
    3332: "«Апробация 14.05.25» — файла с данными нет и взять негде",
    3455: "авторская задача-аналог, данных нет; разбор перенесён на 3774",
}
VIDEO_FROM, VIDEO_TO = 3455, 3774


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
            # ---- Шаг 1: перенос видеоразбора ----
            src = await conn.fetchrow(
                "SELECT task_content->>'hints_video' AS v FROM tasks WHERE id = $1", VIDEO_FROM)
            dst = await conn.fetchrow(
                "SELECT task_content->>'hints_video' AS v, task_content->>'has_hints' AS h "
                "FROM tasks WHERE id = $1 AND is_active", VIDEO_TO)
            if dst is None:
                raise AssertionError(f"задание {VIDEO_TO} не найдено или неактивно")
            videos_src = json.loads(src["v"]) if src["v"] else []
            videos_dst = json.loads(dst["v"]) if dst["v"] else []
            if not videos_src:
                raise AssertionError(f"у {VIDEO_FROM} нет видеоразбора — переносить нечего")
            merged = videos_dst + [u for u in videos_src if u not in videos_dst]
            print(f"Видеоразбор {VIDEO_FROM} → {VIDEO_TO}: было {videos_dst}, станет {merged}")
            await conn.execute(
                "UPDATE tasks SET task_content = jsonb_set("
                "  jsonb_set(task_content, '{hints_video}', $2::jsonb, true),"
                "  '{has_hints}', 'true'::jsonb, true) "
                "WHERE id = $1", VIDEO_TO, json.dumps(merged))

            # ---- Шаг 2: деактивация ----
            for tid, why in DEACTIVATE.items():
                row = await conn.fetchrow(
                    "SELECT is_active, left(regexp_replace(task_content->>'stem','<[^>]+>',' ','g'), 60) AS s "
                    "FROM tasks WHERE id = $1", tid)
                print(f"Деактивирую {tid} ({why}): было активно={row['is_active']} «{row['s'].strip()}…»")
            res = await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = ANY($1::int[]) AND is_active",
                list(DEACTIVATE))
            print(f"Деактивировано: {res}")

            # ---- Верификация ----
            after = await conn.fetch(
                "SELECT id, is_active, task_content->>'hints_video' AS v, task_content->>'has_hints' AS h "
                "FROM tasks WHERE id = ANY($1::int[])", list(DEACTIVATE) + [VIDEO_TO])
            for r in after:
                print(f"  ПОСЛЕ id={r['id']} активно={r['is_active']} видео={r['v']} подсказки={r['h']}")
            if any(r["is_active"] for r in after if r["id"] in DEACTIVATE):
                raise AssertionError("не все деактивированы")
            dst_after = next(r for r in after if r["id"] == VIDEO_TO)
            if not dst_after["is_active"] or json.loads(dst_after["v"]) != merged:
                raise AssertionError(f"перенос видео не применился: {dst_after['v']}")

            print("\nOK: все проверки пройдены.")
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
