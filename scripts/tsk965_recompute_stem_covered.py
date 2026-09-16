# scripts/tsk965_recompute_stem_covered.py
"""
tsk-965: пересчитать пометку «непройденная конструкция» с учётом условия
САМОГО задания (`task_content.stem`).

Живой случай: id-271 «Объединение трёх списков в один» даёт в условии
`list1 = [1, 2, 3]` и т. п. готовым текстом — ученик копирует список оттуда,
а признак называл литерал списка непройденной конструкцией. Разбор по
прод-БД (16.09): `list_literal` — 215 из 268 (80%) всех пометок за всё время.

Как и `tsk915_recompute_unseen_constructs.py`, этот скрипт ПЕРЕСЧИТЫВАЕТ
секцию заново у ВСЕХ готовых разборов кода, а не только у 215 записей с
`list_literal` — механизм («условие задания не проверялось как источник
конструкции») общий для всех 16 конструкций каталога, не только для списков
(решение оператора 16.09, декомпозиция п.2 задачи). Вердикт модели, балл и
разбор линтера не трогаются: пересчитывается только ключ `unseen_constructs`.

**Сверка идёт на момент СДАЧИ** (`as_of=submitted_at`), как и в tsk-864/915 —
иначе пересчёт задним числом снял бы пометку с работ, где ученик прошёл
нужную тему позже дня сдачи.

Запуск (по умолчанию — dry-run, ничего не пишет):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk965_recompute_stem_covered.py
    ... --limit 50            # ограничить объём первого прогона
    ... --result-id 27096     # одна работа: удобно для проверки глазами
    DBCHECK_OK=1 PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk965_recompute_stem_covered.py --apply
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

logger = logging.getLogger("tsk965.recompute")

#: Кандидаты: все готовые разборы кода (та же выборка, что в tsk915 — здесь
#: пересчитываем и уже посчитанные, каталог не менялся, а сам расчёт — да).
_CANDIDATES_SQL = """
    SELECT tr.id,
           tr.user_id,
           tr.attempt_id,
           tr.task_id,
           t.course_id,
           t.task_content->>'stem'                AS stem,
           tr.submitted_at,
           tr.answer_json->'response'->>'value'   AS value,
           tr.answer_json->'response'->>'comment' AS comment,
           tr.answer_json->'response'->'meta'->'attachments' AS attachments,
           tr.code_review->'unseen_constructs' AS old_unseen
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.code_review->>'status' = 'done'
      AND COALESCE(tr.code_review->>'kind', 'code') = 'code'
      AND (CAST(:only_id AS int) IS NULL OR tr.id = CAST(:only_id AS int))
    ORDER BY tr.submitted_at
"""

_WRITE_SQL = """
    UPDATE task_results
       SET code_review = code_review || CAST(:patch AS jsonb)
     WHERE id = :id
       AND code_review->>'status' = 'done'
