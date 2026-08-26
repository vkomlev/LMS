# -*- coding: utf-8 -*-
"""tsk-689, этап 1б: привести «уровень обязательности» к новому месту задания.

ЗАЧЕМ
Курсы «Сложные» (1396, 1397) целиком помечены `recommended` — они вне зачёта
(решение tsk-347). Движок выдаёт только `required`/`skippable`
(`learning_engine_service._ordered_task_rows`), поэтому задание, переехавшее из
«Сложных» в базовый курс, но сохранившее `recommended`, не появится в учебном
пути вовсе: перенос оказался бы холостым. И наоборот — задание, уехавшее из
базового курса в «Сложные», обязано перестать быть обязательным, иначе оно
останется в зачёте курса, которого ученику не назначали.

ЧТО ПРАВИМ (ровно то, что переехало в tsk689_reorder_blocks_18_19_21.py)
- 14 заданий, переехавших ВВЕРХ (1396→146, 1397→147) → `required`;
- 2 задания, переехавших ВНИЗ (146→1396) → `recommended`.
Больше ничего: `recommended`-материалы (599, 602, 597) — дополнительные видео,
их статус к переносу отношения не имеет.

ПОСЛЕДСТВИЕ ДЛЯ УЧЕНИКОВ
14 заданий добавляются в зачёт курса 112 всем записанным на него. У Селина Егора
(4507), закрывшего блок 18, появится 9 новых пунктов; у Анфалова Глеба (4512) в
блоке 19-21 — 5. Это прямое следствие решения оператора «простое из Сложных —
в базовый курс».

БЕЗОПАСНОСТЬ
`order_position` и `course_id` не трогаем — триггеры порядка при UPDATE выходят
сразу. `trg_task_audit_update` на смену `requirement_level` не срабатывает
(он смотрит course_id / is_active / solution_rules). Откат — тот же UPDATE
обратно по тем же id.

Запуск: dry-run по умолчанию; `--apply` — запись (нужен префикс DBCHECK_OK=1).
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
sys.path.insert(0, str(project_root))

# Переехали вверх, в базовые курсы → возвращаются в зачёт.
TO_REQUIRED = {
    146: [4212, 4207, 3968, 3963, 3964, 3966, 3628, 3627, 3569],
    147: [3949, 4538, 3981, 3505, 4539],
}
# Переехали вниз, в «Сложные» → выходят из зачёта, как весь этот курс.
TO_RECOMMENDED = {
    1396: [2309, 2308],
}


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
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


async def _snapshot(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT course_id, requirement_level, count(*) AS n
        FROM tasks WHERE course_id = ANY($1::int[]) AND is_active
        GROUP BY course_id, requirement_level ORDER BY course_id, requirement_level
        """,
        [146, 1396, 147, 1397, 1464],
    )
    for r in rows:
        print(f"  курс {r['course_id']}: {r['requirement_level']} = {r['n']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-689: уровень обязательности после переноса")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        print("=== ДО ===")
        await _snapshot(conn)

        planned = 0
        for course_id, ids in TO_REQUIRED.items():
            found = await conn.fetch(
                "SELECT id, requirement_level FROM tasks "
                "WHERE id = ANY($1::int[]) AND course_id = $2",
                ids, course_id,
            )
            if len(found) != len(ids):
                print(f"СТОП: в курсе {course_id} нашлись не все id из списка — состав изменился.")
                return 2
            need = [r["id"] for r in found if r["requirement_level"] != "required"]
            planned += len(need)
            print(f"\nКурс {course_id} → required: {len(need)} из {len(ids)}: {need}")

        for course_id, ids in TO_RECOMMENDED.items():
            found = await conn.fetch(
                "SELECT id, requirement_level FROM tasks "
                "WHERE id = ANY($1::int[]) AND course_id = $2",
                ids, course_id,
            )
            if len(found) != len(ids):
                print(f"СТОП: в курсе {course_id} нашлись не все id из списка.")
                return 2
            need = [r["id"] for r in found if r["requirement_level"] != "recommended"]
            planned += len(need)
            print(f"Курс {course_id} → recommended: {len(need)} из {len(ids)}: {need}")

        if not args.apply:
            print(f"\nDry-run: записи не было. К обновлению {planned} строк.")
            return 0

        async with conn.transaction():
            total = 0
            for course_id, ids in TO_REQUIRED.items():
                st = await conn.execute(
                    "UPDATE tasks SET requirement_level = 'required' "
                    "WHERE id = ANY($1::int[]) AND course_id = $2 "
                    "AND requirement_level <> 'required'",
                    ids, course_id,
                )
                total += int(st.rsplit(" ", 1)[-1] or 0)
            for course_id, ids in TO_RECOMMENDED.items():
                st = await conn.execute(
                    "UPDATE tasks SET requirement_level = 'recommended' "
                    "WHERE id = ANY($1::int[]) AND course_id = $2 "
                    "AND requirement_level <> 'recommended'",
                    ids, course_id,
                )
                total += int(st.rsplit(" ", 1)[-1] or 0)
            print(f"\nОбновлено строк: {total}")

        print("\n=== ПОСЛЕ ===")
        await _snapshot(conn)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
