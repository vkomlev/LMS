# scripts/tsk646_backfill_text_authorship.py
"""
tsk-646: поставить в очередь на разбор развёрнутые работы, сданные ДО фичи.

Признак ИИ-авторства для текстов появился 2026-08-23 и работает только для
новых сдач. Уже сданные `TA` остались без него — а это вся история, по которой
преподаватель судит об ученике, и именно из-за неё задача и возникла.

**Что скрипт делает и чего НЕ делает.** Он только помечает работы
`code_review = {"status": "pending", "kind": "text", …}`; сам разбор ведёт
штатный фоновый тик (`code_review_cron_service`) — тем же кодом, тем же
промптом, с тем же учётом расхода. Второй копии логики не заводится.

**Баллы и зачёты не трогаются вовсе.** Решение оператора 2026-08-23: признак
проставить, оценки не пересматривать. Скрипт физически не пишет ни в `score`,
ни в `is_correct`, ни в `checked_at` — только в `code_review`.

Отбор — то же правило, что у живого приёма ответа, чтобы история и новые сдачи
разбирались одинаково: тип задания `TA` и текст не короче порога
(`pick_text_for_review`). Работы, у которых `code_review` уже заполнен, не
трогаются: повторный разбор стоил бы денег и перезаписал бы вердикт, на который
преподаватель мог уже опереться.

Запуск (по умолчанию — предпросмотр, ничего не пишет):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk646_backfill_text_authorship.py
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk646_backfill_text_authorship.py --apply
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
from app.services.text_authorship_service import pick_text_for_review

logger = logging.getLogger("tsk646.backfill")

# Кандидаты: развёрнутые работы, у которых отчёта ещё нет вовсе.
#
# `code_review = 'null'::jsonb` проверяем отдельно от SQL NULL — в jsonb-колонке
# это РАЗНЫЕ вещи, и без второй половины условия работа молча выпала бы.
#
# Отменённые и просроченные попытки отсекаем ровно как живой приём ответа.
_CANDIDATES_SQL = """
    SELECT tr.id,
           tr.user_id,
           tr.task_id,
           t.external_uid,
           tr.answer_json->'response'->>'text' AS body
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    LEFT JOIN attempts a ON a.id = tr.attempt_id
    WHERE (tr.code_review IS NULL OR tr.code_review = 'null'::jsonb)
      AND t.task_content->>'type' = 'TA'
      AND COALESCE(TRIM(tr.answer_json->'response'->>'text'), '') <> ''
      AND (a.id IS NULL OR (a.cancelled_at IS NULL AND a.time_expired IS NOT TRUE))
    ORDER BY tr.submitted_at DESC
"""


async def collect(limit: int | None) -> Dict[str, Any]:
    """Считает, что подлежит разбору. Ничего не пишет."""
    picked: List[Dict[str, Any]] = []
    skipped_short = 0

    async with async_session_factory() as db:
        rows = (await db.execute(text(_CANDIDATES_SQL))).fetchall()

    for result_id, user_id, task_id, external_uid, body in rows:
        # `--limit` режет только запись, а не разбор: иначе счётчики в отчёте
        # оказались бы из разных множеств.
        picked_text = pick_text_for_review(body)
        if not picked_text:
            skipped_short += 1
            continue
        picked.append({
            "id": result_id,
            # Снимок текста — той же природы, что снимок кода: разбор обязан
            # идти по тому, что сдали, а не по позднейшей редакции.
            "text": picked_text,
            "user_id": user_id,
            "task": external_uid or f"task#{task_id}",
            "chars": len(picked_text),
            "preview": " ".join(picked_text.split())[:60],
        })

    return {
        "candidates": len(rows),
        "long_enough": len(picked),
        "to_queue": picked[:limit] if limit else picked,
        "skipped_short": skipped_short,
    }


async def apply(rows: List[Dict[str, Any]]) -> int:
    """Помечает работы к разбору, сохраняя снимок текста."""
    if not rows:
        return 0
    updated = 0
    async with async_session_factory() as db:
        for row in rows:
            payload = {
                "status": "pending",
                "kind": "text",
                "backfill": True,
                # Ключ снимка исторически называется `code` — им пользуется
                # общий тик для обеих веток. Переименование потребовало бы
                # разбирать два формата ради временного поля.
                "code": row["text"],
            }
            res = await db.execute(
                text(
                    "UPDATE task_results SET code_review = CAST(:payload AS jsonb) "
                    "WHERE id = :id "
                    # Условие ТО ЖЕ, что в отборе: разойдись они — работа попала
                    # бы в отчёт и молча не обновилась.
                    "AND (code_review IS NULL OR code_review = 'null'::jsonb)"
                ),
                {"payload": json.dumps(payload, ensure_ascii=False), "id": row["id"]},
            )
            updated += res.rowcount or 0
        await db.commit()
    return updated


async def main() -> None:
    parser = argparse.ArgumentParser(description="tsk-646: очередь на разбор для старых текстовых сдач")
    parser.add_argument("--apply", action="store_true", help="записать (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=None, help="взять не больше N работ")
    args = parser.parse_args()

    report = await collect(args.limit)
    to_queue = report["to_queue"]

    print(f"Кандидатов (TA без отчёта, текст непустой): {report['candidates']}")
    print(f"Пропущено — короче порога разбора:          {report['skipped_short']}")
    print(f"К постановке в очередь:                     {len(to_queue)}")
    print()
    for row in to_queue[:20]:
        print(f"  #{row['id']:>6}  ученик {row['user_id']:>5}  знаков {row['chars']:>5}  "
              f"{row['task'][:35]:<35}  {row['preview']}")
    if len(to_queue) > 20:
        print(f"  ... и ещё {len(to_queue) - 20}")

    if not args.apply:
        print("\nЭто предпросмотр. Для записи добавьте --apply")
        return

    updated = await apply(to_queue)
    print(f"\nПомечено к разбору: {updated}")
    print("Дальше их разберёт фоновый тик (интервал — CODE_REVIEW_CRON_INTERVAL_MIN).")
    print("Баллы и зачёты не менялись — скрипт пишет только в code_review.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
