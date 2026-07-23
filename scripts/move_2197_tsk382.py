"""tsk-382 часть В — перенос 2197 из блока «Сложные» в базовый курс.

Задание `ext:d4:kompege:20260602:23207` (задание 25) лежало в курсе 1401
«Задание 25. Сложные» с `difficulty_id = 4`, у которого не было обоснования:
HARD достался от импорта, значит и попадание в блок держалось на
неподтверждённом значении.

Два независимых источника сошлись на уровне «средний»:
  - kompege, публичный API: `difficulty = 1` = «Средний» (канон 3);
  - оценка агента по калиброванной шкале ЕГЭ: приём для задания 25 стандартный
    (перебор плюс разложение на множители), два дополнительных условия — ровно
    два простых множителя и ровно одна цифра 5 в записи каждого — подвох, но не
    новый алгоритм; перебор к тому же короткий, нужны первые 5 найденных чисел.

Именно совпадение двух источников снимает прежнее возражение: раньше перенос
опирался бы на один слабейший канон. Для сравнения, соседнее 2382 оставлено
HARD — там перебирается весь диапазон в 100 млн и прямая факторизация не
проходит, нужен разбор структуры числа.

Инвариант прода: `difficulty_id = 4` ⟺ курс из блока «Сложные» (1379-1403),
поэтому меняются три поля разом: уровень, курс, уровень обязательности.
Результатов учеников у задания нет — прогресс правка не задевает.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/move_2197_tsk382.py
    PROD_DB_DSN=... python scripts/move_2197_tsk382.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

TASK_ID = 2197
UID = "ext:d4:kompege:20260602:23207"
COURSE_FROM, COURSE_TO = 1401, 152
LEVEL_FROM, LEVEL_TO = 4, 3
COURSES = [COURSE_FROM, COURSE_TO]

PROVENANCE = {
    "canon": 3,
    "source": "kompege",
    "evidence": (
        "API difficulty=1 (средняя); независимо подтверждено оценкой агента по "
        "калиброванной шкале — стандартный приём задания 25 с двумя доп. условиями, "
        "перебор короткий (нужны первые 5 найденных)"
    ),
    "decided_at": "2026-07-23",
    "task": "tsk-382",
}


async def main(apply: bool) -> int:
    """Переносит задание в одной транзакции с построчной верификацией."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-382: перенос задания {TASK_ID} — {mode} ===\n")

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, external_uid, course_id, difficulty_id, requirement_level, "
            "difficulty_provenance, is_active FROM tasks WHERE id = $1",
            TASK_ID,
        )
        if row is None:
            print(f"ОШИБКА: задание {TASK_ID} не найдено.")
            return 1
        print(f"BEFORE: {dict(row)}")

        problems: list[str] = []
        if row["external_uid"] != UID:
            problems.append(f"uid не совпадает (факт {row['external_uid']})")
        if row["course_id"] != COURSE_FROM or row["difficulty_id"] != LEVEL_FROM:
            problems.append(
                f"состояние «до» не совпадает (ожидали курс {COURSE_FROM}/уровень {LEVEL_FROM})"
            )
        if not row["is_active"]:
            problems.append("задание неактивно")
        if row["difficulty_provenance"] is not None:
            problems.append("у задания уже есть обоснование — проверь, не перебивает ли оно канон 3")
        results = await conn.fetchval(
            "SELECT count(*) FROM task_results WHERE task_id = $1", TASK_ID
        )
        print(f"результатов учеников: {results}")
        if problems:
            print("\nОШИБКА, обновление не выполняется:")
            for line in problems:
                print(f"  - {line}")
            return 1

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            result = await conn.execute(
                "UPDATE tasks SET course_id=$1, difficulty_id=$2, requirement_level='required', "
                "difficulty_provenance=$3::jsonb "
                "WHERE id=$4 AND course_id=$5 AND difficulty_id=$6",
                COURSE_TO, LEVEL_TO, json.dumps(PROVENANCE, ensure_ascii=False),
                TASK_ID, COURSE_FROM, LEVEL_FROM,
            )
            print(f"\nMOVE: курс {COURSE_FROM} -> {COURSE_TO}, уровень {LEVEL_FROM} -> {LEVEL_TO}: {result}")

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
            """, COURSES)
            print(f"REORDER (курсы {COURSES}): {reorder}")

            after = await conn.fetchrow(
                "SELECT course_id, difficulty_id, requirement_level, difficulty_provenance, order_position "
                "FROM tasks WHERE id = $1", TASK_ID,
            )
            value = after["difficulty_provenance"]
            value = json.loads(value) if isinstance(value, str) else value
            print(f"AFTER: {dict(after)}")
            if (after["course_id"], after["difficulty_id"], after["requirement_level"]) != (
                COURSE_TO, LEVEL_TO, "required"
            ) or value != PROVENANCE:
                print("\nОШИБКА построчной верификации — ROLLBACK")
                await tx.rollback()
                return 1
            print("построчная верификация — OK")

            hard_in_base = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id = 4
                  AND course_id BETWEEN 138 AND 165
            """)
            soft_in_block = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id <> 4
                  AND course_id BETWEEN 1379 AND 1403
            """)
            required_in_block = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND requirement_level <> 'recommended'
                  AND course_id BETWEEN 1379 AND 1403
            """)
            if hard_in_base or soft_in_block or required_in_block:
                print(
                    f"\nОШИБКА инварианта блока: HARD в базовых {hard_in_base}, "
                    f"не-HARD в блоке {soft_in_block}, обязательных в блоке {required_in_block} — ROLLBACK"
                )
                await tx.rollback()
                return 1
            print("инварианты блока «Сложные» по всему проду — OK (0 нарушений)")

            dupes = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT course_id, order_position FROM tasks WHERE course_id = ANY($1::int[])
                    GROUP BY course_id, order_position HAVING COUNT(*) > 1) d
            """, COURSES)
            gaps = await conn.fetch("""
                SELECT course_id FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id
                HAVING MIN(order_position) <> 1 OR MAX(order_position) <> COUNT(*)
            """, COURSES)
            if dupes or gaps:
                print(f"\nОШИБКА порядка: коллизий {dupes}, курсов с дырами {len(gaps)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("order_position уникален и плотный 1..N — OK")

            left = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_provenance IS NULL
                  AND (external_uid ILIKE '%sdamgia%' OR external_uid ILIKE '%crylov%'
                       OR external_uid ILIKE '%kompege%' OR external_uid ILIKE '%polyakov%'
                       OR external_uid ILIKE '%yandex%')
            """)
            print(f"заданий внешних источников без обоснования осталось: {left}")

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
