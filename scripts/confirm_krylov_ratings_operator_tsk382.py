"""tsk-382 часть А — вердикт оператора по 11 заданиям Крылова без канона.

У этих заданий не нашлось ни разметки автора в ТГ-разборах (поиск по тексту без
ключа — `scripts/tsk382_find_missed_posts.py`), ни оценки внешнего сайта: книга
сайтом не представлена. Единственным обоснованием оставалась оценка агента
(канон 4, самый слабый).

Оператор просмотрел оценки и подтвердил их целиком (2026-07-23). Значения уровня
НЕ меняются — меняется только происхождение: канон 4 «оценка агента» →
канон 2 «ручной вердикт оператора», который по иерархии считается истиной и
пересмотру не подлежит. Формулировка обоснования агента сохраняется внутри
записи, чтобы было видно, ЧТО именно подтверждено.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/confirm_krylov_ratings_operator_tsk382.py
    PROD_DB_DSN=... python scripts/confirm_krylov_ratings_operator_tsk382.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

DECIDED_AT = "2026-07-23"

# (task_id, uid, ожидаемый уровень, что именно подтверждено)
CONFIRMED: list[tuple[int, str, int, str]] = [
    (9498, "crylov:v5t1", 2, "типовое сопоставление графа и таблицы, спрашиваются конкретные пункты"),
    (9516, "crylov:v11t10", 2, "типовой поиск сочетания букв в тексте"),
    (9555, "crylov:v11t1", 3, "ответ неоднозначен — какие номера МОГУТ соответствовать пунктам"),
    (9525, "crylov:v16t4", 3, "коды четырёх букв надо достроить самому, затем посчитать длину слова"),
    (9528, "crylov:v16t8", 3, "первое слово при трёх условиях сразу"),
    (9484, "crylov:v1t12", 3, "моделирование машины Тьюринга"),
    (9530, "crylov:v16t12", 3, "моделирование машины Тьюринга"),
    (9521, "crylov:v11t22", 3, "граф зависимостей процессов — стандартный приём задания 22"),
    (9532, "crylov:v16t22", 3, "граф зависимостей процессов — стандартный приём задания 22"),
    (9512, "crylov:v5t27", 4, "кластеризация точек — нужен свой алгоритм"),
    (9523, "crylov:v11t27", 4, "кластеризация точек — нужен свой алгоритм"),
]


def _provenance(what: str) -> str:
    """Обоснование канона 2 с сохранением того, что именно подтверждено."""
    return json.dumps(
        {"canon": 2, "source": "оператор",
         "evidence": f"вердикт оператора: подтверждена оценка — {what}",
         "decided_at": DECIDED_AT, "task": "tsk-382"},
        ensure_ascii=False,
    )


async def main(apply: bool) -> int:
    """Меняет только происхождение; уровень обязан остаться прежним."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-382 часть А: вердикт оператора по {len(CONFIRMED)} заданиям — {mode} ===\n")

    ids = [c[0] for c in CONFIRMED]
    want_level = {c[0]: c[2] for c in CONFIRMED}

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, difficulty_id, difficulty_provenance, is_active "
            "FROM tasks WHERE id = ANY($1::int[])",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        problems: list[str] = []
        if len(rows) != len(ids):
            problems.append(f"найдено {len(rows)} заданий из {len(ids)}")
        for task_id, uid, level, _what in CONFIRMED:
            row = by_id.get(task_id)
            if row is None:
                continue
            if row["external_uid"] != uid:
                problems.append(f"id={task_id}: uid не совпадает (факт {row['external_uid']})")
            if row["difficulty_id"] != level:
                problems.append(
                    f"id={task_id}: уровень уже не {level} (факт {row['difficulty_id']}) — "
                    f"подтверждать нечего, СТОП"
                )
            if not row["is_active"]:
                problems.append(f"id={task_id}: задание неактивно")
        if problems:
            print("ОШИБКА, обновление не выполняется:")
            for line in problems:
                print(f"  - {line}")
            return 1
        print("уровни совпадают с подтверждаемыми по всем 11 заданиям — OK")

        tx = conn.transaction()
        await tx.start()
        try:
            for task_id, _uid, _level, what in CONFIRMED:
                await conn.execute(
                    "UPDATE tasks SET difficulty_provenance = $1::jsonb WHERE id = $2",
                    _provenance(what), task_id,
                )

            after = await conn.fetch(
                "SELECT id, difficulty_id, difficulty_provenance FROM tasks WHERE id = ANY($1::int[])",
                ids,
            )
            bad: list[str] = []
            for row in after:
                value = row["difficulty_provenance"]
                value = json.loads(value) if isinstance(value, str) else value
                if row["difficulty_id"] != want_level[row["id"]]:
                    bad.append(f"id={row['id']}: уровень изменился — этого быть не должно")
                if not value or value.get("canon") != 2 or value.get("source") != "оператор":
                    bad.append(f"id={row['id']}: обоснование записано неверно")
            if len(after) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for line in bad:
                    print(f"  - {line}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after)}/{len(ids)} — уровни целы, канон 2 записан")

            left = await conn.fetchval("""
                SELECT count(*) FROM tasks
                WHERE is_active AND external_uid LIKE '%crylov%' AND difficulty_provenance IS NULL
            """)
            print(f"заданий партии Крылова без обоснования осталось: {left}")

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
