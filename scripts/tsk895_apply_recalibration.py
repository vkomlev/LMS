# -*- coding: utf-8 -*-
"""tsk-895: применение перекалибровки задания 5 ЕГЭ по итогам разбора
(см. `docs/specs/2026-09-10-tsk895-zadanie5-uroven.md`).

Решение оператора (10.09): не все структурные усложнители тянут на перенос
в «Сложные» — телеметрия подтвердила, что 8 из 10 кандидатов уже НОРМАЛЬНО
сдаются как NORMAL внутри курса 156 (82-100%, ноль заявок помощи). Реально
переносится только id=2291 — единственное задание уровня 7 («неподъёмное
пространство перебора», магнит заявок помощи, тот самый пример со скриншота
оператора). Плюс — выравнивание меток сложности внутри курса 156 по итогам
шкалы.

Пять правок:
  1. id=2150 (8-битное представление, критерий №2) — EASY -> NORMAL
  2. id=2069 (два входных числа, новый признак) — EASY -> NORMAL
  3. id=2286 — NORMAL -> EASY (структурно не отличается от соседей уровня 1)
  4. id=3289 — NORMAL -> EASY (то же самое)
  5. id=2291 — перенос 156 -> 1383, HARD, requirement_level=recommended,
     order_position=79 (следующая свободная позиция в 1383, явно — не NULL,
     чтобы не улететь в хвост за снятыми заданиями, мина tsk-802)

Зачёты (`task_results`) не трогаются вообще — правило «не потерять сдачи».
У 2291 есть живые сдачи (11 попыток, 10 зачтено) — после переноса задание
уходит из числителя/знаменателя прогресса 156 (recommended не считается),
как это уже проверено прецедентом tsk-862/tsk-689.

Запуск (после протокола /db-check):
    DBCHECK_OK=1 python scripts/tsk895_apply_recalibration.py --apply
Без --apply — только план.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("tsk895-apply")

DIFF = {"THEORY": 1, "EASY": 2, "NORMAL": 3, "HARD": 4, "PROJECT": 5}

PROVENANCE_COMMON = {
    "task": "tsk-895",
    "canon": None,
    "source": "LMS/docs/specs/2026-09-10-tsk895-zadanie5-uroven.md",
    "decided_at": "2026-09-10",
    "changed": True,
}

DIFFICULTY_CHANGES = [
    (2150, DIFF["NORMAL"],
     "уровень 3 шкалы (восьмибитное/фикс. разрядности представление, критерий "
     "оператора №2); телеметрия (86.7% зачёт с 1-й, 0 заявок) не требовала "
     "переноса в «Сложные», но метка EASY занижала реальный шаг сложности"),
    (2069, DIFF["NORMAL"],
     "уровень 5 шкалы (два независимых входных числа N и M, новый признак); "
     "телеметрия (92.3%, 0 заявок) не требовала переноса, но EASY занижала шаг"),
    (2286, DIFF["EASY"],
     "уровень 1 шкалы (подсчёт различных значений, двоичная база, без "
     "усложняющих признаков) — не отличается от соседей уровня 1, все из "
     "которых EASY; выравнивание меток по итогам шкалы"),
    (3289, DIFF["EASY"],
     "уровень 1 шкалы (двоичное преобразование, без усложняющих признаков) — "
     "то же выравнивание, что и 2286"),
]

MOVE_2291 = {
    "id": 2291,
    "new_course_id": 1383,
    "new_order_position": 79,
    "new_requirement_level": "recommended",
    "new_difficulty_id": DIFF["HARD"],
    "evidence": (
        "уровень 7 шкалы — счётная задача на образе функции на отрезке "
        "[123456789; 1987654321], прямой перебор ~230 млн итераций "
        "неподъёмен, ответ подтверждён точным методом (непересекающиеся "
        "блоки [8N,8N+7], см. scripts/tsk895_hard_solvers.py); магнит "
        "заявок помощи (3 заявки) — тот самый пример со скриншота оператора "
        "из постановки tsk-895"
    ),
}


def load_prod_dsn_asyncpg_style() -> str:
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main_async(apply: bool) -> int:
    import asyncpg

    conn = await asyncpg.connect(load_prod_dsn_asyncpg_style().replace("+asyncpg", ""))
    try:
        logger.info("=== План правок (было -> станет) ===")

        rows = await conn.fetch(
            "SELECT id, task_content->>'title' AS title, course_id, "
            "difficulty_id, order_position, requirement_level "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            [d[0] for d in DIFFICULTY_CHANGES] + [MOVE_2291["id"]],
        )
        by_id = {r["id"]: r for r in rows}

        for task_id, new_diff, evidence in DIFFICULTY_CHANGES:
            row = by_id[task_id]
            logger.info(
                "id=%s %r: difficulty %s -> %s | %s",
                task_id, row["title"], row["difficulty_id"], new_diff, evidence,
            )

        row = by_id[MOVE_2291["id"]]
        logger.info(
            "id=%s %r: course %s->%s, position %s->%s, requirement %s->%s, "
            "difficulty %s->%s | %s",
            MOVE_2291["id"], row["title"], row["course_id"],
            MOVE_2291["new_course_id"], row["order_position"],
            MOVE_2291["new_order_position"], row["requirement_level"],
            MOVE_2291["new_requirement_level"], row["difficulty_id"],
            MOVE_2291["new_difficulty_id"], MOVE_2291["evidence"],
        )

        # sanity: убедиться, что позиция 79 в 1383 свободна
        clash = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE course_id=1383 AND order_position=$1",
            MOVE_2291["new_order_position"],
        )
        if clash:
            raise RuntimeError(
                f"позиция {MOVE_2291['new_order_position']} в курсе 1383 уже занята"
            )

        # sanity: подтвердить, что зачёты 2291 на месте (не трогаем, но проверяем ДО)
        results_before = await conn.fetchval(
            "SELECT count(*) FROM task_results WHERE task_id=$1", MOVE_2291["id"]
        )
        logger.info("task_results у id=2291 до правки: %d строк (не трогаем)", results_before)

        if not apply:
            logger.info("Без --apply: только план. Для записи — DBCHECK_OK=1 ... --apply")
            return 0

        async with conn.transaction():
            for task_id, new_diff, evidence in DIFFICULTY_CHANGES:
                prov = {**PROVENANCE_COMMON, "method": "перекалибровка задания 5 (шкала уровней)",
                        "evidence": evidence}
                await conn.execute(
                    "UPDATE tasks SET difficulty_id=$1, difficulty_provenance=$2::jsonb "
                    "WHERE id=$3",
                    new_diff, json.dumps(prov, ensure_ascii=False), task_id,
                )

            prov_2291 = {**PROVENANCE_COMMON,
                         "method": "перекалибровка задания 5 (шкала уровней, перенос 156->1383)",
                         "evidence": MOVE_2291["evidence"]}
            await conn.execute(
                "UPDATE tasks SET course_id=$1, order_position=$2, "
                "requirement_level=$3, difficulty_id=$4, difficulty_provenance=$5::jsonb "
                "WHERE id=$6",
                MOVE_2291["new_course_id"], MOVE_2291["new_order_position"],
                MOVE_2291["new_requirement_level"], MOVE_2291["new_difficulty_id"],
                json.dumps(prov_2291, ensure_ascii=False), MOVE_2291["id"],
            )

        # верификация после
        rows_after = await conn.fetch(
            "SELECT id, course_id, difficulty_id, order_position, requirement_level "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            [d[0] for d in DIFFICULTY_CHANGES] + [MOVE_2291["id"]],
        )
        logger.info("=== Состояние после записи ===")
        for r in rows_after:
            logger.info(dict(r))

        results_after = await conn.fetchval(
            "SELECT count(*) FROM task_results WHERE task_id=$1", MOVE_2291["id"]
        )
        if results_after != results_before:
            raise RuntimeError(
                f"ЗАЧЁТЫ ПОТЕРЯНЫ: было {results_before}, стало {results_after}"
            )
        logger.info("task_results у id=2291 после правки: %d строк (совпадает)", results_after)

        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись (иначе только план)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    import asyncio
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    if sys.platform == "win32":
        import os
        os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
