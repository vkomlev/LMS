# -*- coding: utf-8 -*-
"""tsk-689, этап 3: снять три задания, которые нельзя привести в порядок.

РЕШЕНИЕ ОПЕРАТОРА (26.08, по итогам этапов 1-2): снять.

- 2384 — потеряно САМО описание игры («В игре, описанной в задании 19...»),
  подбор по известному ответу перебором типовых игр не дал ничего; восстановить
  честно нельзя, а дописывать вопросы не к чему.
- 4036 и 4232 («съесть не более половины конфет») — решатель по эталонному
  алгоритму не воспроизвёл их ответы (11 и 95 против 15 и 63): либо правило
  игры записано неточно, либо неверен сам эталон. Гейт «сперва воспроизведи
  имеющийся ответ» они не прошли, значит трогать их содержимое нельзя.

Снимаются через `is_active = false` — строки остаются, сдач у них нет ни одной,
откат — тот же UPDATE обратно.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ
Перевески «Сложных» под курсы ЕГЭ не делаем: курс 1378 «Сложные задания» УЖЕ
подкурс 112 «ЕГЭ по информатике» (№27), то есть его содержимое и так в дереве
курса, на который записаны ученики. Пустой `user_courses` у 1378 — норма для
подкурса, а не признак недоступности; вывод «эти задания не видит никто»,
записанный по итогам этапа 2, был ошибочным и исправлен в артефактах.

БЕЗОПАСНОСТЬ
- `trg_task_audit_update` НЕ глушится: снятие `is_active` обязано попасть в
  `task_audit`. Актёр — `tsk-689`.
- После снятия остаются дырки в нумерации; активные задания затронутых курсов
  перенумеровываются подряд. Триггер порядка глушится своим же флагом
  (`app.skip_task_order_trigger`) — он реализует «вставку со сдвигом» и при
  массовой перенумерации каскадил бы.

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

# id -> почему снимаем
DEACTIVATE = {
    2384: "потеряно описание игры, восстановить не удалось",
    4036: "решатель не воспроизвёл эталон (11 против 15)",
    4232: "решатель не воспроизвёл эталон (95 против 63)",
}

RENUMBER_COURSES = [147, 1397]  # где после снятия остаются дырки в нумерации


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


async def _snapshot(conn: asyncpg.Connection, title: str) -> None:
    print(f"\n=== {title} ===")
    rows = await conn.fetch(
        """
        SELECT c.id, c.title,
               (SELECT count(*) FROM tasks t
                 WHERE t.course_id = c.id AND t.is_active) AS aktivnyh,
               (SELECT count(*) FROM tasks t
                 WHERE t.course_id = c.id AND t.is_active
                   AND t.requirement_level IN ('required','skippable')) AS obyazatelnyh
        FROM courses c WHERE c.id = ANY($1::int[]) ORDER BY c.id
        """,
        [146, 147, 1396, 1397, 1464],
    )
    for r in rows:
        print(f"  {r['id']:>5} {r['title'][:46]:<46} активных {r['aktivnyh']:>3}, "
              f"обязательных {r['obyazatelnyh']:>3}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-689 этап 3: снять три битых задания")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        await _snapshot(conn, "ДО")

        found = await conn.fetch(
            "SELECT id, course_id, is_active FROM tasks WHERE id = ANY($1::int[])",
            list(DEACTIVATE),
        )
        if len(found) != len(DEACTIVATE):
            print("СТОП: нашлись не все задания из списка на снятие.")
            return 2
        results = await conn.fetchval(
            "SELECT count(*) FROM task_results WHERE task_id = ANY($1::int[])",
            list(DEACTIVATE),
        )
        print(f"\nК снятию {len(found)} заданий, сдач у них: {results}")
        if results:
            print("СТОП: у снимаемых заданий есть сдачи — нужно решение оператора.")
            return 2
        for r in found:
            print(f"  {r['id']} (курс {r['course_id']}, активно={r['is_active']}): "
                  f"{DEACTIVATE[r['id']]}")

        if not args.apply:
            print("\nDry-run: записи не было.")
            return 0

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-689', true)")
            st = await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = ANY($1::int[]) AND is_active",
                list(DEACTIVATE),
            )
            print(f"\nСнято заданий: {st.rsplit(' ', 1)[-1]}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            for course_id in RENUMBER_COURSES:
                rows = await conn.fetch(
                    "SELECT id FROM tasks WHERE course_id = $1 AND is_active "
                    "ORDER BY order_position NULLS LAST, id",
                    course_id,
                )
                ids = [r["id"] for r in rows]
                await conn.execute(
                    """
                    UPDATE tasks t SET order_position = v.pos
                    FROM (SELECT unnest($1::int[]) AS id,
                                 generate_subscripts($1::int[], 1) AS pos) v
                    WHERE t.id = v.id AND t.order_position IS DISTINCT FROM v.pos
                    """,
                    ids,
                )
                print(f"  курс {course_id}: перенумеровано {len(ids)} активных заданий")

        await _snapshot(conn, "ПОСЛЕ")

        ok = True
        still_active = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) AND is_active",
            list(DEACTIVATE),
        )
        if still_active:
            ok = False
            print(f"  РАСХОЖДЕНИЕ: {still_active} снимаемых заданий остались активными")
        gaps = await conn.fetch(
            """
            SELECT course_id, count(*) AS n, min(order_position) AS lo, max(order_position) AS hi
            FROM tasks WHERE course_id = ANY($1::int[]) AND is_active
            GROUP BY course_id ORDER BY course_id
            """,
            RENUMBER_COURSES,
        )
        for g in gaps:
            solid = g["lo"] == 1 and g["hi"] == g["n"]
            print(f"  курс {g['course_id']}: {g['n']} заданий, позиции {g['lo']}..{g['hi']}"
                  f"{'' if solid else '  ← нумерация с дырками'}")
            ok = ok and solid
        return 0 if ok else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