"""


def _codes(report: Optional[Dict[str, Any]]) -> List[str]:
    if not report or not isinstance(report.get("items"), list):
        return []
    return [i["code"] for i in report["items"] if isinstance(i, dict) and "code" in i]


async def collect(limit: Optional[int], only_id: Optional[int]) -> List[Dict[str, Any]]:
    """Пересчитать пометку для кандидатов, ничего не записывая. Отдаёт только те,
    где НОВЫЙ результат отличается от старого (появилась/исчезла/сменилась)."""
    out: List[Dict[str, Any]] = []
    cache: Dict[Any, Any] = {}
    async with async_session_factory() as db:
        rows = (await db.execute(text(_CANDIDATES_SQL), {"only_id": only_id})).fetchall()
        logger.info("кандидатов (все готовые разборы кода): %s", len(rows))
        checked = 0
        for row in rows:
            (result_id, user_id, attempt_id, task_id, course_id, stem, submitted_at,
             value, comment, attachments, old_unseen) = row
            code = pick_code_for_review(
                value, comment, attachments,
                attempt_id=attempt_id, task_id=task_id, allow_untagged=True,
            )
            if not code:
                continue
            new_report = await unseen_constructs_service.build_report(
                db, student_id=user_id, course_id=course_id,
                code=code, stem=stem, as_of=submitted_at, cache=cache,
            )
            checked += 1
            old_codes = set(_codes(old_unseen))
            new_codes = set(_codes(new_report))
            if old_codes == new_codes and (old_unseen is not None) == (new_report is not None):
                continue  # без изменений - не трогаем строку вообще
            out.append({
                "id": result_id,
                "user_id": user_id,
                "course_id": course_id,
                "submitted_at": submitted_at,
                "old_codes": sorted(old_codes),
                "new_report": new_report,
            })
            if limit is not None and len(out) >= limit:
                break
        logger.info("проверено: %s; изменившихся: %s", checked, len(out))
    return out


async def apply(rows: List[Dict[str, Any]]) -> int:
    """Записать пересчитанные секции одной пачкой (по строке за раз, как в tsk-864/915)."""
    if not rows:
        return 0
    async with async_session_factory() as db:
        written = 0
        for row in rows:
            patch: Dict[str, Any] = {"unseen_constructs": row["new_report"]}
            result = await db.execute(text(_WRITE_SQL), {
                "id": row["id"],
                "patch": json.dumps(patch, ensure_ascii=False),
            })
            written += result.rowcount or 0
        await db.commit()
    return written


def report(rows: List[Dict[str, Any]]) -> None:
    appeared = [r for r in rows if not r["old_codes"] and _codes(r["new_report"])]
    disappeared = [r for r in rows if r["old_codes"] and not _codes(r["new_report"])]
    changed = [r for r in rows if r not in appeared and r not in disappeared]

    # tsk-965: операторское решение было явно про list_literal (215 боевых
    # пометок) - отдельный счётчик именно по нему, чтобы сверить с прогнозом
    # до записи, а не только с общим числом изменившихся строк.
    list_literal_removed = [
        r for r in rows
        if "list_literal" in r["old_codes"] and "list_literal" not in _codes(r["new_report"])
    ]
    list_literal_kept = [
        r for r in rows
        if "list_literal" in r["old_codes"] and "list_literal" in _codes(r["new_report"])
    ]

    print(
        f"изменившихся строк: {len(rows)}; "
        f"пометка появилась: {len(appeared)}; пометка пропала: {len(disappeared)}; "
        f"набор кодов сменился: {len(changed)}"
    )
    print(
        f"list_literal: снято {len(list_literal_removed)}, "
        f"осталось (реальная пометка) {len(list_literal_kept)} "
        f"из {len(list_literal_removed) + len(list_literal_kept)} затронутых этим прогоном"
    )
    for label, group in (("появилась", appeared), ("сменился набор", changed), ("пропала", disappeared)):
        for row in group[:15]:
            new_codes = sorted(_codes(row["new_report"]))
            print(
                f"  [{label}] tr={row['id']} ученик={row['user_id']} курс={row['course_id']} "
                f"{row['submitted_at']:%Y-%m-%d}: было={row['old_codes']} стало={new_codes}"
            )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="tsk-965: пересчитать пометку о непройденных конструкциях (условие задания как источник)"
    )
    parser.add_argument("--apply", action="store_true", help="записать (без флага - только показать)")
    parser.add_argument("--limit", type=int, default=None, help="взять не больше N изменившихся строк")
    parser.add_argument("--result-id", type=int, default=None, help="только одна работа (task_results.id)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)

    rows = await collect(args.limit, args.result_id)
    report(rows)
    if not args.apply:
        print("\n(dry-run - ничего не записано; для записи добавьте --apply)")
        return
    written = await apply(rows)
    print(f"\nзаписано строк: {written}")


if __name__ == "__main__":
    asyncio.run(main())
