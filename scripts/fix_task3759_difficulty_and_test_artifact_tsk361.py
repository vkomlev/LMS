# -*- coding: utf-8 -*-
"""tsk-361: сложность задания 3759 (решение оператора) + деактивация тестового 1025.

ЧТО ДЕЛАЕТ (одна транзакция, три шага)
1. 3759 (курс 158, «Картинка занимает 840 бит…»): difficulty_id 4 (HARD) → 2 (EASY).
   Решение оператора 2026-07-22: задание элементарное (840 бит / 8 = 105 байт при
   1 байт/с). Ответ 105 записан отдельно, бэкфиллом solution_rules (tsk-100-механика).
2. Реордер order_position курса 158 — смена сложности меняет межгрупповые границы.
   Та же ROW_NUMBER-логика THEORY→EASY→NORMAL→HARD→PROJECT с тайбрейком по текущему
   order_position, что в scripts/reorder_courses_by_difficulty_tsk345.py. Триггер
   trg_set_task_order_position глушится session-variable app.skip_task_order_trigger
   (НЕ ALTER TABLE ... DISABLE TRIGGER — тот берёт ACCESS EXCLUSIVE лок на всю tasks).
3. 1025 (курс 3, external_uid y4ps5-task-…, stem «y4ps5-test», тип SC без вариантов
   ответа, max_score=10) — тестовый артефакт, не учебный контент: is_active=false.
   Не «чиним» правило проверки — задания не существует как учебной единицы.

КООРДИНАЦИЯ
Курс 158 параллельно пересортировывал чип tsk-354 (задание 2262 → HARD). Скрипт
проверяет 2262.difficulty_id = 4 ДО работы и падает, если чип ещё не закончил, —
иначе реордер закрепил бы промежуточное состояние.

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE-ы адресные (id = 3759 / id = 1025), реордер ограничен course_id = 158.
Обратимо: difficulty_id 2→4, is_active false→true, order_position — повторным
прогоном реордера.

Запуск: dry-run по умолчанию (транзакция откатывается);
  python scripts/fix_task3759_difficulty_and_test_artifact_tsk361.py
  DBCHECK_OK=1 python scripts/fix_task3759_difficulty_and_test_artifact_tsk361.py --apply
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

TASK_ID = 3759
COURSE_ID = 158
DIFFICULTY_EASY = 2
DIFFICULTY_HARD = 4
CONTROL_TASK_ID = 2262  # маркер завершения параллельного чипа tsk-354
TEST_ARTIFACT_ID = 1025

REORDER_SQL = """
WITH new_order AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY course_id
               ORDER BY
                   difficulty_id ASC,
                   CASE task_content->>'type'
                       WHEN 'SC' THEN 1
                       WHEN 'MC' THEN 1
                       WHEN 'TA' THEN 2
                       WHEN 'SA' THEN 2
                       WHEN 'SA_COM' THEN 3
                       ELSE 99
                   END ASC,
                   order_position ASC NULLS LAST,
                   id ASC
           ) AS new_op
    FROM tasks
    WHERE course_id = $1
)
UPDATE tasks t
SET order_position = n.new_op
FROM new_order n
WHERE t.id = n.id
  AND t.order_position IS DISTINCT FROM n.new_op
