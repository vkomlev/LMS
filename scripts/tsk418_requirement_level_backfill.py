# -*- coding: utf-8 -*-
"""tsk-418: бэкфилл requirement_level материалов/заданий ЕГЭ/Python по WP-канону.

Протокол /db-check: read (compute_proposals переиспользует ту же логику, что
и отчёт reviews/2026-08-03-tsk418-requirement-level-proposal.md) -> dry-run
(печать плана) -> транзакция -> верификация. Список одобрен оператором
2026-08-03 с двумя правками:
  - материал id=538 ("Как очистить список от дубликатов с помощью множества",
    курс 105) -> recommended (не skippable, как предлагал автомат — оператор:
    "нужная тема").
  - task external_uid=wp_nav:4:563025ee (курс 155) -> НЕ трогаем, остаётся
    required (единственное предложение по заданиям, оператор отклонил).
  - Несопоставленное (unresolved) — не трогаем, как и было.

Запуск:
  python scripts/tsk418_requirement_level_backfill.py <json>            # dry-run
  DBCHECK_OK=1 python scripts/tsk418_requirement_level_backfill.py <json> --apply
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk418_requirement_level_report import (  # noqa: E402
    _dsn,
    compute_proposals,
    load_scope,
)

# Оператор 2026-08-03: понизить это предложение до recommended (не skippable).
OVERRIDE_MATERIAL_AFTER: dict[int, str] = {538: "recommended"}
# Оператор 2026-08-03: не трогать (единственное предложение по заданиям).
SKIP_TASK_EXTERNAL_UIDS: set[str] = {"wp_nav:4:563025ee"}


def apply_operator_overrides(proposals: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for p in proposals:
        if p["kind"] == "material" and p["id"] in OVERRIDE_MATERIAL_AFTER:
            new_after = OVERRIDE_MATERIAL_AFTER[p["id"]]
            if new_after == p["before"]:
                continue  # оператор фактически отменил изменение
            p = {**p, "after": new_after, "match_reason": p["match_reason"] + " [override оператора]"}
        if p["kind"] == "task" and p.get("external_uid") in SKIP_TASK_EXTERNAL_UIDS:
            continue
        key = (p["kind"], p["id"])
        if key in seen:
            # WP-сторона дала два разных пункта списка на один и тот же объект LMS
            # (напр. один анкор упомянут и в text_lessons, и в video_lessons) —
            # применяем один раз, дубль в плане не нужен.
            continue
        seen.add(key)
        out.append(p)
    return out


def print_plan(proposals: list[dict]) -> None:
    print(f"План бэкфилла: {len(proposals)} строк\n")
    for p in proposals:
        obj = p["title"] if p["kind"] == "material" else f"{p['title']} ({p.get('external_uid')})"
        print(f"  [{p['kind']}] course={p['course_id']} id={p['id']} «{obj}» {p['before']} -> {p['after']}  ({p['match_reason']})")


async def run(json_path: Path, apply: bool) -> int:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    dsn = _dsn()
    conn = await asyncpg.connect(dsn)
    try:
        course_by_uid, materials_by_course, tasks_by_course = await load_scope(conn)
        proposals, _unresolved = compute_proposals(data, course_by_uid, materials_by_course, tasks_by_course)
        proposals = apply_operator_overrides(proposals)
        print_plan(proposals)

        if not apply:
            print("\nDRY-RUN — ничего не изменено. Повторите с --apply (и DBCHECK_OK=1) для записи.")
            return 0

        if os.environ.get("DBCHECK_OK") != "1":
            print("\nОШИБКА: --apply требует DBCHECK_OK=1 (протокол /db-check пройден).", file=sys.stderr)
            return 2

        material_rows = [p for p in proposals if p["kind"] == "material"]
        task_rows = [p for p in proposals if p["kind"] == "task"]

        async with conn.transaction():
            for p in material_rows:
                result = await conn.execute(
                    "UPDATE materials SET requirement_level = $1, updated_at = now() "
                    "WHERE id = $2 AND requirement_level = $3",
                    p["after"], p["id"], p["before"],
                )
                if result != "UPDATE 1":
                    raise RuntimeError(
                        f"material id={p['id']}: ожидал UPDATE 1 (было {p['before']}), получил {result!r} "
                        "— строка изменилась параллельно, откатываю транзакцию"
                    )
            for p in task_rows:
                result = await conn.execute(
                    "UPDATE tasks SET requirement_level = $1, updated_at = now() "
                    "WHERE id = $2 AND requirement_level = $3",
                    p["after"], p["id"], p["before"],
                )
                if result != "UPDATE 1":
                    raise RuntimeError(
                        f"task id={p['id']}: ожидал UPDATE 1 (было {p['before']}), получил {result!r} "
                        "— строка изменилась параллельно, откатываю транзакцию"
                    )

        # верификация после commit
        bad = 0
        for p in material_rows:
            row = await conn.fetchrow("SELECT requirement_level FROM materials WHERE id = $1", p["id"])
            if not row or row["requirement_level"] != p["after"]:
                print(f"ВЕРИФИКАЦИЯ ПРОВАЛЕНА: material id={p['id']} ожидал {p['after']}, получил {row}")
                bad += 1
        for p in task_rows:
            row = await conn.fetchrow("SELECT requirement_level FROM tasks WHERE id = $1", p["id"])
            if not row or row["requirement_level"] != p["after"]:
                print(f"ВЕРИФИКАЦИЯ ПРОВАЛЕНА: task id={p['id']} ожидал {p['after']}, получил {row}")
                bad += 1

        if bad:
            print(f"\n{bad} строк не прошли верификацию — смотри выше.")
            return 3
        print(f"\nПрименено и верифицировано: {len(material_rows)} материалов, {len(task_rows)} заданий.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run(Path(args.json_path), args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
