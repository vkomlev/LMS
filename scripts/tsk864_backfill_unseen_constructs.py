# scripts/tsk864_backfill_unseen_constructs.py
"""
tsk-864: досчитать пометку «конструкция из непройденной темы» для готовых отчётов.

Признак появился 2026-09-09 и считается фоновым тиком при разборе работы. Уже
разобранные работы (2299 отчётов со статусом `done`) остались без него — а
именно по ним преподаватель и смотрит историю ученика.

Скрипт НЕ трогает ни вердикт модели, ни балл, ни разбор линтера: он добавляет в
`code_review` ровно один ключ `unseen_constructs` и только тем работам, у
которых его ещё нет. Повторная оценка моделью не запускается — она стоила бы
денег и переписала бы вердикт, на который преподаватель мог уже опереться.

**Сверка идёт на момент СДАЧИ, а не на сегодня** (`as_of=submitted_at`). Ученик
мог дойти до нужной темы позже: на сегодняшнем состоянии пометка с такой работы
молча исчезла бы, хотя в день сдачи конструкции он действительно не знал. На
боевых данных это ровно одна из пяти работ — та, где ученик прошёл «Работу со
строками» через три недели после сдачи.

Код берётся тем же правилом, что у живого приёма ответа
(`pick_code_for_review`), с `allow_untagged=True`: у истории есть вложения
старого формата без метки задания, а задним числом ученик их уже не перезальёт
— тот же довод, что у пересчёта tsk-302.

Запуск (по умолчанию — dry-run, ничего не пишет):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk864_backfill_unseen_constructs.py
    ... --limit 50            # ограничить объём первого прогона
    ... --result-id 25723     # одна работа: удобно для проверки глазами
    DBCHECK_OK=1 PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk864_backfill_unseen_constructs.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db.session import async_session_factory
from app.services import unseen_constructs_service
from app.services.code_review_service import pick_code_for_review

logger = logging.getLogger("tsk864.backfill")

#: Кандидаты: разобранные работы с кодом, у которых пометки ещё нет.
#:
#: `kind` у работ, помеченных до появления текстовой ветки, отсутствует — они
#: все про код, отсюда COALESCE (то же правило, что в фоновом тике).
_CANDIDATES_SQL = """
    SELECT tr.id,
           tr.user_id,
           tr.attempt_id,
           tr.task_id,
           t.course_id,
           tr.submitted_at,
           tr.answer_json->'response'->>'value'   AS value,
           tr.answer_json->'response'->>'comment' AS comment,
           tr.answer_json->'response'->'meta'->'attachments' AS attachments
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.code_review->>'status' = 'done'
      AND COALESCE(tr.code_review->>'kind', 'code') = 'code'
      AND NOT (tr.code_review ? 'unseen_constructs')
      AND (CAST(:only_id AS int) IS NULL OR tr.id = CAST(:only_id AS int))
    ORDER BY tr.submitted_at
"""

_WRITE_SQL = """
    UPDATE task_results
       SET code_review = code_review || CAST(:patch AS jsonb)
     WHERE id = :id
       AND code_review->>'status' = 'done'
       AND NOT (code_review ? 'unseen_constructs')
"""


async def collect(limit: Optional[int], only_id: Optional[int]) -> List[Dict[str, Any]]:
    """Посчитать пометку для кандидатов, ничего не записывая."""
    out: List[Dict[str, Any]] = []
    # Кэш на весь прогон. Помогает здесь только половиной: материалы ТЕМЫ
    # общие для всех работ по ней и читаются один раз, а пройденное ученика
    # завязано на момент сдачи (`as_of`) и у каждой работы своё.
    cache: Dict[Any, Any] = {}
    async with async_session_factory() as db:
        rows = (await db.execute(text(_CANDIDATES_SQL), {"only_id": only_id})).fetchall()
        logger.info("кандидатов: %s", len(rows))
        for row in rows:
            (result_id, user_id, attempt_id, task_id, course_id, submitted_at,
             value, comment, attachments) = row
            code = pick_code_for_review(
                value, comment, attachments,
                attempt_id=attempt_id, task_id=task_id, allow_untagged=True,
            )
            if not code:
                continue
            report = await unseen_constructs_service.build_report(
                db, student_id=user_id, course_id=course_id,
                code=code, as_of=submitted_at, cache=cache,
            )
            if report is None:
                continue
            out.append({
                "id": result_id,
                "user_id": user_id,
                "course_id": course_id,
                "submitted_at": submitted_at,
                "report": report,
            })
            if limit is not None and len(out) >= limit:
                break
    return out


async def apply(rows: List[Dict[str, Any]]) -> int:
    """Записать пометки одной транзакцией."""
    if not rows:
        return 0
    async with async_session_factory() as db:
        written = 0
        for row in rows:
            result = await db.execute(text(_WRITE_SQL), {
                "id": row["id"],
                "patch": json.dumps({"unseen_constructs": row["report"]}, ensure_ascii=False),
            })
            written += result.rowcount or 0
        await db.commit()
    return written


def report(rows: List[Dict[str, Any]]) -> None:
    """Показать, что получилось: сперва работы с пометкой, потом счётчики."""
    with_mark = [r for r in rows if r["report"]["items"]]
    print(f"посчитано: {len(rows)}; с пометкой: {len(with_mark)}; тихо: {len(rows) - len(with_mark)}")
    for row in with_mark[:30]:
        labels = ", ".join(i["label"] for i in row["report"]["items"])
        print(
            f"  tr={row['id']} ученик={row['user_id']} курс={row['course_id']} "
            f"{row['submitted_at']:%Y-%m-%d} материалов={row['report']['materials_seen']}: {labels}"
        )
        print(f"      {row['report']['items'][0]['evidence']}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="tsk-864: пометка о непройденных конструкциях для готовых отчётов"
    )
    parser.add_argument("--apply", action="store_true", help="записать (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=None, help="взять не больше N работ с пометкой")
    parser.add_argument("--result-id", type=int, default=None, help="только одна работа (task_results.id)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)

    rows = await collect(args.limit, args.result_id)
    report(rows)
    if not args.apply:
        print("\n(dry-run — ничего не записано; для записи добавьте --apply)")
        return
    written = await apply(rows)
    print(f"\nзаписано строк: {written}")


if __name__ == "__main__":
    asyncio.run(main())
