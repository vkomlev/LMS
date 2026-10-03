"""tsk-1202: перенести 92 «Словарь арифметических операций» и 94 «Плагины в словаре функций»
из курса 104 «Функции в Python» в 107 «Работа со словарями в Python» (прод).

Почему: функции идут в Python-цепочке раньше словарей, ученики курса 104 словарей ещё не знают.

Перенос — точечный UPDATE, как в tsk-1132/tsk-1160 (PATCH не меняет course_id, bulk_upsert
затирает task_content). Триггер `trg_set_task_order_position` (ветка переезда tsk-802) сам
уплотняет 104 и ставит задание в конец 107 (позиция NULL = конец). requirement_level=required —
как у всех заданий 107. Сдачи (`task_results`) привязаны к заданию: движок судит о задании по
последней сдаче независимо от курса попытки (compute_task_state), поэтому они не трогаются.

Кеш `student_course_state` ведётся и для узлов 104/107, и для корней — пересчитывается штатным
`LearningEngineService.compute_course_state` для каждого ученика с строкой по 104/107 и для их
активных корней, в той же транзакции.

Запуск (из корня LMS):
  python scripts/tsk1202_move_dict_tasks.py
  DBCHECK_OK=1 python scripts/tsk1202_move_dict_tasks.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

logger = logging.getLogger("tsk1202")

SOURCE_COURSE_ID = 104
TARGET_COURSE_ID = 107
TASK_IDS: list[int] = [92, 94]


def _prod_async_dsn() -> str:
    """Боевой DSN из .mcp.json в форме для asyncpg. Пароль не печатается."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    dsn: str = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _snapshot(db: Any) -> dict[str, Any]:
    """Снимок: курс/позиция/уровень заданий, сдачи, целостность позиций, кеш по курсам."""
    from sqlalchemy import text

    tasks = {
        r[0]: (r[1], r[2], r[3])
        for r in (await db.execute(text(
            "SELECT id, course_id, order_position, requirement_level FROM tasks WHERE id = ANY(:ids)"
        ), {"ids": TASK_IDS})).all()
    }
    results = dict((await db.execute(text(
        "SELECT task_id, count(*) FROM task_results WHERE task_id = ANY(:ids) GROUP BY task_id"
    ), {"ids": TASK_IDS})).all())
    positions = {
        r[0]: (r[1], r[2], r[3])
        for r in (await db.execute(text(
            "SELECT course_id, count(*), max(order_position), count(DISTINCT order_position) "
            "FROM tasks WHERE course_id = ANY(:c) GROUP BY course_id"
        ), {"c": [SOURCE_COURSE_ID, TARGET_COURSE_ID]})).all()
    }
    states = {
        (r[0], r[1]): r[2]
        for r in (await db.execute(text(
            "SELECT course_id, state, count(*) FROM student_course_state "
            "WHERE course_id = ANY(:c) GROUP BY 1, 2"
        ), {"c": [SOURCE_COURSE_ID, TARGET_COURSE_ID]})).all()
    }
    return {"tasks": tasks, "results": results, "positions": positions, "states": states}


async def run(apply: bool) -> int:
    """Перенести задания и пересчитать кеш в одной транзакции.

    :returns: 0 — успех, 1 — отказ по сверке.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.learning_engine_service import LearningEngineService

    engine = create_async_engine(_prod_async_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    learning = LearningEngineService()
    print(f"Режим: {'ЗАПИСЬ' if apply else 'DRY-RUN'}")
    try:
        async with factory() as db:
            before = await _snapshot(db)
            print("ДО:", before)
            if any(before["tasks"][t][0] != SOURCE_COURSE_ID for t in TASK_IDS):
                print("ОТКАЗ: задания уже не в курсе 104")
                return 1

            for task_id in TASK_IDS:
                res = await db.execute(text(
                    "UPDATE tasks SET course_id = :dst, order_position = NULL, "
                    "requirement_level = 'required' WHERE id = :id AND course_id = :src"
                ), {"dst": TARGET_COURSE_ID, "src": SOURCE_COURSE_ID, "id": task_id})
                if res.rowcount != 1:
                    raise RuntimeError(f"задание {task_id}: обновлено {res.rowcount} строк")

            students = sorted(r[0] for r in (await db.execute(text(
                "SELECT DISTINCT student_id FROM student_course_state WHERE course_id = ANY(:c) "
                "UNION SELECT DISTINCT user_id FROM task_results WHERE task_id = ANY(:ids)"
            ), {"c": [SOURCE_COURSE_ID, TARGET_COURSE_ID], "ids": TASK_IDS})).all())
            for sid in students:
                nodes: set[int] = {SOURCE_COURSE_ID, TARGET_COURSE_ID}
                for node in (SOURCE_COURSE_ID, TARGET_COURSE_ID):
                    nodes.update(await learning.list_active_roots_of_node(db, sid, node))
                for course_id in sorted(nodes):
                    await learning.compute_course_state(db, sid, course_id, update_state_table=True)
            print(f"кеш пересчитан для {len(students)} учеников")

            after = await _snapshot(db)
            print("ПОСЛЕ:", after)
            ok = (
                all(after["tasks"][t][0] == TARGET_COURSE_ID and after["tasks"][t][2] == "required"
                    for t in TASK_IDS)
                and after["results"] == before["results"]
                and all(n == mx == d for n, mx, d in after["positions"].values())
                and after["positions"][SOURCE_COURSE_ID][0] == before["positions"][SOURCE_COURSE_ID][0] - 2
                and after["positions"][TARGET_COURSE_ID][0] == before["positions"][TARGET_COURSE_ID][0] + 2
            )
            if not ok:
                print("ОТКАЗ: проверка после не прошла — откат")
                await db.rollback()
                return 1
            if not apply:
                print("DRY-RUN: проверка пройдена, откат.")
                await db.rollback()
                return 0
            await db.commit()
            print("COMMIT")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Точка входа."""
    ap = argparse.ArgumentParser(description="tsk-1202: перенос 92/94 из 104 в 107")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию dry-run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
