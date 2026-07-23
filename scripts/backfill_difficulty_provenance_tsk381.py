"""tsk-381 — заполнение `tasks.difficulty_provenance` по разобранным партиям.

Происхождение собирается из артефактов, а не выводится заново: планы и отчёты,
на основании которых значения уже применены, лежат в `reviews/tsk381/`.

Каноны (решение оператора 2026-07-23):
  1 — авторская разметка «Уровень …» в ТГ-разборах канала @cyberguru_ege;
  2 — ручной вердикт оператора (tsk-354, tsk-355, tsk-361) — истина;
  3 — оценка внешнего сайта (kompege, публичный API `difficulty`).
NULL остаётся у заданий, чьё значение не подтверждено ничем, — «неизвестно»
честнее правдоподобной выдумки.

Записывается ТОЛЬКО колонка `difficulty_provenance`; сами уровни не трогаются.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/backfill_difficulty_provenance_tsk381.py
    PROD_DB_DSN=... python scripts/backfill_difficulty_provenance_tsk381.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

_LMS_ROOT = Path(__file__).resolve().parent.parent
_REVIEWS = _LMS_ROOT / "reviews" / "tsk381"

DECIDED_AT = "2026-07-23"
TG_SOURCE = "tg:cyberguru_ege"

# Канон 2 — ручные вердикты оператора. Значение = задача, где вердикт вынесен.
MANUAL_VERDICT: dict[int, str] = {}
for _task_id in (
    9478, 9564, 9485, 4550, 9482, 9486, 9492, 9494, 9510, 9495, 9489, 9562, 9518,
    9519, 9520, 9493, 9479, 9503, 9556, 4561, 9490, 9491, 9505, 9506, 9507, 9563,
    9531, 9488, 9499, 9502, 9504, 9508, 9509, 9511, 9513, 9514, 9522, 9561, 9497,
    9500, 9501, 4579,
):
    MANUAL_VERDICT[_task_id] = "tsk-355"
for _task_id in (2059, 2116, 2262, 2352, 2386, 2720, 3792, 3796, 3477, 3794):
    MANUAL_VERDICT[_task_id] = "tsk-354"
MANUAL_VERDICT[3759] = "tsk-361"


def _entry(canon: int, source: str, evidence: str, task: str) -> dict[str, Any]:
    """Значение колонки происхождения."""
    return {
        "canon": canon, "source": source, "evidence": evidence,
        "decided_at": DECIDED_AT, "task": task,
    }


LEVEL_TO_ID = {"простой": 2, "лёгкий": 2, "легкий": 2, "средний": 3, "сложный": 4}
KOMPEGE_TO_ID = {0: 2, 1: 3, 2: 4, 3: 4}


def collect() -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    """task_id → (происхождение, подразумеваемый каноном уровень).

    Канон 2 перекрывает 1, канон 1 перекрывает 3. Подразумеваемый уровень
    нужен, чтобы не записать обоснование заданию, у которого стоит ДРУГОЕ
    значение: такое поле выглядит достоверно и при этом врёт — вред больше,
    чем от пустого происхождения.
    """
    provenance: dict[int, dict[str, Any]] = {}
    implied: dict[int, int] = {}

    # --- Канон 3: оценка kompege (слабейший, кладём первым) ---
    kompege = json.loads((_REVIEWS / "kompege-plan-2026-07-23.json").read_text(encoding="utf-8"))
    for row in kompege["plan"]:
        if row.get("kompege_difficulty") is None:
            continue
        provenance[row["id"]] = _entry(
            3, "kompege",
            f"API difficulty={row['kompege_difficulty']} ({row['kompege_label']})",
            "tsk-381",
        )
        implied[row["id"]] = KOMPEGE_TO_ID[row["kompege_difficulty"]]

    # --- Канон 1: ТГ-разборы ---
    # kompege-партия: у части заданий канон 1 перекрывает оценку сайта.
    for row in kompege["plan"]:
        canon = row.get("canon") or ""
        if canon.startswith("1 (ТГ-разбор)") and "конфликт" not in canon:
            provenance[row["id"]] = _entry(1, TG_SOURCE, row["evidence"], "tsk-381")
            implied[row["id"]] = row["decided_difficulty_id"]

    # sdamgia / Поляков / Яндекс.
    tg_plan = json.loads((_REVIEWS / "tg-canon-plan-2026-07-23.json").read_text(encoding="utf-8"))
    for row in tg_plan["plan"]:
        if row.get("decided_difficulty_id") is None:
            continue
        provenance[row["id"]] = _entry(1, TG_SOURCE, row["evidence"], "tsk-381")
        implied[row["id"]] = row["decided_difficulty_id"]

    # Партия Крылова: канон — сам пост канала (не строка в тексте условия).
    arbiter = json.loads((_REVIEWS / "arbiter-tg-canon-2026-07-23.json").read_text(encoding="utf-8"))
    for row in arbiter["rows"]:
        if not row.get("tg_level") or not row.get("tg_post_ids"):
            continue
        provenance[row["id"]] = _entry(
            1, TG_SOURCE,
            f"посты {','.join(row['tg_post_ids'])}: {row['tg_level']}",
            "tsk-381",
        )
        implied[row["id"]] = LEVEL_TO_ID[row["tg_level"]]
    # Три задания, чьё совпадение текста с постом подтверждено вручную
    # (похожесть ниже автоматического порога, тексты сверены построчно).
    for task_id, post, level in ((9496, "744", "средний"), (9517, "1039", "простой"), (9554, "802", "средний")):
        provenance[task_id] = _entry(
            1, TG_SOURCE, f"пост {post}: {level} (текст сверен вручную)", "tsk-381"
        )
        implied[task_id] = LEVEL_TO_ID[level]

    # --- Канон 2: ручные вердикты оператора (перекрывают всё) ---
    # Вердикт оператора обосновывает то значение, которое стоит сейчас, каким бы
    # оно ни было, — подразумеваемого уровня у него нет.
    for task_id, task in MANUAL_VERDICT.items():
        provenance[task_id] = _entry(2, "оператор", f"ручной вердикт, {task}", task)
        implied.pop(task_id, None)

    return provenance, implied


async def main(apply: bool) -> int:
    """Пишет происхождение в одной транзакции с построчной верификацией."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    provenance, implied = collect()
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    conn_probe = await asyncpg.connect(dsn)
    try:
        # Партия `tg:ege` — сами разборы оператора: текст задания и есть текст
        # поста, метка «Уровень …» в нём — авторская. Проверено: у всех 177
        # заданий с меткой поле совпадает с ней, поэтому канон 1 для них
        # берётся прямо из текста (в отличие от партии Крылова, где шапку
        # переписали при импорте и она посту противоречит).
        tg_ege = await conn_probe.fetch(
            r"""
            WITH b AS (
              SELECT id, difficulty_id,
                lower((regexp_match(
                  lower(regexp_replace(coalesce(task_content->>'stem',''), '<[^>]+>', ' ', 'g')),
                  'уровень[^а-яё]{0,5}(простой|лёгкий|легкий|средний|сложный)'))[1]) AS lvl
              FROM tasks WHERE is_active AND external_uid LIKE 'tg:ege:%'
            )
            SELECT id, lvl FROM b
            WHERE lvl IS NOT NULL AND difficulty_id = CASE lvl
                WHEN 'простой' THEN 2 WHEN 'лёгкий' THEN 2 WHEN 'легкий' THEN 2
                WHEN 'средний' THEN 3 WHEN 'сложный' THEN 4 END
            """
        )
        for row in tg_ege:
            if row["id"] in MANUAL_VERDICT:
                continue
            provenance[row["id"]] = _entry(
                1, TG_SOURCE, f"разбор оператора, метка в тексте: {row['lvl']}", "tsk-381"
            )
    finally:
        await conn_probe.close()

    print(f"=== tsk-381 провенанс: {len(provenance)} заданий — {mode} ===\n")

    by_canon: dict[int, int] = {}
    for entry in provenance.values():
        by_canon[entry["canon"]] = by_canon.get(entry["canon"], 0) + 1
    for canon in sorted(by_canon):
        print(f"  канон {canon}: {by_canon[canon]} заданий")

    ids = sorted(provenance)
    conn = await asyncpg.connect(dsn)
    try:
        existing = await conn.fetch(
            "SELECT id, difficulty_id, difficulty_provenance FROM tasks "
            "WHERE id = ANY($1::int[])",
            ids,
        )
        found = {r["id"] for r in existing}
        missing = sorted(set(ids) - found)
        if missing:
            print(f"\nОШИБКА: заданий нет в БД: {missing[:20]} — СТОП")
            return 1
        already = [r["id"] for r in existing if r["difficulty_provenance"] is not None]
        print(f"\nнайдено {len(found)} заданий; уже с происхождением: {len(already)}")

        # Обоснование пишется только там, где канон подразумевает ИМЕННО ТО
        # значение, что стоит в поле. Расхождение значит, что правку не
        # применяли (например, она требует переноса между курсами) — записать
        # обоснование к неприменённому решению = получить достоверно
        # выглядящую ложь.
        current = {r["id"]: r["difficulty_id"] for r in existing}
        contradicting = sorted(
            task_id for task_id, want in implied.items()
            if task_id in current and current[task_id] != want
        )
        for task_id in contradicting:
            print(
                f"  ПРОПУСК id={task_id}: канон подразумевает уровень {implied[task_id]}, "
                f"в поле {current[task_id]} — решение не применено, обоснование не пишем"
            )
            provenance.pop(task_id, None)
        ids = sorted(provenance)

        tx = conn.transaction()
        await tx.start()
        try:
            if contradicting:
                # Идемпотентность: если противоречащее обоснование записал
                # прошлый прогон — снимаем его, а не оставляем висеть.
                cleared = await conn.execute(
                    "UPDATE tasks SET difficulty_provenance = NULL "
                    "WHERE id = ANY($1::int[]) AND difficulty_provenance IS NOT NULL",
                    contradicting,
                )
                print(f"снято противоречащее обоснование: {cleared}")

            for task_id in ids:
                await conn.execute(
                    "UPDATE tasks SET difficulty_provenance = $1::jsonb WHERE id = $2",
                    json.dumps(provenance[task_id], ensure_ascii=False), task_id,
                )

            after = await conn.fetch(
                "SELECT id, difficulty_provenance FROM tasks WHERE id = ANY($1::int[])",
                ids,
            )
            bad: list[str] = []
            for row in after:
                value = row["difficulty_provenance"]
                value = json.loads(value) if isinstance(value, str) else value
                if value != provenance[row["id"]]:
                    bad.append(f"id={row['id']}: записано {value}, ожидали {provenance[row['id']]}")
            if len(after) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for line in bad[:20]:
                    print(f"  - {line}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after)}/{len(ids)} совпали — OK")

            total = await conn.fetchval("SELECT count(*) FROM tasks WHERE is_active")
            with_prov = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active AND difficulty_provenance IS NOT NULL"
            )
            print(f"активных заданий: {total}, из них с происхождением: {with_prov}")

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
