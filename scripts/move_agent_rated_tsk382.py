"""tsk-382 часть В — перенос заданий, чей уровень пересмотрен экспертной оценкой.

11 заданий лежали в блоке «Сложные» с `difficulty_id = 4`, у которого **не было
никакого обоснования**: канона 1-3 у них нет, HARD достался от импорта. Значит и
членство в блоке держалось на неподтверждённом значении.

Оценки сделаны по ПОЛНОМУ тексту условия (в отличие от первого прогона по
фрагментам — тот для этих заданий давал другой ответ, различающее условие
договаривается в конце):

  - 2327/2328: у исполнителя Цапля третья команда «Дуга r,a,b,α» — движение по
    дуге окружности. Это не шаблон задания 6, а свой разбор → остаются HARD.
  - 2382: диапазон [289123456; 389123456], перебор с факторизацией не проходит,
    нужен разбор структуры числа с ровно тремя нетривиальными делителями → HARD.
  - остальные 8 — стандартные приёмы своего номера, в блоке им не место.

Инвариант прода: `difficulty_id = 4` ⟺ курс из блока «Сложные» (1379-1403).
Поэтому у восьми меняются три поля разом: уровень, курс, уровень обязательности.

Последствие для учеников: 2291, 2329, 2330, 2349 решали ученики (по 3-4 попытки
каждое). Задания уходят из блока (`recommended`, вне зачёта) в обязательные
курсы — их работа начинает засчитываться. Результаты сохраняются: `task_results`
привязан к `task_id`, а не к курсу.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/move_agent_rated_tsk382.py
    PROD_DB_DSN=... python scripts/move_agent_rated_tsk382.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

BLOCK_MIN, BLOCK_MAX = 1379, 1403
BASE_MIN, BASE_MAX = 138, 165
HARD = 4
DECIDED_AT = "2026-07-23"

# (task_id, uid, курс «до», курс «после», уровень «после», req «после», обоснование)
MOVES: list[tuple[int, str, int, int, int, str, str]] = [
    (2291, "ext:d4:sdamgia:20260602:56505", 1383, 156, 3, "required",
     "подвох: чётность суммы цифр ДЕСЯТИЧНОЙ записи управляет дописыванием к ДВОИЧНОЙ, и так трижды"),
    (2329, "ext:d4:sdamgia:20260602:68239", 1384, 157, 3, "required",
     "площадь составной фигуры Черепахи с подбором минимального x — шаблон задания 6 с подвохом"),
    (2330, "ext:d4:sdamgia:20260602:55593", 1384, 157, 3, "required",
     "подсчёт узлов решётки внутри области — шаблон задания 6 с подвохом"),
    (2349, "ext:d4:sdamgia:20260602:72568", 1388, 141, 3, "required",
     "подвох: слова от А до Я с разбиением по дефису и без учёта регистра"),
    (2384, "ext:d4:sdamgia:20260602:58527", 1397, 147, 3, "required",
     "две кучи и диапазон S, выигрыш вторым ходом — усложнённый шаблон теории игр"),
    (2385, "ext:d4:sdamgia:20260602:47016", 1397, 147, 3, "required",
     "подвох: запрещено повторять собственный предыдущий ход, состояние включает последний ход"),
    (9521, "crylov:v11t22", 1398, 149, 3, "required",
     "минимальное время выполнения по графу зависимостей — стандартный приём задания 22"),
    (9532, "crylov:v16t22", 1398, 149, 3, "required",
     "минимальное время выполнения по графу зависимостей — стандартный приём задания 22"),
]

# Остаются на месте: меняется только обоснование, значение подтверждается.
CONFIRMED: list[tuple[int, str, int, str]] = [
    (2327, "ext:d4:sdamgia:20260602:58248", 1384,
     "исполнитель Цапля с командой «Дуга» — движение по дуге окружности, нужен свой разбор"),
    (2328, "ext:d4:sdamgia:20260602:58249", 1384,
     "исполнитель Цапля с командой «Дуга» — движение по дуге окружности, нужен свой разбор"),
    (2382, "ext:d4:sdamgia:20260602:33104", 1401,
     "диапазон в 100 млн, перебор с факторизацией не проходит — нужен разбор структуры числа"),
]

COURSES: list[int] = sorted(
    {m[2] for m in MOVES} | {m[3] for m in MOVES} | {c[2] for c in CONFIRMED}
)

ORDER_BY_EXPR = """
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
"""


def _provenance(evidence: str) -> str:
    """Обоснование канона 4 (оценка агента) в виде JSON."""
    return json.dumps(
        {"canon": 4, "source": "оценка агента", "evidence": evidence,
         "decided_at": DECIDED_AT, "task": "tsk-382"},
        ensure_ascii=False,
    )


async def main(apply: bool) -> int:
    """Переносит и подтверждает задания в одной транзакции."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-382: {len(MOVES)} переносов + {len(CONFIRMED)} подтверждений — {mode} ===\n")

    move_ids = [m[0] for m in MOVES]
    confirm_ids = [c[0] for c in CONFIRMED]
    all_ids = sorted(move_ids + confirm_ids)

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, requirement_level, "
            "difficulty_provenance FROM tasks WHERE id = ANY($1::int[])",
            all_ids,
        )
        by_id = {r["id"]: r for r in rows}
        problems: list[str] = []
        if len(rows) != len(all_ids):
            problems.append(f"найдено {len(rows)} заданий из {len(all_ids)}")

        for task_id, uid, course_before, _c2, _d, _r, _e in MOVES:
            row = by_id.get(task_id)
            if row is None:
                continue
            if row["external_uid"] != uid or row["course_id"] != course_before:
                problems.append(
                    f"id={task_id}: состояние «до» не совпадает "
                    f"(ожидали курс {course_before}/{uid}, факт {row['course_id']}/{row['external_uid']})"
                )
            if row["difficulty_id"] != HARD:
                problems.append(f"id={task_id}: уровень уже не HARD (факт {row['difficulty_id']})")
        for task_id, uid, course, _e in CONFIRMED:
            row = by_id.get(task_id)
            if row is None:
                continue
            if row["external_uid"] != uid or row["course_id"] != course or row["difficulty_id"] != HARD:
                problems.append(f"id={task_id}: состояние «до» не совпадает")

        for task_id, row in by_id.items():
            existing = row["difficulty_provenance"]
            existing = json.loads(existing) if isinstance(existing, str) else existing
            if existing is not None and int(existing.get("canon", 9)) < 4:
                problems.append(
                    f"id={task_id}: уже обосновано каноном {existing.get('canon')} — оценка агента его не перебивает"
                )

        if problems:
            print("ОШИБКА, обновление не выполняется:")
            for line in problems:
                print(f"  - {line}")
            return 1
        print("состояние «до» совпадает по всем 11 заданиям, сильного канона нет — OK")

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")

            for task_id, _uid, course_before, course_after, level, req, evidence in MOVES:
                result = await conn.execute(
                    "UPDATE tasks SET course_id=$1, difficulty_id=$2, requirement_level=$3, "
                    "difficulty_provenance=$4::jsonb WHERE id=$5 AND course_id=$6 AND difficulty_id=$7",
                    course_after, level, req, _provenance(evidence), task_id, course_before, HARD,
                )
                print(f"MOVE id={task_id}: курс {course_before} -> {course_after}, уровень HARD -> {level}: {result}")
            for task_id, _uid, _course, evidence in CONFIRMED:
                await conn.execute(
                    "UPDATE tasks SET difficulty_provenance=$1::jsonb WHERE id=$2",
                    _provenance(evidence), task_id,
                )
                print(f"CONFIRM id={task_id}: уровень HARD подтверждён, записано обоснование")

            reorder = await conn.execute(f"""
                WITH new_order AS (
                    SELECT id, ROW_NUMBER() OVER ({ORDER_BY_EXPR.strip()}) AS new_op
                    FROM tasks WHERE course_id = ANY($1::int[])
                )
                UPDATE tasks t SET order_position = n.new_op
                FROM new_order n WHERE t.id = n.id AND (t.order_position IS DISTINCT FROM n.new_op)
            """, COURSES)
            print(f"\nREORDER (курсы {COURSES}): {reorder}")

            after = await conn.fetch(
                "SELECT id, course_id, difficulty_id, requirement_level, difficulty_provenance "
                "FROM tasks WHERE id = ANY($1::int[])",
                all_ids,
            )
            want_move = {m[0]: (m[3], m[4], m[5]) for m in MOVES}
            want_confirm = {c[0]: c[2] for c in CONFIRMED}
            bad: list[str] = []
            for row in after:
                if row["id"] in want_move:
                    got = (row["course_id"], row["difficulty_id"], row["requirement_level"])
                    if got != want_move[row["id"]]:
                        bad.append(f"id={row['id']}: {got}, ожидали {want_move[row['id']]}")
                else:
                    if row["course_id"] != want_confirm[row["id"]] or row["difficulty_id"] != HARD:
                        bad.append(f"id={row['id']}: подтверждаемое задание изменилось")
                if row["difficulty_provenance"] is None:
                    bad.append(f"id={row['id']}: обоснование не записано")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for line in bad:
                    print(f"  - {line}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after)}/{len(all_ids)} — OK")

            hard_in_base = await conn.fetchval(f"""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id={HARD}
                  AND course_id BETWEEN {BASE_MIN} AND {BASE_MAX}
            """)
            soft_in_block = await conn.fetchval(f"""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id<>{HARD}
                  AND course_id BETWEEN {BLOCK_MIN} AND {BLOCK_MAX}
            """)
            required_in_block = await conn.fetchval(f"""
                SELECT count(*) FROM tasks WHERE is_active AND requirement_level<>'recommended'
                  AND course_id BETWEEN {BLOCK_MIN} AND {BLOCK_MAX}
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
