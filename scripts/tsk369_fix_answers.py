# -*- coding: utf-8 -*-
"""tsk-369, добор: починить ответы и условие у трёх заданий, вскрытых при привязке файлов.

ЧТО И ПОЧЕМУ (решение оператора, 2026-07-22)

1. **3177 — ответ 113 заменяется на 12.** В шапке условия стоял неверный номер задачи
   (27360 вместо 23760), и ответ был взят у чужой задачи — по 27360 у kompege лежит
   «исполнитель с командами A, B, C». Верный ответ подтверждён дважды: источником 23760 и
   расчётом по привязанному файлу (за первые 17 мс завершаются ровно 12 процессов из 25).

2. **3058 — вместо ручной проверки проставляется ответ 901.** Задание уходило
   преподавателю (`manual_review_required=true`, `short_answer` пуст), хотя ответ известен:
   источник 23747 даёт 901, и он же получается расчётом по файлу — последняя строка,
   удовлетворяющая условию (№41990), сумма её чисел 901.

3. **3409 — в условие дописывается сам вопрос.** Импорт оставил только преамбулу (дважды),
   а спрашиваемое потерялось: ученик видел описание таблицы и не понимал, что искать.
   Формулировка взята дословно у источника kompege:17876; ответ 5 уже стоит и подтверждён
   расчётом по файлу (максимум 4 одновременных процесса держится 5 мс, мс 15..19).
   Дубль преамбулы НЕ трогается — это отдельный класс правок, за рамками решения оператора.

Форма записи ответа повторяет существующую в этих же заданиях (`short_answer` с
`normalization: [trim, lower]`), чтобы приёмка ответа осталась той же самой.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. После COMMIT — независимая построчная
проверка. Бэкап прежних значений пишется до записи.

Запуск: python scripts/tsk369_fix_answers.py --backup <файл.json> [--apply]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

QUESTION_3409 = (
    "<p>Определите максимальную продолжительность отрезка времени (в мс), в течение "
    "которого возможно одновременное выполнение максимального количества процессов, "
    "при условии, что все независимые друг от друга процессы могут выполняться "
    "параллельно, а время окончания работы всех процессов минимально.</p>"
)

# id → новый ответ (None = ответ не трогаем)
ANSWERS: dict[int, str] = {3177: "12", 3058: "901"}
# id → что дописать в конец условия
STEM_APPEND: dict[int, str] = {3409: QUESTION_3409}

SHORT_ANSWER_TEMPLATE = {
    "regex": None,
    "use_regex": False,
    "normalization": ["trim", "lower"],
}


async def main(backup_path: Path, apply: bool) -> None:
    ids = sorted(set(ANSWERS) | set(STEM_APPEND))
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, solution_rules, "
            "       task_content->>'stem' AS stem "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        missing = sorted(set(ids) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")
        inactive = [i for i in ids if not rows[i]["is_active"]]
        if inactive:
            raise RuntimeError(f"задания неактивны, править нечего: {inactive}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"],
              "solution_rules": json.loads(rows[i]["solution_rules"] or "null"),
              "stem": rows[i]["stem"]} for i in ids],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних значений: {backup_path}")

        for i in ids:
            sr = json.loads(rows[i]["solution_rules"] or "{}")
            old = ((sr.get("short_answer") or {}).get("accepted_answers") or [{}])[0].get("value")
            print(f"  id={i} {rows[i]['external_uid']}: ответ {old!r} → {ANSWERS.get(i, old)!r}"
                  + ("; в условие дописывается вопрос" if i in STEM_APPEND else ""))

        async with conn.transaction():
            for i, value in ANSWERS.items():
                sr = json.loads(rows[i]["solution_rules"] or "{}")
                sr["short_answer"] = {**SHORT_ANSWER_TEMPLATE,
                                      "accepted_answers": [{"score": sr.get("max_score", 1),
                                                            "value": value}]}
                sr["manual_review_required"] = False
                sr["auto_check"] = True
                await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb WHERE id = $1",
                    i, json.dumps(sr, ensure_ascii=False))

            for i, block in STEM_APPEND.items():
                stem = rows[i]["stem"] or ""
                if block in stem:
                    raise RuntimeError(f"id={i}: вопрос уже дописан (повторный запуск?)")
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  jsonb_set(task_content, '{stem}', to_jsonb($2::text)) WHERE id = $1",
                    i, stem + block)

            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans, "
                "       (solution_rules->>'manual_review_required')::bool AS manual, "
                "       task_content->>'stem' AS stem "
                "FROM tasks WHERE id = ANY($1::int[])", ids)}
            bad = []
            for i, value in ANSWERS.items():
                if check[i]["ans"] != value or check[i]["manual"]:
                    bad.append((i, "ответ"))
            for i, block in STEM_APPEND.items():
                if block not in (check[i]["stem"] or ""):
                    bad.append((i, "вопрос в условии"))
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad}")
            print(f"Внутри транзакции: обновлено и проверено {len(ids)} заданий.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {r["id"]: r for r in await conn.fetch(
            "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans, "
            "       (solution_rules->>'manual_review_required')::bool AS manual, "
            "       right(task_content->>'stem', 120) AS tail "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        for i in ids:
            r = after[i]
            print(f"  id={i}: ответ={r['ans']!r} ручная_проверка={r['manual']} … {r['tail'][-70:]}")
        problems = [i for i, v in ANSWERS.items() if after[i]["ans"] != v or after[i]["manual"]]
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