"""


def _dsn() -> str:
    """Прод-DSN для learn. Из окружения или из .mcp.json (learn_prod_db)."""
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
            if "5.42.107.253" in dsn:
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
            # ---- Шаг 0: предусловия ----
            control = await conn.fetchval(
                "SELECT difficulty_id FROM tasks WHERE id = $1", CONTROL_TASK_ID
            )
            if control != DIFFICULTY_HARD:
                raise AssertionError(
                    f"контроль tsk-354: задание {CONTROL_TASK_ID} difficulty_id={control}, "
                    f"ждали {DIFFICULTY_HARD} — параллельный чип ещё не закончил, реордер откладываю"
                )
            print(f"Контроль tsk-354: {CONTROL_TASK_ID}.difficulty_id = {control} — чип закончил, продолжаю")

            before = await conn.fetchrow(
                "SELECT difficulty_id, order_position, is_active FROM tasks WHERE id = $1", TASK_ID
            )
            if before is None:
                raise AssertionError(f"задание {TASK_ID} не найдено")
            print(f"ДО: {TASK_ID} difficulty_id={before['difficulty_id']} "
                  f"order_position={before['order_position']} is_active={before['is_active']}")

            art = await conn.fetchrow(
                "SELECT course_id, is_active, task_content->>'stem' AS stem, "
                "task_content->>'type' AS ttype FROM tasks WHERE id = $1", TEST_ARTIFACT_ID
            )
            if art is None:
                raise AssertionError(f"задание {TEST_ARTIFACT_ID} не найдено")
            if art["stem"] != "y4ps5-test":
                raise AssertionError(
                    f"задание {TEST_ARTIFACT_ID}: stem='{art['stem']}' — не тестовый артефакт, не трогаю"
                )
            print(f"ДО: {TEST_ARTIFACT_ID} курс={art['course_id']} тип={art['ttype']} "
                  f"stem='{art['stem']}' is_active={art['is_active']}")

            tasks_before = await conn.fetch(
                "SELECT id, difficulty_id, order_position FROM tasks "
                "WHERE course_id = $1 ORDER BY order_position", COURSE_ID
            )
            print(f"Курс {COURSE_ID}: заданий {len(tasks_before)}")

            # ---- Шаг 1: difficulty_id 3759 ----
            res = await conn.execute(
                "UPDATE tasks SET difficulty_id = $2 WHERE id = $1 AND difficulty_id IS DISTINCT FROM $2",
                TASK_ID, DIFFICULTY_EASY,
            )
            print(f"Шаг 1 (difficulty {TASK_ID} → EASY): {res}")

            # ---- Шаг 2: реордер курса 158 ----
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            res = await conn.execute(REORDER_SQL, COURSE_ID)
            moved = int(res.split()[-1])
            print(f"Шаг 2 (реордер курса {COURSE_ID}): переставлено строк = {moved}")

            # ---- Шаг 3: деактивация тестового артефакта ----
            res = await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = $1 AND is_active",
                TEST_ARTIFACT_ID,
            )
            print(f"Шаг 3 (деактивация {TEST_ARTIFACT_ID}): {res}")

            # ---- Верификация внутри транзакции ----
            after = await conn.fetchrow(
                "SELECT difficulty_id, order_position FROM tasks WHERE id = $1", TASK_ID
            )
            if after["difficulty_id"] != DIFFICULTY_EASY:
                raise AssertionError(f"{TASK_ID}: difficulty_id={after['difficulty_id']}, ждали {DIFFICULTY_EASY}")
            print(f"ПОСЛЕ: {TASK_ID} difficulty_id={after['difficulty_id']} "
                  f"order_position={before['order_position']} → {after['order_position']}")

            control_after = await conn.fetchval(
                "SELECT difficulty_id FROM tasks WHERE id = $1", CONTROL_TASK_ID
            )
            if control_after != DIFFICULTY_HARD:
                raise AssertionError(f"контроль {CONTROL_TASK_ID} изменился: {control_after}")

            dupes = await conn.fetchval(
                "SELECT count(*) FROM (SELECT order_position FROM tasks WHERE course_id = $1 "
                "GROUP BY order_position HAVING count(*) > 1) x", COURSE_ID
            )
            if dupes:
                raise AssertionError(f"коллизии order_position в курсе {COURSE_ID}: {dupes}")

            violations = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT difficulty_id,
                           LAG(difficulty_id) OVER (ORDER BY order_position ASC NULLS LAST) AS prev
                    FROM tasks WHERE course_id = $1
                ) x WHERE prev IS NOT NULL AND difficulty_id < prev
            """, COURSE_ID)
            if violations:
                raise AssertionError(f"межгрупповые нарушения порядка в курсе {COURSE_ID}: {violations}")

            gaps = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT order_position, ROW_NUMBER() OVER (ORDER BY order_position) AS rn
                    FROM tasks WHERE course_id = $1
                ) x WHERE order_position IS DISTINCT FROM rn
            """, COURSE_ID)
            if gaps:
                raise AssertionError(f"order_position не плотный 1..N в курсе {COURSE_ID}: {gaps} расхождений")
            print(f"Проверка курса {COURSE_ID}: 0 коллизий, 0 нарушений порядка, order_position плотный 1..N")

            art_after = await conn.fetchval(
                "SELECT is_active FROM tasks WHERE id = $1", TEST_ARTIFACT_ID
            )
            if art_after is not False:
                raise AssertionError(f"{TEST_ARTIFACT_ID}: is_active={art_after}, ждали false")

            outside = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id <> $1 AND id <> $2 AND NOT is_active "
                "AND course_id BETWEEN 138 AND 165", COURSE_ID, TEST_ARTIFACT_ID
            )
            print(f"Неактивных заданий в курсах 138-165 вне выборки: {outside} (справочно)")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="реально записать (нужен DBCHECK_OK=1)")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
