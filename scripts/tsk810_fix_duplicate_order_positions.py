"""tsk-810: развести дубликаты `tasks.order_position` в курсах 146, 147 и 1397.

Что за дефект. На каждой занятой позиции стоят два задания (в 147 на позиции
17 — три): одно активное и один-два архивных импортных шаблона
(`pdf:d4:pdf:crylov:*`, `tg:ege:*`, `ext:calib:*`, `wp_nav:*`). След массового
импорта, а не триггера `set_task_order_position` (см. tsk-802, где эта
версия была отвергнута) — архивные строки приезжали пачкой и получали
позиции, уже занятые действующими заданиями.

Почему ученик этого не видит. Learning Engine и кабинет показывают только
активные задания, а у активных позиции уникальны и плотны: 1..29 в курсе 146,
1..28 в курсе 147, 1..38 в курсе 1397. Дубликаты видны лишь в общем списке
методиста, где архив соседствует с действующими заданиями.

Отсюда способ починки: **активные задания не трогаем вовсе**. Их номера уже
правильные, и любая их правка — это риск переставить курс людям ради
косметики инварианта. Архивные получают номера в хвосте, начиная с
`MAX(позиция активных) + 1`, с сохранением их взаимного порядка
(`order_position NULLS LAST, id` — то же правило, по которому нумерует
`reorder_tasks_after_delete`). Результат: дублей нет, дыр нет, порядок для
ученика не изменился ни на шаг.

Триггер `trg_set_task_order_position` на время правки глушится штатным
рубильником `app.skip_task_order_trigger`: он сам двигает соседей на каждый
UPDATE и, работая поверх массовой перенумерации, размножил бы сдвиги.

Запуск (из D:\\Work\\LMS):
    python scripts/tsk810_fix_duplicate_order_positions.py            # только показать план
    DBCHECK_OK=1 python scripts/tsk810_fix_duplicate_order_positions.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk810")

# Повторный прогон по уже вычищенному курсу безвреден: план окажется пустым,
# потому что архивные задания уже стоят там, куда их и надо поставить.
COURSE_IDS: tuple[int, ...] = (146, 147, 1397)
ACTOR = "script:tsk810_fix_duplicate_order_positions.py"


def _prod_dsn() -> str:
    """DSN боевой БД из .mcp.json (тот же, что у read-only проверок)."""
    cfg = json.loads(Path("D:/Work/LMS/.mcp.json").read_text(encoding="utf-8"))
    dsn: str = cfg["mcpServers"]["learn_prod_db"]["args"][-1]
    return dsn.replace("postgresql://", "postgresql+asyncpg://")


async def _snapshot(conn: AsyncConnection, course_id: int) -> dict[str, object]:
    """Снять состояние курса: сколько заданий, дублей, дыр, каков хвост активных."""
    row = (
        await conn.execute(
            text(
                """
                SELECT count(*) AS vsego,
                       count(*) FILTER (WHERE is_active) AS aktivnyh,
                       count(*) FILTER (WHERE order_position IS NULL) AS bez_pozitsii,
                       max(order_position) FILTER (WHERE is_active) AS max_aktivnoy
                FROM tasks WHERE course_id = :c
                """
            ),
            {"c": course_id},
        )
    ).first()
    dupes = (
        await conn.execute(
            text(
                """
                SELECT count(*) FROM (
                    SELECT order_position FROM tasks
                    WHERE course_id = :c AND order_position IS NOT NULL
                    GROUP BY order_position HAVING count(*) > 1
                ) d
                """
            ),
            {"c": course_id},
        )
    ).scalar()
    active_dupes = (
        await conn.execute(
            text(
                """
                SELECT count(*) FROM (
                    SELECT order_position FROM tasks
                    WHERE course_id = :c AND order_position IS NOT NULL AND is_active
                    GROUP BY order_position HAVING count(*) > 1
                ) d
                """
            ),
            {"c": course_id},
        )
    ).scalar()
    return {
        "всего заданий": row.vsego,
        "активных": row.aktivnyh,
        "без позиции": row.bez_pozitsii,
        "последняя позиция активного": row.max_aktivnoy,
        "позиций с дублями": dupes,
        "из них среди активных": active_dupes,
    }


async def _active_positions(conn: AsyncConnection, course_id: int) -> list[tuple[int, int]]:
    """`[(id задания, позиция), …]` для активных — контрольный слепок «до/после»."""
    rows = (
        await conn.execute(
            text(
                "SELECT id, order_position FROM tasks "
                "WHERE course_id = :c AND is_active "
                "ORDER BY order_position NULLS LAST, id"
            ),
            {"c": course_id},
        )
    ).all()
    return [(int(r[0]), r[1]) for r in rows]


async def _plan(conn: AsyncConnection, course_id: int) -> list[tuple[int, int | None, int]]:
    """Что получат архивные задания: `[(id, было, станет), …]`, только реальные сдвиги."""
    rows = (
        await conn.execute(
            text(
                """
                WITH tail AS (
                    SELECT COALESCE(MAX(order_position), 0) AS start
                    FROM tasks WHERE course_id = :c AND is_active
                ),
                numbered AS (
                    SELECT id, order_position,
                           (SELECT start FROM tail) + ROW_NUMBER() OVER (
                               ORDER BY order_position NULLS LAST, id
                           ) AS new_pos
                    FROM tasks
                    WHERE course_id = :c AND NOT is_active
                )
                SELECT id, order_position, new_pos FROM numbered
                WHERE order_position IS DISTINCT FROM new_pos
                ORDER BY new_pos
                """
            ),
            {"c": course_id},
        )
    ).all()
    return [(int(r[0]), r[1], int(r[2])) for r in rows]


async def _apply(conn: AsyncConnection, course_id: int) -> int:
    """Перенумеровать архивные задания курса. Возвращает число изменённых строк."""
    result = await conn.execute(
        text(
            """
            WITH tail AS (
                SELECT COALESCE(MAX(order_position), 0) AS start
                FROM tasks WHERE course_id = :c AND is_active
            ),
            numbered AS (
                SELECT id,
                       (SELECT start FROM tail) + ROW_NUMBER() OVER (
                           ORDER BY order_position NULLS LAST, id
                       ) AS new_pos
                FROM tasks
                WHERE course_id = :c AND NOT is_active
            )
            UPDATE tasks t
            SET order_position = n.new_pos
            FROM numbered n
            WHERE t.id = n.id
              AND t.order_position IS DISTINCT FROM n.new_pos
            """
        ),
        {"c": course_id},
    )
    return result.rowcount


def _print_snapshot(title: str, snap: dict[str, object]) -> None:
    logger.info("%s", title)
    for key, value in snap.items():
        logger.info("    %s: %s", key, value)


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="выполнить правку (без флага — только показать план)",
    )
    args = parser.parse_args(argv)

    engine = create_async_engine(_prod_dsn())
    try:
        # --- Шаг 1: состояние ДО ---
        before: dict[int, dict[str, object]] = {}
        before_active: dict[int, list[tuple[int, int]]] = {}
        plans: dict[int, list[tuple[int, int | None, int]]] = {}
        async with engine.connect() as conn:
            for course_id in COURSE_IDS:
                before[course_id] = await _snapshot(conn, course_id)
                before_active[course_id] = await _active_positions(conn, course_id)
                plans[course_id] = await _plan(conn, course_id)
                _print_snapshot(f"=== Курс {course_id}: состояние ДО ===", before[course_id])

        # --- Шаг 2: план ---
        for course_id in COURSE_IDS:
            plan = plans[course_id]
            logger.info("")
            logger.info(
                "=== Курс %s: план — переставить архивных заданий: %s ===",
                course_id,
                len(plan),
            )
            for task_id, old_pos, new_pos in plan[:5]:
                logger.info("    задание %s: %s -> %s", task_id, old_pos, new_pos)
            if len(plan) > 5:
                logger.info("    … и ещё %s строк", len(plan) - 5)
            logger.info("    активные задания не затрагиваются ни одной строкой плана")

        if not args.apply:
            logger.info("")
            logger.info("Режим плана: ничего не изменено. Для правки — флаг --apply.")
            return 0

        # --- Шаг 3: правка в транзакции ---
        logger.info("")
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.audit_actor', :a, true)"), {"a": ACTOR}
            )
            # Триггер порядка двигает соседей на каждый UPDATE — поверх массовой
            # перенумерации он размножил бы сдвиги. Глушим штатным рубильником.
            await conn.execute(
                text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            )
            for course_id in COURSE_IDS:
                changed = await _apply(conn, course_id)
                logger.info("курс %s: изменено строк — %s", course_id, changed)
            await conn.execute(
                text("SELECT set_config('app.skip_task_order_trigger', 'false', true)")
            )

        # --- Шаг 4: верификация ---
        logger.info("")
        ok = True
        async with engine.connect() as conn:
            for course_id in COURSE_IDS:
                snap = await _snapshot(conn, course_id)
                _print_snapshot(f"=== Курс {course_id}: состояние ПОСЛЕ ===", snap)
                if snap["позиций с дублями"] != 0:
                    logger.error("    ПРОВАЛ: дубликаты остались")
                    ok = False
                after_active = await _active_positions(conn, course_id)
                if after_active != before_active[course_id]:
                    logger.error("    ПРОВАЛ: позиции активных заданий изменились")
                    ok = False
                else:
                    logger.info(
                        "    позиции активных заданий не изменились: %s строк совпали",
                        len(after_active),
                    )
                holes = (
                    await conn.execute(
                        text(
                            """
                            SELECT count(*) FROM generate_series(1, (
                                SELECT count(*) FROM tasks
                                WHERE course_id = :c AND order_position IS NOT NULL
                            )) g
                            WHERE NOT EXISTS (
                                SELECT 1 FROM tasks
                                WHERE course_id = :c AND order_position = g
                            )
                            """
                        ),
                        {"c": course_id},
                    )
                ).scalar()
                logger.info("    дыр в нумерации: %s", holes)
        return 0 if ok else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
