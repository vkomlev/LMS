# -*- coding: utf-8 -*-
"""tsk-324: построить маппинг {external_id -> [vk.com видео]} из ContentBackbone.

READ-ONLY скрипт, запускается ЛОКАЛЬНО (не на прод-сервере LMS): подключается
к прод-БД ContentBackbone (cb_prod, .mcp.json) и вытаскивает соответствие
"числовой ID задания источника (kompege/sdamgia/polyakov)" -> "VK-ссылки на
видео-разбор" из ЗАГОЛОВКА самого видео (content_hub.source_item, source_system=
'vk_importer', raw->>'title' вида "... Разбираем задание №N (N_ID) (Источник)").

ПОЧЕМУ ЧЕРЕЗ ЗАГОЛОВОК, А НЕ ЧЕРЕЗ content_hub.asset.source_item_id
Первая версия джойнила видео к посту через content_hub.asset (content_hash файла,
привязанного к ТГ-посту) -> vk_importer source_item с тем же hash. Оказалось,
что один и тот же файл (по content_hash) бывает прикреплён к ДВУМ РАЗНЫМ постам
(напр. пост 14856 "17_37359" технически нёс тот же hash, что и настоящее видео
задания 70532) -- джойн через asset тихо приписывал чужой видео. Заголовок VK-
видео, который человек вписывает при заливке ("Разбираем задание №5 (5_70532)"),
оказался надёжнее: это то, что реально имеет в виду автор ролика. См. reviews/
2026-08-06-tsk324-video-hints.md.

Результат пишется в JSON (ext_id как строка -> список ссылок, дедуп по URL),
отдельно на источник. Файл потом переносится (scp) на прод-сервер LMS и
используется write-скриптом tsk324_apply_video_hints.py -- тот НЕ имеет доступа
к ContentBackbone и просто читает готовый маппинг.
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

SOURCES = {
    "kompege": r"\(\d+_\d+\)\s*\(?\s*(КЕГЭ|Комп\s*ЕГЭ|КомпЕГЭ)",
    "sdamgia": r"\(\d+_\d+\)\s*\(?\s*(Решу\s*ЕГЭ|РешуЕГЭ)",
    "polyakov": r"\(\d+_\d+\)\s*\(?\s*Поляков",
}

QUERY = """
SELECT (regexp_match(raw->>'title', '\\((\\d+)_(\\d+)\\)'))[2]::bigint AS ext_id,
       raw->>'video_url' AS video_url
FROM content_hub.source_item
WHERE source_system = 'vk_importer'
  AND raw->>'video_url' IS NOT NULL
  AND raw->>'title' ~* $1
"""


def _cb_dsn() -> str:
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["content_backbone_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("Не нашёл прод-DSN content_backbone (5.42.107.253) в .mcp.json")


async def main() -> None:
    conn = await asyncpg.connect(_cb_dsn())
    try:
        result: dict[str, dict[str, list[str]]] = {}
        for src, pattern in SOURCES.items():
            rows = await conn.fetch(QUERY, pattern)
            per_id: dict[int, set[str]] = {}
            for r in rows:
                per_id.setdefault(r["ext_id"], set()).add(r["video_url"])
            result[src] = {str(k): sorted(v) for k, v in sorted(per_id.items())}
            print(f"{src}: {len(per_id)} различных ext_id с видео")
    finally:
        await conn.close()

    out_path = project_root / "reviews" / "tsk324-video-hint-mapping.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nЗаписано: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
