# -*- coding: utf-8 -*-
"""tsk-324: проставить task_content.hints_video для заданий ЕГЭ (kompege/sdamgia/polyakov)
по маппингу видео из ТГ-канала @cyberguru_ege, построенному tsk324_build_video_hint_mapping.py.

ЧТО ДЕЛАЕТ
Для tasks.external_uid LIKE 'ext:d4:{kompege|sdamgia|polyakov}:%' -- если задание
активно (is_active) И task_content->'hints_video' сейчас пусто И для его числового
ID (последний сегмент external_uid) в маппинге есть видео -- добавляет
task_content.hints_video = [ссылки], has_hints = true. Ничего не перезаписывает
(WHERE hints_video пусто -- находки с уже проставленной подсказкой не трогаются).

ИСТОЧНИК МАППИНГА
reviews/tsk324-video-hint-mapping.json (scp с локальной машины, построен read-only
запросом к content_backbone prod по заголовку VK-видео -- см. докстринг билдера).
Этот скрипт доступа к ContentBackbone не имеет и не требует.

BLAST-RADIUS / ИДЕМПОТЕНТНОСТЬ
UPDATE трогает только tasks с совпавшим external_uid-префиксом И пустым
hints_video -- независимые прогоны идемпотентны (повторный запуск не находит
уже обновлённые строки, т.к. hints_video больше не пуст). stem/solution_rules/
answer_raw не затрагиваются (patch добавляет ровно 2 верхнеуровневых ключа через
||), проверяется md5-сверкой до/после в транзакции.

Запуск: dry-run по умолчанию (транзакция откатывается); --apply -- запись
(нужен DBCHECK_OK=1, прод-хост 5.42.107.253, прод LMS DSN из /opt/lms/.env).
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
MAPPING_PATH = project_root / "reviews" / "tsk324-video-hint-mapping.json"

SOURCES = ("kompege", "sdamgia", "polyakov")

SELECT_CANDIDATES = """
SELECT id, external_uid,
       split_part(external_uid, ':', 3) AS src,
       split_part(external_uid, ':', 5)::bigint AS ext_id,
       task_content->>'type' AS type,
       md5(COALESCE(task_content->>'stem','')) AS stem_md5,
       md5(COALESCE(solution_rules::text,'')) AS solrules_md5
FROM tasks
WHERE external_uid LIKE ANY(ARRAY['ext:d4:kompege:%', 'ext:d4:sdamgia:%', 'ext:d4:polyakov:%'])
  AND is_active
  AND jsonb_array_length(COALESCE(task_content->'hints_video', '[]'::jsonb)) = 0
"""

UPDATE_ONE = """
UPDATE tasks
SET task_content = task_content || jsonb_build_object(
        'hints_video', $2::jsonb,
        'has_hints', true
    )
WHERE id = $1
  AND is_active
  AND jsonb_array_length(COALESCE(task_content->'hints_video', '[]'::jsonb)) = 0
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


def _load_mapping() -> dict[str, dict[str, list[str]]]:
    if not MAPPING_PATH.exists():
        raise RuntimeError(f"Нет файла маппинга: {MAPPING_PATH} (запусти сначала build_video_hint_mapping.py локально и scp сюда)")
    return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))


async def main(apply: bool) -> None:
    mapping = _load_mapping()
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            candidates = await conn.fetch(SELECT_CANDIDATES)
            print(f"Кандидатов (активны, hints_video пуст, ext:d4:{{kompege,sdamgia,polyakov}}): {len(candidates)}")

            plan: list[tuple[int, str, list[str]]] = []
            no_video: dict[str, int] = {s: 0 for s in SOURCES}
            for r in candidates:
                videos = mapping.get(r["src"], {}).get(str(r["ext_id"]))
                if videos:
                    plan.append((r["id"], r["external_uid"], videos))
                else:
                    no_video[r["src"]] = no_video.get(r["src"], 0) + 1

            by_src: dict[str, int] = {}
            for _id, uid, _v in plan:
                src = uid.split(":")[2]
                by_src[src] = by_src.get(src, 0) + 1
            print(f"Найдено видео и будет обновлено: {len(plan)} -> {by_src}")
            print(f"Кандидатов без видео в маппинге (пропускаем): {no_video}")
            print("Примеры (id, external_uid -> видео):")
            for tid, uid, videos in plan[:8]:
                print(f"  id={tid} {uid} -> {videos}")

            if not plan:
                print("\nНечего обновлять.")
                if not apply:
                    raise RuntimeError("DRY-RUN: откатываю (нечего применять)")
                return

            target_ids = [p[0] for p in plan]
            before = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, task_content->>'type' AS type, "
                    "md5(COALESCE(task_content->>'stem','')) AS stem_md5, "
                    "md5(COALESCE(solution_rules::text,'')) AS solrules_md5 "
                    "FROM tasks WHERE id = ANY($1::int[])",
                    target_ids,
                )
            }

            updated = 0
            for tid, uid, videos in plan:
                payload = json.dumps(videos, ensure_ascii=False)
                res = await conn.execute(UPDATE_ONE, tid, payload)
                updated += int(res.split()[-1])
            print(f"\nUPDATE затронул строк: {updated} (ожидали {len(plan)})")
            if updated != len(plan):
                raise AssertionError(f"обновлено {updated} != {len(plan)} — расхождение состояния (кто-то параллельно писал)")

            after = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, task_content->'hints_video' AS hv, "
                    "(task_content->>'has_hints')::bool AS has_hints, "
                    "task_content->>'stem' IS NOT NULL AS has_stem, "
                    "md5(COALESCE(task_content->>'stem','')) AS stem_md5, "
                    "md5(COALESCE(solution_rules::text,'')) AS solrules_md5 "
                    "FROM tasks WHERE id = ANY($1::int[])",
                    target_ids,
                )
            }
            for tid, uid, videos in plan:
                a = after[tid]
                hv = json.loads(a["hv"]) if a["hv"] else []
                if hv != videos:
                    raise AssertionError(f"id={tid}: hints_video={hv} != {videos}")
                if a["has_hints"] is not True:
                    raise AssertionError(f"id={tid}: has_hints={a['has_hints']} != true")
                if a["stem_md5"] != before[tid]["stem_md5"]:
                    raise AssertionError(f"id={tid}: stem ИЗМЕНЁН — недопустимо")
                if a["solrules_md5"] != before[tid]["solrules_md5"]:
                    raise AssertionError(f"id={tid}: solution_rules ИЗМЕНЁН — недопустимо")

            print(f"Верификация: у всех {len(plan)} hints_video совпал с планом, has_hints=true, "
                  "stem и solution_rules не изменены.")
            print("\nOK: подсказки проставлены, коллатералей нет.")
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
