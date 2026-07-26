# -*- coding: utf-8 -*-
"""tsk-414 (класс B): восстановить возрастающий порядок "Задание N" в курсах
108 "Работа со строками" и 111 "Условные конструкции" (курс 88 "Python для ЕГЭ").

Диагноз (сверка живьём, чип tsk-414): cq-вопросы (external_uid содержит ':cq:')
и практические задания "Задание N" (external_uid оканчивается на ':N', N —
0-based номер из исходного WP) — это две подряд идущие группы order_position
внутри курса: сначала все cq (order_position 1..K), затем практические задания
БЕЗ учёта их WP-номера N — из-за этого страница показывает задания вразнобой
(QA: "задание 9 потеряно", "нумерация не по порядку"). Фикс — задать
order_position практических заданий = K + N + 1, сохранив исходный относительный
порядок cq-блока нетронутым.

Механизм: BEFORE INSERT/UPDATE триггер set_task_order_position() на каждую
строку каскадно сдвигает соседей (см. app/db/migrations/versions/
20260521_120000_tasks_order_position_triggers.py) — прямой построчный UPDATE
запутает порядок. Как и сам триггер при массовом пересчёте (reorder_tasks_
after_delete), глушим его на время пакета через session var
app.skip_task_order_trigger, выставляем итоговые позиции одним проходом,
затем проверяем уникальность (course_id, order_position) до commit.

Запуск: dry-run по умолчанию;
  python scripts/tsk414_fix_task_order.py
  DBCHECK_OK=1 python scripts/tsk414_fix_task_order.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
COURSE_IDS = [108, 111]
PLAIN_SUFFIX_RE = re.compile(r":(\d+)$")


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


def plan_course(rows: list[asyncpg.Record]) -> list[tuple[int, int, int]]:
    """Возвращает [(task_id, old_pos, new_pos), ...] для строк, которым нужен новый order_position."""
    cq_positions = [r["order_position"] for r in rows if ":cq:" in r["external_uid"]]
    if not cq_positions:
        raise AssertionError("не нашёл cq-блок — план не для этого курса")
    base = max(cq_positions)

    plan: list[tuple[int, int, int]] = []
    seen_targets: dict[int, int] = {}
    for r in rows:
        uid = r["external_uid"]
        if ":cq:" in uid:
            continue
        m = PLAIN_SUFFIX_RE.search(uid)
        assert m, f"external_uid без числового суффикса: {uid}"
        n = int(m.group(1))
        target = base + n + 1
        if target in seen_targets:
            raise AssertionError(f"коллизия целевой позиции {target}: id={r['id']} и id={seen_targets[target]}")
        seen_targets[target] = r["id"]
        if r["order_position"] != target:
            plan.append((r["id"], r["order_position"], target))
    return plan


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            full_plan: list[tuple[int, int, int, int]] = []  # course_id, task_id, old, new
            for course_id in COURSE_IDS:
                rows = await conn.fetch(
                    "SELECT id, order_position, external_uid FROM tasks "
                    "WHERE course_id = $1 AND is_active = true ORDER BY order_position",
                    course_id,
                )
                plan = plan_course(rows)
                print(f"--- курс {course_id}: {len(plan)} задач меняют order_position ---")
                for task_id, old, new in plan:
                    print(f"  id={task_id}: {old} -> {new}")
                    full_plan.append((course_id, task_id, old, new))

            if apply:
                await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
                for course_id, task_id, old, new in full_plan:
                    await conn.execute(
                        "UPDATE tasks SET order_position = $1 WHERE id = $2",
                        new, task_id,
                    )
                await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'false', true)")

                # Верификация внутри транзакции: уникальность (course_id, order_position)
                # и совпадение с планом.
                for course_id in COURSE_IDS:
                    dup = await conn.fetchval(
                        "SELECT count(*) FROM ("
                        "  SELECT order_position FROM tasks WHERE course_id=$1 AND is_active=true"
                        "  GROUP BY order_position HAVING count(*) > 1"
                        ") d",
                        course_id,
                    )
                    if dup:
                        raise AssertionError(f"курс {course_id}: {dup} дублирующихся order_position после апдейта")
                for course_id, task_id, old, new in full_plan:
                    actual = await conn.fetchval("SELECT order_position FROM tasks WHERE id=$1", task_id)
                    if actual != new:
                        raise AssertionError(f"id={task_id}: после UPDATE order_position={actual}, ожидалось {new}")
                print(f"\nВерификация внутри транзакции: OK, {len(full_plan)} строк, дублей нет.")

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
