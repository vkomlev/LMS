# -*- coding: utf-8 -*-
"""tsk-524 follow-up: перенести материалы теории рекурсии из курса 104
«Функции в Python» в новый подкурс 1451 «Рекурсия в Python».

ЗАЧЕМ
Оператор решил (2026-08-02, в этой же сессии): раз практика рекурсии вынесена
в отдельный подкурс, теория (материал 222 «Рекурсия» + видео 533/534) должна
физически жить там же, а не в «Функциях». Ранее (при создании подкурса) был
сделан другой выбор — не дублировать теорию, а оставить короткий материал-мост
(id=3861 «От теории к практике: рекурсия») со ссылкой назад на курс 104. Этот
мост теперь неверен по факту (утверждает «теория осталась в Функциях») и
удаляется вместе с переносом.

ЧТО ПЕРЕНОСИТСЯ (course_id: 104 -> 1451)
- id=222 «Рекурсия» (text), было order_position=12
- id=533 «Рекурсия (основы)» (video), было order_position=15
- id=534 «Рекурсивные функции: практика» (video), было order_position=18
Новый порядок в course_id=1451: 222 -> 1, 533 -> 2, 534 -> 3 (перед 9 заданиями
37-45, у которых своя независимая нумерация order_position в таблице tasks).

ПРОГРЕСС УЧЕНИКОВ
Проверено read-only ДО переноса: ВСЕ строки student_material_progress по этим
трём материалам (14 на каждый) имеют source='manual_teacher' — органических
(source='system') прохождений нет ни одного. Перенос — это UPDATE course_id
у самого material, progress-строки завязаны на material_id и переезжают вместе
с материалом автоматически, их NULL-эффект: 14 учеников, у кого course_104
уже COMPLETED (все три материала были у них зачтены руками наравне с прочим),
после переноса теряют эти 3 материала И из числителя, И из знаменателя курса
104 одновременно — проверено ниже, что course_104 остаётся COMPLETED для всех
14. Материал 3861 удаляется (ON DELETE CASCADE снимет его 13 progress-строк,
все тоже manual_teacher, поставленные скриптом tsk524_grandfather_recursion.py
несколько часов назад в этой же сессии).

ТРИГГЕР ПОРЯДКА
Глушится `app.skip_material_order_trigger` на время транзакции (как в
tsk347_hard_subcourses.py для tasks) — иначе построчные UPDATE order_position
триггерили бы каскадный сдвиг соседей на каждый шаг. В конце — явный REORDER
(плотная нумерация 1..N по текущему order_position) для course_id=104 (закрыть
дыры 12/15/18) и course_id=1451 (нормализовать 1..3).

Запуск: dry-run по умолчанию;
  python scripts/tsk524_move_theory_materials.py
  DBCHECK_OK=1 python scripts/tsk524_move_theory_materials.py --apply
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

SRC_COURSE_ID = 104   # Функции в Python
DST_COURSE_ID = 1451  # Рекурсия в Python
BRIDGE_MATERIAL_ID = 3861  # "От теории к практике: рекурсия" — удаляется

# id -> целевой order_position в course_id=1451
MOVE_PLAN: dict[int, int] = {
    222: 1,  # Рекурсия (text)
    533: 2,  # Рекурсия (основы) (video)
    534: 3,  # Рекурсивные функции: практика (video)
}

async def _reorder_materials(conn: asyncpg.Connection, course_id: int) -> None:
    """Плотная перенумерация 1..N по текущему order_position (закрыть дыры).

    Не через self-referential UPDATE...FROM CTE с ROW_NUMBER(): Postgres роняет
    такой запрос на таблице с BEFORE ROW триггером (даже под WHEN-скипом)
    ошибкой ``TriggeredDataChangeViolationError`` ("tuple to be updated was
    already modified") — известное ограничение при повторном визите той же
    строки внутри одной команды. Простой цикл по строкам этого не задевает.
    """
    rows = await conn.fetch(
        "SELECT id, order_position FROM materials WHERE course_id = $1 "
        "ORDER BY order_position ASC NULLS LAST, id ASC",
        course_id,
    )
    for new_pos, row in enumerate(rows, start=1):
        if row["order_position"] != new_pos:
            await conn.execute(
                "UPDATE materials SET order_position = $1 WHERE id = $2",
                new_pos, row["id"],
            )


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (паттерн tsk-362/366/373)."""
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


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, title, order_position FROM materials "
            "WHERE id = ANY($1::int[]) ORDER BY id",
            list(MOVE_PLAN) + [BRIDGE_MATERIAL_ID],
        )
        organic_progress = await conn.fetchval(
            "SELECT count(*) FROM student_material_progress "
            "WHERE material_id = ANY($1::int[]) AND source <> 'manual_teacher'",
            list(MOVE_PLAN),
        )
        affected_students = await conn.fetch(
            "SELECT DISTINCT student_id FROM student_material_progress "
            "WHERE material_id = ANY($1::int[])",
            list(MOVE_PLAN),
        )
        student_ids = [int(r["student_id"]) for r in affected_students]

        print("=" * 78)
        print(f"tsk-524 · перенос теории рекурсии {SRC_COURSE_ID}->{DST_COURSE_ID} · "
              f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)
        for r in rows:
            print(f"  id={r['id']:>5}  course_id={r['course_id']:>5}  "
                  f"order={r['order_position']:>3}  «{r['title']}»")
        print(f"Органических (не manual_teacher) прохождений 222/533/534: {organic_progress} "
              f"(ожидание 0)")
        print(f"Учеников с прогрессом по этим материалам: {len(student_ids)} -> {student_ids}")

        if organic_progress != 0:
            raise RuntimeError(
                "Найдены органические прохождения — перенос без сверки с учеником небезопасен."
            )

        if not apply:
            print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")

            deleted = await conn.execute(
                "DELETE FROM materials WHERE id = $1", BRIDGE_MATERIAL_ID
            )
            print(f"\nМатериал-мост {BRIDGE_MATERIAL_ID}: {deleted}")

            for material_id, target_pos in MOVE_PLAN.items():
                await conn.execute(
                    "UPDATE materials SET course_id = $1, order_position = $2 WHERE id = $3",
                    DST_COURSE_ID, target_pos, material_id,
                )
            print(f"Перенесено материалов: {len(MOVE_PLAN)}")

            await _reorder_materials(conn, SRC_COURSE_ID)
            await _reorder_materials(conn, DST_COURSE_ID)

            await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'false', true)")

            # ── Верификация ДО COMMIT ──────────────────────────────────────
            print("\nВерификация в транзакции:")
            still_in_src = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE id = ANY($1::int[]) AND course_id = $2",
                list(MOVE_PLAN), SRC_COURSE_ID,
            )
            now_in_dst = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE id = ANY($1::int[]) AND course_id = $2",
                list(MOVE_PLAN), DST_COURSE_ID,
            )
            bridge_gone = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE id = $1", BRIDGE_MATERIAL_ID
            )
            bridge_progress_gone = await conn.fetchval(
                "SELECT count(*) FROM student_material_progress WHERE material_id = $1",
                BRIDGE_MATERIAL_ID,
            )
            progress_intact = await conn.fetchval(
                "SELECT count(*) FROM student_material_progress WHERE material_id = ANY($1::int[])",
                list(MOVE_PLAN),
            )
            dup_src = await conn.fetchval(
                "SELECT COALESCE(sum(c), 0) FROM ("
                "  SELECT count(*) - 1 AS c FROM materials WHERE course_id = $1 "
                "  AND order_position IS NOT NULL GROUP BY order_position HAVING count(*) > 1"
                ") x", SRC_COURSE_ID,
            )
            dup_dst = await conn.fetchval(
                "SELECT COALESCE(sum(c), 0) FROM ("
                "  SELECT count(*) - 1 AS c FROM materials WHERE course_id = $1 "
                "  AND order_position IS NOT NULL GROUP BY order_position HAVING count(*) > 1"
                ") x", DST_COURSE_ID,
            )
            print(f"  осталось в {SRC_COURSE_ID}: {still_in_src} (ожидание 0)")
            print(f"  теперь в {DST_COURSE_ID}: {now_in_dst} (ожидание {len(MOVE_PLAN)})")
            print(f"  мост {BRIDGE_MATERIAL_ID} удалён: материал={bridge_gone==0}, "
                  f"progress={bridge_progress_gone==0}")
            print(f"  progress по перенесённым материалам не тронут: {progress_intact} "
                  f"(ожидание {14*len(MOVE_PLAN)})")
            print(f"  коллизий order_position в {SRC_COURSE_ID}: {dup_src} (ожидание 0)")
            print(f"  коллизий order_position в {DST_COURSE_ID}: {dup_dst} (ожидание 0)")

            ok = (
                still_in_src == 0 and now_in_dst == len(MOVE_PLAN)
                and bridge_gone == 0 and bridge_progress_gone == 0
                and progress_intact == 14 * len(MOVE_PLAN)
                and dup_src == 0 and dup_dst == 0
            )
            if not ok:
                raise RuntimeError("Верификация не сошлась — ROLLBACK.")

        print(f"\nCOMMIT выполнен. Пересчёт course_state для {len(student_ids)} затронутых "
              "учеников — отдельным шагом (learning_engine_service, ORM-сессия).")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-524: перенос теории рекурсии в подкурс 1451")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
