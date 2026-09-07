# -*- coding: utf-8 -*-
"""tsk-816: переставить заведённые задания из конца курса в свои тематические места.

ЗАЧЕМ
`tsk816_create_tasks.py` вставлял задания без `order_position`, и триггер
`trg_set_task_order_position` ставил их в конец курса — так ничего не сдвигалось,
но новое задание оказывалось после самых сложных, вне своего блока. Решение
оператора: расставить по темам.

ПРАВИЛО ПОРЯДКА (сложившееся в этих курсах, см. reorder_tasks_by_difficulty_type)
Блоки по `difficulty_id` по возрастанию; внутри блока — по типу (SC/MC, затем
SA_COM, затем TBL_COM); внутри типа — смысловые группы. План размещения —
`reviews/tsk816-reorder-plan.json`: для каждого нового задания указано, ПОСЛЕ
какого существующего оно встаёт и почему.

КАК СЧИТАЕТСЯ НОВЫЙ ПОРЯДОК
Не «сдвинуть на N», а построить полный целевой список id для каждого затронутого
курса: берём текущий порядок, вынимаем из него новые задания и вставляем каждое
за своим якорем. Позиции переписываются как 1..N — это заодно убирает дыры
(в курсе 108 позиции 40 и 41 пустовали).

ТРИГГЕР И ОГРАНИЧЕНИЕ
На время массового UPDATE триггер порядка глушится через
`app.skip_task_order_trigger` — иначе он на каждой строке начал бы двигать
соседей. Ограничение `tasks_course_order_unique` отложенное (DEFERRABLE), так что
промежуточные коллизии внутри транзакции допустимы, а на COMMIT проверяются.

Запуск: dry-run по умолчанию (всегда ROLLBACK);
  python scripts/tsk816_reorder_new_tasks.py
  DBCHECK_OK=1 python scripts/tsk816_reorder_new_tasks.py --apply
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
DEFAULT_PLAN = project_root / "reviews" / "tsk816-reorder-plan.json"
BACKUP = project_root / "reviews" / "tsk816-reorder-before.json"

TYPE_RANK = {"SC": 1, "MC": 1, "TA": 2, "SA": 2, "SA_COM": 3}


def _dsn() -> str:
    """Прод-DSN базы learn: из окружения, иначе из .mcp.json проекта."""
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
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def build_target(current: list[int], moves: list[dict]) -> list[int]:
    """Целевой порядок id курса: вынуть перемещаемые и вставить за якорями."""
    moving = [i for m in moves for i in m["ids"]]
    if len(set(moving)) != len(moving):
        raise AssertionError(f"одно задание перемещается дважды: {moving}")
    rest = [i for i in current if i not in set(moving)]
    for m in moves:
        if m["after"] not in rest:
            raise AssertionError(f"якорь #{m['after']} не найден среди заданий курса")
        at = rest.index(m["after"]) + 1
        rest[at:at] = m["ids"]
    if sorted(rest) != sorted(current):
        raise AssertionError("состав курса изменился при перестановке")
    return rest


def check_monotonic(rows: dict[int, dict], order: list[int]) -> list[str]:
    """Не нарушает ли новый порядок правило «сложность не убывает, тип за ней»."""
    warns: list[str] = []
    prev_key: tuple[int, int] | None = None
    prev_id: int | None = None
    for tid in order:
        row = rows[tid]
        key = (row["difficulty_id"], TYPE_RANK.get(row["type"], 99))
        if prev_key is not None and key < prev_key:
            warns.append(
                f"#{tid} (d={key[0]}, {row['type']}) стоит после "
                f"#{prev_id} (d={prev_key[0]}) — сложность убывает")
        prev_key, prev_id = key, tid
    return warns


async def main(apply: bool, plan_path: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))["items"]
    by_course: dict[int, list[dict]] = {}
    for item in plan:
        by_course.setdefault(item["course_id"], []).append(item)

    print("=" * 78)
    print(f"tsk-816 · перестановка по темам · {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
    print("=" * 78)

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")

            total_moved = 0
            backup: dict[str, dict[str, int]] = {}
            for course_id, moves in sorted(by_course.items()):
                rows = {r["id"]: dict(r) for r in await conn.fetch(
                    "SELECT id, order_position, difficulty_id, "
                    "task_content->>'type' AS type, task_content->>'title' AS title "
                    "FROM tasks WHERE course_id = $1 ORDER BY order_position", course_id)}
                current = sorted(rows, key=lambda i: rows[i]["order_position"])
                backup[str(course_id)] = {str(i): rows[i]["order_position"] for i in current}

                target = build_target(current, moves)
                moving = {i for m in moves for i in m["ids"]}

                print(f"\nКурс {course_id} — {len(current)} заданий, "
                      f"переставляем {len(moving)}")
                for warn in check_monotonic(rows, target):
                    print(f"  ВНИМАНИЕ: {warn}")

                changed = [(i, rows[i]["order_position"], n)
                           for n, i in enumerate(target, 1)
                           if rows[i]["order_position"] != n]
                for tid in target:
                    n = target.index(tid) + 1
                    if tid in moving:
                        anchor = next(m["after"] for m in moves if tid in m["ids"])
                        print(f"  {rows[tid]['order_position']:>3} -> {n:>3}  #{tid} "
                              f"d={rows[tid]['difficulty_id']} {rows[tid]['title'][:46]}")
                        print(f"              за #{anchor} «{rows[anchor]['title'][:42]}»")
                print(f"  сдвинется всего строк: {len(changed)} "
                      f"(включая существующие, которые уехали на позицию ниже)")

                for n, tid in enumerate(target, 1):
                    if rows[tid]["order_position"] != n:
                        await conn.execute(
                            "UPDATE tasks SET order_position = $2 WHERE id = $1", tid, n)
                total_moved += len(moving)

                after = [r["id"] for r in await conn.fetch(
                    "SELECT id FROM tasks WHERE course_id = $1 ORDER BY order_position",
                    course_id)]
                if after != target:
                    raise AssertionError(f"курс {course_id}: порядок после UPDATE не тот")
                gaps = await conn.fetchval(
                    "SELECT count(*) FROM (SELECT order_position, "
                    "ROW_NUMBER() OVER (ORDER BY order_position) rn FROM tasks "
                    "WHERE course_id = $1) q WHERE order_position <> rn", course_id)
                if gaps:
                    raise AssertionError(f"курс {course_id}: позиции не сплошные (дыр {gaps})")

            print(f"\nПереставлено заданий: {total_moved}; "
                  f"позиции во всех затронутых курсах сплошные 1..N")

            if apply:
                BACKUP.write_text(json.dumps(backup, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
                print(f"Прежние позиции сохранены: {BACKUP.name}")
            else:
                raise RuntimeError("DRY-RUN: откатываю (повтор с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    args = ap.parse_args()
    path = Path(args.plan)
    if not path.is_absolute():
        path = project_root / path
    try:
        asyncio.run(main(args.apply, path))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
