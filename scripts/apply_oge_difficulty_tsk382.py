"""tsk-382 часть Г — сложность 105 заданий ОГЭ (курсы 1178-1181).

Шкала ОГЭ калибровалась с оператором отдельно от ЕГЭ и сдвинута относительно неё:
  - простой  — типовое решение в 1-3 действия;
  - средний  — типовое решение, но действий больше;
  - сложный  — уход от типового шаблона (примерно соответствует среднему ЕГЭ).

Оператор подтвердил базовый уровень по типу задания (2026-07-23):
  - задание 13 «презентация или текстовый документ» — средний: действий много,
    но все механические и шаблон один на все варианты;
  - задание 14 «обработка данных в таблице» — средний: две формулы с условием
    плюс построение круговой диаграммы, то есть три действия;
  - задание 15 «Робот в среде КуМир» — средний: цикл с проверками условий,
    алгоритм обязан работать при ЛЮБОМ расположении стен и проходов;
  - задание 16 «программа на анализ последовательности» — простой: цикл,
    условие, накопитель.

Курсы 1178-1180 однородны: внутри каждого меняется только тема (животное,
предмет тестирования, форма лабиринта), постановка одна. Поэтому им ставится
подтверждённый оператором уровень — канон 2.

В курсе 1181 однородности нет: 10 заданий из 30 выходят за базовый шаблон
(два условия сразу, неизвестная длина последовательности с признаком конца,
два выводимых значения, постановка «камера наблюдения» со значением и флагом).
Их повышение до среднего — оценка агента, канон 4.

Блок «Сложные» (1379-1403) курсов ОГЭ не касается: у ОГЭ своего блока нет,
уровень HARD живёт прямо в курсе, переносов не требуется.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/apply_oge_difficulty_tsk382.py
    PROD_DB_DSN=... python scripts/apply_oge_difficulty_tsk382.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

EASY, NORMAL = 2, 3
DECIDED_AT = "2026-07-23"

# Базовый уровень по типу задания — подтверждён оператором (канон 2).
BASELINE: dict[int, tuple[int, str]] = {
    1178: (NORMAL, "задание 13: действий много, но все механические — типовое решение"),
    1179: (NORMAL, "задание 14: две формулы с условием плюс круговая диаграмма — три действия"),
    1180: (NORMAL, "задание 15: цикл с проверками, алгоритм обязан работать при любом лабиринте"),
    1181: (EASY, "задание 16: цикл, условие, накопитель — типовое решение в 1-3 действия"),
}

# Отклонения от базы внутри курса 1181 — оценка агента (канон 4).
DEVIATIONS: dict[int, tuple[int, str]] = {}
for _task_id in (7241, 7246, 7247, 7248, 7250, 7251):
    DEVIATIONS[_task_id] = (NORMAL, "два условия сразу плюс неизвестная длина последовательности с признаком конца")
DEVIATIONS[7242] = (NORMAL, "надо отслеживать по два экстремума с обеих сторон и вывести два значения")
for _task_id in (7243, 7244, 7245):
    DEVIATIONS[_task_id] = (NORMAL, "постановка «камера наблюдения»: считается значение и отдельно выводится флаг по условию")


def _provenance(canon: int, evidence: str) -> str:
    """Обоснование уровня: канон 2 для подтверждённой базы, 4 для отклонений."""
    source = "оператор" if canon == 2 else "оценка агента"
    prefix = "подтверждённая оператором база шкалы ОГЭ" if canon == 2 else "отклонение от базы"
    return json.dumps(
        {"canon": canon, "source": source, "evidence": f"{prefix}: {evidence}",
         "decided_at": DECIDED_AT, "task": "tsk-382"},
        ensure_ascii=False,
    )


async def main(apply: bool) -> int:
    """Ставит уровень и обоснование 105 заданиям ОГЭ в одной транзакции."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-382 часть Г: задания ОГЭ, курсы {sorted(BASELINE)} — {mode} ===\n")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("""
            SELECT id, course_id, difficulty_id, difficulty_provenance
            FROM tasks
            WHERE is_active AND difficulty_provenance IS NULL
              AND external_uid ILIKE '%sdamgia%'
              AND course_id = ANY($1::int[])
            ORDER BY id
        """, sorted(BASELINE))
        if not rows:
            print("нечего обновлять — у всех заданий уже есть обоснование.")
            return 1

        plan: list[tuple[int, int, int, str]] = []  # (id, level, canon, evidence)
        for row in rows:
            if row["id"] in DEVIATIONS:
                level, evidence = DEVIATIONS[row["id"]]
                plan.append((row["id"], level, 4, evidence))
            else:
                level, evidence = BASELINE[row["course_id"]]
                plan.append((row["id"], level, 2, evidence))

        by_course: dict[int, int] = {}
        for row in rows:
            by_course[row["course_id"]] = by_course.get(row["course_id"], 0) + 1
        print(f"заданий без обоснования: {len(rows)} — по курсам {dict(sorted(by_course.items()))}")
        changes = sum(
            1 for (task_id, level, _c, _e), row in zip(plan, rows) if row["difficulty_id"] != level
        )
        canon2 = sum(1 for p in plan if p[2] == 2)
        print(f"меняют уровень: {changes}; подтверждают текущий: {len(plan) - changes}")
        print(f"канон 2 (подтверждённая база): {canon2}; канон 4 (отклонения агента): {len(plan) - canon2}\n")

        ids = [p[0] for p in plan]
        want = {p[0]: (p[1], p[2]) for p in plan}

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            for task_id, level, canon, evidence in plan:
                await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1, difficulty_provenance = $2::jsonb WHERE id = $3",
                    level, _provenance(canon, evidence), task_id,
                )

            courses = sorted(BASELINE)
            reorder = await conn.execute("""
                WITH new_order AS (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY course_id
                        ORDER BY difficulty_id ASC,
                            CASE task_content->>'type'
                                WHEN 'SC' THEN 1 WHEN 'MC' THEN 1
                                WHEN 'TA' THEN 2 WHEN 'SA' THEN 2
                                WHEN 'SA_COM' THEN 3 ELSE 99 END ASC,
                            order_position ASC NULLS LAST, id ASC
                    ) AS new_op
                    FROM tasks WHERE course_id = ANY($1::int[])
                )
                UPDATE tasks t SET order_position = n.new_op
                FROM new_order n WHERE t.id = n.id AND (t.order_position IS DISTINCT FROM n.new_op)
            """, courses)
            print(f"REORDER (курсы {courses}): {reorder}")

            after = await conn.fetch(
                "SELECT id, difficulty_id, difficulty_provenance FROM tasks WHERE id = ANY($1::int[])",
                ids,
            )
            bad: list[str] = []
            for row in after:
                level, canon = want[row["id"]]
                value = row["difficulty_provenance"]
                value = json.loads(value) if isinstance(value, str) else value
                if row["difficulty_id"] != level:
                    bad.append(f"id={row['id']}: уровень {row['difficulty_id']}, ожидали {level}")
                if not value or value.get("canon") != canon:
                    bad.append(f"id={row['id']}: канон {(value or {}).get('canon')}, ожидали {canon}")
            if len(after) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for line in bad[:20]:
                    print(f"  - {line}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after)}/{len(ids)} — OK")

            # Блок «Сложные» курсов ОГЭ не касается, но инвариант проверяем целиком.
            hard_in_base = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id = 4
                  AND course_id BETWEEN 138 AND 165
            """)
            soft_in_block = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id <> 4
                  AND course_id BETWEEN 1379 AND 1403
            """)
            if hard_in_base or soft_in_block:
                print(f"\nОШИБКА инварианта блока: {hard_in_base} / {soft_in_block} — ROLLBACK")
                await tx.rollback()
                return 1
            print("инварианты блока «Сложные» по всему проду — OK")

            dupes = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT course_id, order_position FROM tasks WHERE course_id = ANY($1::int[])
                    GROUP BY course_id, order_position HAVING COUNT(*) > 1) d
            """, courses)
            gaps = await conn.fetch("""
                SELECT course_id FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id
                HAVING MIN(order_position) <> 1 OR MAX(order_position) <> COUNT(*)
            """, courses)
            if dupes or gaps:
                print(f"\nОШИБКА порядка: коллизий {dupes}, курсов с дырами {len(gaps)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("order_position уникален и плотный 1..N — OK")

            if apply:
                await tx.commit()
                print("\nCOMMIT — изменения сохранены.")
            else:
                await tx.rollback()
                print("\nROLLBACK — dry-run, изменения откатаны.")
        except Exception:
            await tx.rollback()
            raise
    finally:
        await conn.close()

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply)))
