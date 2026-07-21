# -*- coding: utf-8 -*-
"""tsk-332 (прод): снять коллизии order_position в курсах 1163/1164/1179.

ЧТО ДЕЛАЕТ
Первая диагностика tsk-332 проверяла инвариант `order_position` на курсах
917/1080 напрямую — но это узлы-контейнеры (`course_parents`), своих задач
не имеют. Реальная коллизия — в детях курса 1080: `1163` («Задание 11»,
6 задетых строк), `1164` («Задание 12», 4 строки), `1179` («Задание 14»,
42 строки). Причина — два прохода импорта внешних банков заданий
(`oge:reshu:*`, затем `sdamgia:oge:*`): второй, похоже, посчитал
MAX(order_position)+1 по снимку до завершения первого — классическая гонка
под read committed. Диагностика — reviews/2026-07-20-tsk332-order-position-diagnosis.md
(раздел "ИСПРАВЛЕННЫЙ РАЗБОР").

Фикс — МИНИМАЛЬНЫЙ и консервативный: НЕ придумывать новый порядок, а
формализовать тот, что уже реально отдаётся ученикам через
`LearningEngineService._ordered_task_rows` (`order_position ASC NULLS LAST,
id ASC`). Пересчёт `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY
order_position ASC NULLS LAST, id ASC)` даёт 100% ту же относительную
последовательность — просто без дублей и без пропусков в номерах.

ОБЛАСТЬ ДЕЙСТВИЯ
Прод (`learn`, 5.42.107.253). Только course_id IN (1163, 1164, 1179).

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
Пересчёт детерминирован из текущих данных — повторный запуск даёт тот же
результат. Порядок для читателей (SPW/TG_LMS) не меняется, т.к. новый
order_position строго монотонен относительно старого (только закрывает
пропуски и разводит дубли по уже действующему id-тайбрейку).

Запуск: dry-run по умолчанию (транзакция откатывается); --apply — запись
(нужен DBCHECK_OK=1 и go оператора).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(project_root, ".env"), encoding="utf-8-sig")

COURSE_IDS = (1163, 1164, 1179)

SELECT_PREVIEW = """
SELECT id, course_id, order_position,
       ROW_NUMBER() OVER (
           PARTITION BY course_id
           ORDER BY order_position ASC NULLS LAST, id ASC
       ) AS new_pos
FROM tasks
WHERE course_id = ANY($1::int[])
ORDER BY course_id, new_pos
"""

UPDATE_ONE = "UPDATE tasks SET order_position = $2 WHERE id = $1"


def _dsn() -> str:
    """Прод-DSN для learn. Берём из окружения или из .mcp.json (learn_prod_db)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        with open(os.path.join(project_root, ".mcp.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)"
            )

            rows = await conn.fetch(SELECT_PREVIEW, list(COURSE_IDS))
            print(f"Целевых строк (курсы {COURSE_IDS}): {len(rows)}")
            if len(rows) == 0:
                raise RuntimeError("кандидатов нет")

            changed = [r for r in rows if r["order_position"] != r["new_pos"]]
            print(f"Из них меняют номер: {len(changed)} (только закрытие пропусков/разводка дублей)")

            by_course: dict[int, list] = {}
            for r in rows:
                by_course.setdefault(r["course_id"], []).append(r)
            for cid, items in by_course.items():
                print(f"  course_id={cid}: {len(items)} строк, диапазон new_pos 1..{len(items)}")

            updated = 0
            for r in rows:
                res = await conn.execute(UPDATE_ONE, r["id"], r["new_pos"])
                updated += int(res.split()[-1])
            if updated != len(rows):
                raise AssertionError(f"ожидали обновить {len(rows)}, обновлено {updated}")

            # ---- Верификация внутри транзакции ----
            collisions = await conn.fetch(
                """
                SELECT course_id, order_position, COUNT(*) AS cnt
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id, order_position
                HAVING COUNT(*) > 1
                """,
                list(COURSE_IDS),
            )
            if collisions:
                raise AssertionError(f"остались коллизии order_position: {collisions}")

            for cid in COURSE_IDS:
                seq = await conn.fetch(
                    "SELECT order_position FROM tasks WHERE course_id = $1 ORDER BY order_position",
                    cid,
                )
                positions = [row["order_position"] for row in seq]
                expected = list(range(1, len(positions) + 1))
                if positions != expected:
                    raise AssertionError(
                        f"course_id={cid}: order_position не 1..N без пропусков: {positions[:10]}..."
                    )
                print(f"course_id={cid}: order_position = 1..{len(positions)} без пропусков и дублей — OK")

            print("\nOK: пересчёт применён, инвариант БД восстановлен, порядок для читателей не изменён.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
