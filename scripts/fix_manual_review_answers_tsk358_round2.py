"""tsk-358 round 2 — ручной разбор оператора для 15 из 16 отложенных заданий.

Первый прогон (`fix_manual_review_required_tsk358.py`) снял
manual_review_required=true у 170 задач с уже надёжным accepted_answers.
16 заданий с браком экстракции (пустой ответ / формула / обрывок текста /
неоднозначная склейка вариантов) были оставлены под ручной проверкой.
Оператор разобрал их вручную (сверка с источником sdamgia) и дал верные
ответы для 15 из 16 — id=2355 пока без ответа, остаётся под ручной проверкой.

Для многострочных табличных ответов (2379/2380/2381/2382/2386) значения
склеены пробелом в одну строку: normalization ("collapse_spaces" — `"
".join(result.split())`, `checking_service.py:743`) схлопывает ЛЮБОЙ
пробельный разделитель (пробел/перенос строки) одинаково, поэтому порядок
многострочный/однострочный не влияет на сравнение.

id=54 — отдельный случай: не sdamgia-импорт, а собственный авторский текст
курса (wp:task:komlev). Оператор указал, что исходный текст задания был
испорчен (`"со значением (пусто)"` вместо конкретного числа) — правится и
task_content.stem (значение 1051 + недостающая инструкция про округление),
и solution_rules (ответ = sqrt(1051), округлено до 2 знаков = 32.42).

Запуск (на прод-сервере, .env с прод DSN):
    python scripts/fix_manual_review_answers_tsk358_round2.py            # dry-run
    python scripts/fix_manual_review_answers_tsk358_round2.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

# id -> верный ответ (от оператора, сверено с источником).
ANSWERS: dict[int, str] = {
    2242: "DFCA",
    2300: "1355",
    2303: "1296",
    2304: "154248381",
    2306: "1253 2494",
    2310: "1150 2652",
    2311: "667 4009",
    2313: "416 1390",
    2338: "21",
    2379: "162139404 80148 1321399324 653188 1421396214 702618 1521393104 752048",
    2380: "1113840 1179360 1208844 1499400",
    2381: "41818182 261959 5 271 57500001",
    2382: "294499921 2248091 352275361 2571353 373301041 2685619",
    2386: "10738 30730 37522 51277",
}

TASK_54_STEM = (
    "Задание 10 . Напишите программу, которая находит квадратный корень числа, "
    "которое вводит пользователь.\n\n"
    "Запустите программу со значением `1051`. Выведите результат с округлением "
    "до двух знаков после десятичной точки. Введите результат в поле «Ответ»."
)
TASK_54_ANSWER = "32.42"  # sqrt(1051) = 32.41913015489465 -> round(.., 2)

# Ещё под ручной проверкой — ответ не дан оператором.
STILL_PENDING = [2355]

EXPECTED_ANSWERS_UPDATED = len(ANSWERS) + 1  # + id=54


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-358 round2: ответы оператора для 15/16 отложенных — {mode} ===\n")

    async with async_session_factory() as db:
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )

        # --- sdamgia-задания: только значение accepted_answers[0].value + снять флаг ---
        updated = 0
        for task_id, answer in ANSWERS.items():
            result = await db.execute(
                text(
                    "UPDATE tasks SET solution_rules = jsonb_set("
                    "  jsonb_set(solution_rules, '{short_answer,accepted_answers,0,value}', to_jsonb(CAST(:answer AS text))),"
                    "  '{manual_review_required}', 'false'::jsonb"
                    ") WHERE id = :task_id"
                    "  AND task_content->>'type' = 'SA_COM'"
                    "  AND solution_rules ? 'short_answer'"
                ),
                {"task_id": task_id, "answer": answer},
            )
            updated += result.rowcount

        # --- id=54: свой текст + ответ + снять флаг ---
        result54 = await db.execute(
            text(
                "UPDATE tasks SET "
                "  task_content = jsonb_set(task_content, '{stem}', to_jsonb(CAST(:stem AS text))),"
                "  solution_rules = jsonb_set("
                "    jsonb_set(solution_rules, '{short_answer,accepted_answers,0,value}', to_jsonb(CAST(:answer AS text))),"
                "    '{manual_review_required}', 'false'::jsonb"
                "  )"
                "WHERE id = 54"
            ),
            {"stem": TASK_54_STEM, "answer": TASK_54_ANSWER},
        )
        updated += result54.rowcount

        print(f"UPDATE rowcount = {updated} (ожидалось {EXPECTED_ANSWERS_UPDATED})")
        if updated != EXPECTED_ANSWERS_UPDATED:
            print("ОШИБКА: количество обновлённых строк не совпало — ROLLBACK")
            await db.rollback()
            return 1

        # --- верификация: значения записались, флаг снят ---
        all_ids = list(ANSWERS.keys()) + [54]
        rows = (await db.execute(
            text(
                "SELECT id, solution_rules->>'manual_review_required' AS mrr, "
                "  solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS answer "
                "FROM tasks WHERE id = ANY(:ids) ORDER BY id"
            ),
            {"ids": all_ids},
        )).mappings().all()

        mismatches = []
        expected = {**ANSWERS, 54: TASK_54_ANSWER}
        for row in rows:
            if row["mrr"] != "false" or row["answer"] != expected[row["id"]]:
                mismatches.append(dict(row))
            print(f"  id={row['id']:5d} mrr={row['mrr']:5s} answer={row['answer']!r}")

        if mismatches:
            print(f"\nОШИБКА: несовпадения после апдейта: {mismatches} — ROLLBACK")
            await db.rollback()
            return 1

        # --- id=2355 должен остаться нетронутым (mrr=true, ответ не дан) ---
        pending = (await db.execute(
            text(
                "SELECT id, solution_rules->>'manual_review_required' AS mrr "
                "FROM tasks WHERE id = ANY(:ids)"
            ),
            {"ids": STILL_PENDING},
        )).mappings().all()
        for row in pending:
            print(f"  (оставлено под ручной проверкой) id={row['id']} mrr={row['mrr']}")
            if row["mrr"] != "true":
                print("ОШИБКА: id=2355 не должен был измениться — ROLLBACK")
                await db.rollback()
                return 1

        print(f"\nВерификация пройдена: {updated} задач обновлены верными ответами оператора, "
              f"id={STILL_PENDING} по-прежнему под ручной проверкой.")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения сохранены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
