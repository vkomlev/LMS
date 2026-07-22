"""tsk-358 round 3 — последнее из 16 отложенных заданий: id=2355.

Курс 144, функция F(n): F(n)=n при n<9; F(n)=F(n mod 9)+F(n div 9) при n>=9.
Определить количество n на отрезке [4*6^20; 5*6^20], для которых F(n)=121.
Сломанный ответ был обрывком текста ("- разность между количеством
подходящих чисел на отрезке [1"). Оператор дал верное значение: 194257368.

Запуск (на прод-сервере, .env с прод DSN):
    python scripts/fix_manual_review_answer_tsk358_id2355.py            # dry-run
    python scripts/fix_manual_review_answer_tsk358_id2355.py --apply    # COMMIT
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

TASK_ID = 2355
ANSWER = "194257368"


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-358 round3: id={TASK_ID} — {mode} ===\n")

    async with async_session_factory() as db:
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )

        result = await db.execute(
            text(
                "UPDATE tasks SET solution_rules = jsonb_set("
                "  jsonb_set(solution_rules, '{short_answer,accepted_answers,0,value}', to_jsonb(CAST(:answer AS text))),"
                "  '{manual_review_required}', 'false'::jsonb"
                ") WHERE id = :task_id"
                "  AND task_content->>'type' = 'SA_COM'"
                "  AND solution_rules ? 'short_answer'"
            ),
            {"task_id": TASK_ID, "answer": ANSWER},
        )
        print(f"UPDATE rowcount = {result.rowcount} (ожидалось 1)")
        if result.rowcount != 1:
            print("ОШИБКА: количество обновлённых строк не совпало — ROLLBACK")
            await db.rollback()
            return 1

        row = (await db.execute(
            text(
                "SELECT solution_rules->>'manual_review_required' AS mrr, "
                "  solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS answer "
                "FROM tasks WHERE id = :task_id"
            ),
            {"task_id": TASK_ID},
        )).mappings().one()
        print(f"id={TASK_ID} mrr={row['mrr']} answer={row['answer']!r}")
        if row["mrr"] != "false" or row["answer"] != ANSWER:
            print("ОШИБКА: верификация не совпала — ROLLBACK")
            await db.rollback()
            return 1

        print("\nВерификация пройдена.")

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
