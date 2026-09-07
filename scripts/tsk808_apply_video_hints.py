# -*- coding: utf-8 -*-
"""tsk-808: проставить task_content.hints_video по плану tsk808-video-hint-plan.json.

ЧТО ДЕЛАЕТ
Для каждого задания из плана — если оно активно И `task_content->'hints_video'`
сейчас пусто — записывает `hints_video` = [ссылки на ВК] и `has_hints` = true.
Существующие подсказки не трогаются: у 711 активных заданий они уже стоят, и
`WHERE jsonb_array_length(... ) = 0` защищает их и от гонки с чужой сессией.

ИСТОЧНИК ПЛАНА
`reviews/tsk808-video-hint-plan.json`, построенный
`tsk808_build_video_hint_plan.py` (read-only, обе прод-базы). Там же описана
цепочка ТГ-пост -> ролик -> публикация -> задание и почему текстовый ключ
отклонён. Этот скрипт в ContentBackbone не ходит.

BLAST-RADIUS / ИДЕМПОТЕНТНОСТЬ
Патч добавляет к `task_content` ровно два верхнеуровневых ключа через `||`;
`stem`, `solution_rules`, `answer_raw`, `hints_text` сверяются по md5 до и
после, расхождение — исключение и откат. `order_position` не меняется, значит
триггер `set_task_order_position` выходит на первой же ветке
(`NEW.order_position = old_order -> RETURN NEW`) и позиции заданий не двигает —
проверено на боевой схеме 07.09, пересечения с правкой этого триггера в
tsk-802 нет. Повторный запуск не находит уже обновлённые строки.

Запуск: dry-run по умолчанию (транзакция откатывается);
        `DBCHECK_OK=1 python scripts/tsk808_apply_video_hints.py --apply` — запись.
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
PLAN_PATH = project_root / "reviews" / "tsk808-video-hint-plan.json"

SELECT_BEFORE = """
SELECT id, external_uid, course_id, is_active,
       task_content->'hints_video' AS hv,
       md5(COALESCE(task_content->>'stem', '')) AS stem_md5,
       md5(COALESCE(task_content->>'hints_text', '')) AS hints_text_md5,
       md5(COALESCE(task_content->>'answer_raw', '')) AS answer_md5,
       md5(COALESCE(solution_rules::text, '')) AS solrules_md5,
       order_position
FROM tasks WHERE id = ANY($1::int[])
"""

UPDATE_ONE = """
UPDATE tasks
SET task_content = task_content || jsonb_build_object('hints_video', $2::jsonb, 'has_hints', true)
WHERE id = $1
  AND is_active
  AND jsonb_array_length(COALESCE(task_content->'hints_video', '[]'::jsonb)) = 0
"""

SELECT_AFTER = """
SELECT id, task_content->'hints_video' AS hv,
       (task_content->>'has_hints')::bool AS has_hints,
       md5(COALESCE(task_content->>'stem', '')) AS stem_md5,
       md5(COALESCE(task_content->>'hints_text', '')) AS hints_text_md5,
       md5(COALESCE(task_content->>'answer_raw', '')) AS answer_md5,
       md5(COALESCE(solution_rules::text, '')) AS solrules_md5,
       order_position
FROM tasks WHERE id = ANY($1::int[])
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        for arg in cfg["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


async def main(apply: bool) -> None:
    if not PLAN_PATH.exists():
        raise RuntimeError(f"Нет плана: {PLAN_PATH} — сначала tsk808_build_video_hint_plan.py")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    for p in plan:
        bad = [v for v in p["videos"] if v.get("link_check") not in (None, "ОК")]
        if bad:
            raise RuntimeError(f"задание {p['task_id']}: в плане ролик, который не открылся — {bad}")
    ids = [p["task_id"] for p in plan]
    print(f"План: {len(plan)} заданий, {sum(len(p['videos']) for p in plan)} ссылок")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            before = {r["id"]: r for r in await conn.fetch(SELECT_BEFORE, ids)}
            missing = [i for i in ids if i not in before]
            if missing:
                raise AssertionError(f"нет таких заданий: {missing}")

            todo = []
            for p in plan:
                row = before[p["task_id"]]
                if not row["is_active"]:
                    print(f"  пропуск id={p['task_id']}: задание неактивно")
                    continue
                if json.loads(row["hv"] or "[]"):
                    print(f"  пропуск id={p['task_id']}: подсказка уже стоит ({row['hv']})")
                    continue
                todo.append((p, [v["url"] for v in p["videos"]]))

            print(f"\nБудет обновлено: {len(todo)} заданий")
            print("Выборка (первые 10):")
            for p, urls in todo[:10]:
                print(f"  id={p['task_id']:<6} {p['external_uid']:<42} курс {p['course_id']}")
                for u in urls:
                    print(f"        -> {u}")
            if not todo:
                raise RuntimeError("DRY-RUN: нечего применять")

            updated = 0
            for p, urls in todo:
                res = await conn.execute(UPDATE_ONE, p["task_id"], json.dumps(urls, ensure_ascii=False))
                updated += int(res.split()[-1])
            print(f"\nUPDATE затронул строк: {updated} (ожидали {len(todo)})")
            if updated != len(todo):
                raise AssertionError(f"обновлено {updated} != {len(todo)} — кто-то писал параллельно")

            after = {r["id"]: r for r in await conn.fetch(SELECT_AFTER, ids)}
            for p, urls in todo:
                a, b = after[p["task_id"]], before[p["task_id"]]
                if json.loads(a["hv"] or "[]") != urls:
                    raise AssertionError(f"id={p['task_id']}: hints_video={a['hv']} != {urls}")
                if a["has_hints"] is not True:
                    raise AssertionError(f"id={p['task_id']}: has_hints={a['has_hints']}")
                for field in ("stem_md5", "hints_text_md5", "answer_md5", "solrules_md5"):
                    if a[field] != b[field]:
                        raise AssertionError(f"id={p['task_id']}: изменилось {field} — недопустимо")
                if a["order_position"] != b["order_position"]:
                    raise AssertionError(
                        f"id={p['task_id']}: order_position {b['order_position']} -> {a['order_position']} "
                        "— триггер порядка сработал, откатываю")
            print(f"Верификация: у всех {len(todo)} hints_video совпал с планом, has_hints=true; "
                  "stem, hints_text, answer_raw, solution_rules и order_position не изменились.")
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
