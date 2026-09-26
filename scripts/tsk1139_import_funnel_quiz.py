"""Импорт квиза-воронки из JSON контента (tsk-1139).

Источник — `Marketing/clients/it-school/campaigns/site-funnel-quiz/quiz-razvilka.lms.json`
(его готовит контентная сессия). Скрипт:

1. проверяет правила движком: у каждого пройденного пути есть итог, каждый итог
   достижим (`quiz_funnel_engine.coverage_check`); при провале — отказ, без записи;
2. заводит/обновляет курс-квиз (`course_uid = quiz_uid`, публичный);
3. заводит/обновляет вопросы как задания `SC_Qw/MC_Qw` курса с `external_uid =
   <quiz_uid>:<код>` — по ним пишутся гостевые попытки. Схема квиз-вопроса требует
   шкалы, поэтому добавляется техническая шкала `route` с нулями: итог считают
   правила спецификации, не баллы;
4. кладёт спецификацию целиком в `quiz_funnel_spec`.

Идемпотентен. По умолчанию — разбор без записи.

    python scripts/tsk1139_import_funnel_quiz.py --file <путь к JSON>
    python scripts/tsk1139_import_funnel_quiz.py --file <путь> --apply

Против прода — с прод-DSN и после протокола /db-check:

    DBCHECK_OK=1 python scripts/tsk1139_import_funnel_quiz.py --file <путь> --apply

Файл со статусом «не загружать» без `--force-draft` не импортируется: контент
сначала утверждает оператор.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
from typing import Any, Dict, List

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import quiz_funnel_engine as engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("tsk1139_import")

TECH_SCALE = "route"


def question_rows(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Вопросы всех веток в порядке спецификации: код, тип, task_content задания."""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for branch, questions in (spec.get("branches") or {}).items():
        for question in questions:
            code = question["code"]
            if code in seen:
                raise ValueError(f"код вопроса {code} повторяется")
            seen.add(code)
            content = copy.deepcopy(question["task_content"])
            content["scales"] = [TECH_SCALE]
            for option in content.get("options") or []:
                option["scores"] = {TECH_SCALE: 0}
            rows.append({"code": code, "branch": branch, "content": content})
    return rows


async def run(conn: asyncpg.Connection, spec: Dict[str, Any], apply: bool) -> None:
    """Разобрать или записать квиз."""
    quiz_uid = spec["quiz_uid"]
    rows = question_rows(spec)
    course_id = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", quiz_uid)
    logger.info("квиз %s: курс %s, вопросов %d, итогов %d", quiz_uid,
                course_id or "будет создан", len(rows), len(spec.get("outcomes") or []))
    # Курсы итогов, которых нет в базе: итог покажется, но записать и открыть
    # демо будет некуда. Не отказ — контент может дозаполнить их позже.
    targets = sorted({o["target_course_uid"] for o in spec.get("outcomes") or []
                      if o.get("target_course_uid")})
    known = {r["course_uid"] for r in await conn.fetch(
        "SELECT course_uid FROM courses WHERE course_uid = ANY($1::text[])", targets)}
    for uid in targets:
        if uid not in known:
            logger.warning("курса итога нет в базе: %s", uid)
    if not apply:
        return

    if course_id is None:
        course_id = await conn.fetchval(
            "INSERT INTO courses (title, access_level, course_uid, is_public_demo, description) "
            "VALUES ($1, 'self_guided', $2, TRUE, $3) RETURNING id",
            spec.get("title") or quiz_uid, quiz_uid, spec.get("description"),
        )
    else:
        await conn.execute(
            "UPDATE courses SET title = $2, description = $3, is_public_demo = TRUE WHERE id = $1",
            course_id, spec.get("title") or quiz_uid, spec.get("description"),
        )
    difficulty_id = await conn.fetchval("SELECT id FROM difficulties ORDER BY id LIMIT 1")
    for order, row in enumerate(rows, start=1):
        mode = "multi" if row["content"].get("type") == "MC_Qw" else "single"
        rules = {"max_score": 1, "quiz": {"scales": [TECH_SCALE], "mode": mode}}
        await conn.execute(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id, "
            "solution_rules, order_position) VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb, $6) "
            "ON CONFLICT (external_uid) DO UPDATE SET task_content = EXCLUDED.task_content, "
            "solution_rules = EXCLUDED.solution_rules, order_position = EXCLUDED.order_position, "
            "course_id = EXCLUDED.course_id",
            f"{quiz_uid}:{row['code']}", json.dumps(row["content"], ensure_ascii=False),
            course_id, difficulty_id, json.dumps(rules), order,
        )
    await conn.execute(
        "INSERT INTO quiz_funnel_spec (course_id, spec) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (course_id) DO UPDATE SET spec = EXCLUDED.spec, updated_at = now()",
        course_id, json.dumps(spec, ensure_ascii=False),
    )
    logger.info("записано: курс %s, вопросов %d, спецификация обновлена", course_id, len(rows))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт квиза-воронки (tsk-1139)")
    parser.add_argument("--file", required=True, help="JSON контента квиза")
    parser.add_argument("--apply", action="store_true", help="записать; без флага — разбор")
    parser.add_argument("--force-draft", action="store_true",
                        help="импортировать файл со статусом «не загружать» (только dev)")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        spec = json.load(fh)
    status = str(spec.get("status") or "")
    if "не загружать" in status and not args.force_draft:
        logger.error("статус файла: %r — контент не утверждён, импорт отменён", status)
        sys.exit(3)

    coverage = engine.coverage_check(spec)
    logger.info("проверка правил: %d прохождений, без итога %d, недостижимых итогов %d",
                coverage.runs, coverage.without_outcome, len(coverage.unreached_outcomes))
    if coverage.without_outcome or coverage.unreached_outcomes:
        logger.error("правила неполны: без итога %s; недостижимы %s",
                     coverage.samples_without_outcome[:2], coverage.unreached_outcomes)
        sys.exit(4)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.error("не задан DATABASE_URL")
        sys.exit(2)
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if args.apply:
            # Одна транзакция: половина квиза хуже, чем его отсутствие.
            async with conn.transaction():
                await run(conn, spec, apply=True)
        else:
            await run(conn, spec, apply=False)
            logger.info("это был разбор без записи; чтобы применить — добавьте --apply")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
