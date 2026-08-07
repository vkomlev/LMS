# scripts/tsk302_backfill_code_review.py
"""
tsk-302: поставить в очередь на оценку работы, сданные ДО появления фичи.

Оценка кода (чистота + признак ИИ-авторства) появилась 2026-08-07 и работает
только для новых сдач. Уже решённые задания остались без отчёта — а именно они
и составляют всю историю, по которой преподаватель судит об ученике.

Скрипт ничего не считает сам: он лишь помечает подходящие работы
`code_review = {"status": "pending"}`, а дальше их разбирает штатный фоновый
тик (`code_review_cron_service`) — тем же кодом, тем же промптом, с тем же
учётом расхода. Иначе пришлось бы держать вторую копию логики оценки, которая
неминуемо разъедется с основной.

Отбор — те же два правила, что и у живого приёма ответа, чтобы история и
новые сдачи оценивались одинаково:
  • задание помечено как кодовое (`turtle_sim` либо `code_ast`);
  • в ответе есть ПРОГРАММА, а не однострочник «допиши строку»
    (`pick_code_for_review`, см. находку ревью Б2).

Работы, у которых `code_review` уже заполнен, не трогаются: повторная оценка
стоила бы денег и перезаписала бы вердикт, на который преподаватель мог уже
опереться.

Запуск (по умолчанию — dry-run, ничего не пишет):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk302_backfill_code_review.py
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk302_backfill_code_review.py --apply
    ... --limit 20      # ограничить объём первого прогона
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict, List

from sqlalchemy import text

from app.db.session import async_session_factory
from app.services.code_review_service import pick_code_for_review

logger = logging.getLogger("tsk302.backfill")

# Кандидаты: кодовые задания, у которых отчёта ещё нет вовсе.
#
# `code_review = 'null'::jsonb` проверяем отдельно от SQL NULL: это РАЗНЫЕ вещи
# в jsonb-колонке, а прийти оно может от любого кода, записавшего туда пустой
# JSON. Без второй половины условия такие работы молча выпали бы из пересчёта.
#
# Отменённые и просроченные попытки отсекаем ровно как живой приём ответа
# (`attempts.py`: `cancelled_at`, `time_expired`). У просроченной балл обнулён,
# у отменённой работы нет вовсе — платить за их оценку незачем.
_CANDIDATES_SQL = """
    SELECT tr.id,
           tr.user_id,
           t.external_uid,
           tr.answer_json->'response'->>'value'   AS value,
           tr.answer_json->'response'->>'comment' AS comment
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    LEFT JOIN attempts a ON a.id = tr.attempt_id
    WHERE (tr.code_review IS NULL OR tr.code_review = 'null'::jsonb)
      AND t.is_active
      AND (t.solution_rules->'turtle_sim' IS NOT NULL
           OR t.solution_rules->'short_answer'->'normalization' ? 'code_ast')
      AND (a.id IS NULL OR (a.cancelled_at IS NULL AND a.time_expired IS NOT TRUE))
    ORDER BY tr.submitted_at DESC
"""


async def collect(limit: int | None) -> Dict[str, Any]:
    """Считает, что подлежит оценке. Ничего не пишет."""
    picked: List[Dict[str, Any]] = []
    skipped_no_program = 0

    async with async_session_factory() as db:
        rows = (await db.execute(text(_CANDIDATES_SQL))).fetchall()

    for result_id, user_id, external_uid, value, comment in rows:
        # `--limit` режет только запись, а не разбор: иначе счётчики в отчёте
        # оказались бы из разных множеств (кандидаты — по всей базе, пропуски —
        # до места обрыва) и «пропущено» читалось бы как доля от всего корпуса.
        code = pick_code_for_review(value, comment)
        if not code:
            skipped_no_program += 1
            continue
        picked.append({
            "id": result_id,
            "user_id": user_id,
            # external_uid nullable: у заданий, заведённых руками, его нет.
            "task": external_uid or f"task#{result_id}",
            "preview": code.strip().splitlines()[0][:60],
            "lines": len(code.splitlines()),
        })

    return {
        "candidates": len(rows),
        "with_program": len(picked),
        "to_queue": picked[:limit] if limit else picked,
        "skipped_no_program": skipped_no_program,
    }


async def apply(ids: List[int]) -> int:
    """Помечает работы к оценке. Возвращает число обновлённых строк."""
    if not ids:
        return 0
    async with async_session_factory() as db:
        res = await db.execute(
            text(
                "UPDATE task_results SET code_review = CAST(:payload AS jsonb) "
                "WHERE id = ANY(:ids) "
                # Условие ТО ЖЕ, что в отборе. Разойдись они — работа попала бы
                # в список «к постановке в очередь», была бы посчитана в отчёте
                # и молча не обновилась.
                "AND (code_review IS NULL OR code_review = 'null'::jsonb)"
            ),
            {"payload": json.dumps({"status": "pending", "backfill": True}), "ids": ids},
        )
        await db.commit()
        return res.rowcount or 0


async def main() -> None:
    parser = argparse.ArgumentParser(description="tsk-302: очередь на оценку для старых сдач")
    parser.add_argument("--apply", action="store_true", help="записать (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=None, help="взять не больше N работ")
    args = parser.parse_args()

    report = await collect(args.limit)
    to_queue = report["to_queue"]

    print(f"Кандидатов (кодовые задания без отчёта): {report['candidates']}")
    print(f"Пропущено — нет программы в ответе:      {report['skipped_no_program']}")
    print(f"С программой в ответе:                   {report['with_program']}")
    if len(to_queue) != report["with_program"]:
        print(f"К постановке в очередь (--limit):        {len(to_queue)}")
    else:
        print(f"К постановке в очередь:                  {len(to_queue)}")
    print()
    for row in to_queue[:15]:
        print(f"  #{row['id']:>6}  ученик {row['user_id']:>5}  строк {row['lines']:>3}  "
              f"{row['task'][:45]:<45}  {row['preview']}")
    if len(to_queue) > 15:
        print(f"  ... и ещё {len(to_queue) - 15}")

    if not args.apply:
        print("\nЭто предпросмотр. Для записи добавьте --apply")
        return

    updated = await apply([row["id"] for row in to_queue])
    print(f"\nПомечено к оценке: {updated}")
    print("Дальше их разберёт фоновый тик (интервал — CODE_REVIEW_CRON_INTERVAL_MIN).")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
