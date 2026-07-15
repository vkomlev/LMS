"""
tsk-227 · Фаза 4 — проставить solution_rules.requires_attachment=true целевым заданиям.

КОГДА ЗАПУСКАТЬ: строго ПОСЛЕ деплоя клиентов (SPW/TG_LMS с обязательной
загрузкой файла). Иначе ученик упрётся в форс без способа приложить файл (R1 спека).

БЕЗОПАСНОСТЬ (протокол /db-check, прод-write):
- Прод-DSN берётся из env DATABASE_URL (LMS/.mcp.json), НЕ из .env (там dev localhost).
- Запуск записи:
      DBCHECK_OK=1 DATABASE_URL=<прод> PYTHONIOENCODING=utf-8 \
          python scripts/set_requires_attachment_tsk227.py --ids-file <frozen>.txt --apply
- По умолчанию — DRY-RUN: только SELECT-отчёт кандидатов, НИКАКИХ записей.
- --apply пишет в одной транзакции: before-count → UPDATE → after-verify → commit,
  при несходе verify — откат (raise внутри транзакции).
- Идемпотентность: задания с requires_attachment=true пропускаются.
- Секреты (DSN) не печатаются.

ЗАМОРОЗКА СПИСКА (Phase 0, пер-таск): широкий селектор по формулировке stem
над-захватывает (в проде ~26 SA + ~64 SA_COM содержат «приложи», Фаза 0 фиксировала 44
после ручного отбора пограничных SA_COM-миссий). Поэтому для --apply ОБЯЗАТЕЛЕН явный
замороженный список ID (--ids / --ids-file), согласованный с оператором по dry-run отчёту.
Без явного списка --apply отклоняется.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tsk227.requires_attachment")

# Целевые диапазоны курсов из Фазы 0 (документированы в спеке tech-spec-tsk227).
SA_MANUAL_COURSE_MIN, SA_MANUAL_COURSE_MAX = 924, 958   # чат-бот-курсы, manual_review=true
SA_COM_COURSE_MIN, SA_COM_COURSE_MAX = 1095, 1242       # флагман, миссии по слову

_CANDIDATE_SQL = """
    SELECT
        t.id AS task_id,
        t.course_id,
        t.task_content->>'type' AS type,
        (t.solution_rules->>'manual_review_required') AS manual,
        (t.solution_rules->>'requires_attachment') AS requires_attachment,
        left(t.task_content->>'stem', 90) AS stem_head
    FROM tasks t
    WHERE t.is_active = true
      AND t.task_content->>'type' IN ('SA', 'SA_COM')
      AND (
          t.task_content->>'stem' ILIKE '%приложи%'
          OR t.task_content->>'stem' ILIKE '%прикрепи%'
          OR t.task_content->>'stem' ILIKE '%пришли скрин%'
          OR t.task_content->>'stem' ILIKE '%скриншот%'
          OR t.task_content->>'stem' ILIKE '%карточк%'
      )
      AND (
          (t.task_content->>'type' = 'SA'
           AND (t.solution_rules->>'manual_review_required') = 'true'
           AND t.course_id BETWEEN $1 AND $2)
          OR
          (t.task_content->>'type' = 'SA_COM'
           AND t.course_id BETWEEN $3 AND $4)
      )
    ORDER BY t.task_content->>'type', t.course_id, t.id
"""


def _dsn() -> str:
    """Прод-DSN только из env DATABASE_URL. Без него — падаем, чтобы не задеть dev по .env."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.error(
            "DATABASE_URL не задан. Передай прод-DSN явно: "
            "DATABASE_URL=<прод из LMS/.mcp.json> ... (в .env лежит dev localhost)."
        )
        sys.exit(2)
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def _parse_ids(ids_arg: Optional[str], ids_file: Optional[str]) -> Optional[set[int]]:
    """Явный список ID из --ids и/или --ids-file. None → список не задан."""
    ids: set[int] = set()
    if ids_arg:
        ids.update(int(x) for x in ids_arg.replace(",", " ").split())
    if ids_file:
        for token in Path(ids_file).read_text(encoding="utf-8").replace(",", " ").split():
            token = token.strip()
            if token and not token.startswith("#"):
                ids.add(int(token))
    return ids or None


def _print_report(candidates: list[asyncpg.Record], ids: Optional[set[int]]) -> None:
    logger.info("Кандидатов по селектору Фазы 0: %d", len(candidates))
    by_type: dict[str, int] = {}
    already = 0
    for r in candidates:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        if r["requires_attachment"] == "true":
            already += 1
    for tp, n in sorted(by_type.items()):
        logger.info("  %-7s: %d", tp, n)
    logger.info("  уже requires_attachment=true (будут пропущены): %d", already)
    print("\ntask_id | course | type    | manual | req_att | stem")
    print("-" * 90)
    for r in candidates:
        print(
            f"{r['task_id']:>7} | {r['course_id']:>6} | {r['type']:<7} | "
            f"{str(r['manual']):<6} | {str(r['requires_attachment']):<7} | {r['stem_head']}"
        )
    if ids is not None:
        missing = ids - {r["task_id"] for r in candidates}
        if missing:
            logger.warning(
                "ID из списка НЕ попали в кандидаты (не активны / не тот тип / вне диапазона): %s",
                sorted(missing),
            )


async def _apply(conn: asyncpg.Connection, target_ids: list[int]) -> None:
    """Проставить requires_attachment=true в транзакции с before/after verify."""
    async with conn.transaction():
        before = await conn.fetchval(
            "SELECT count(*) FROM tasks "
            "WHERE id = ANY($1::int[]) AND (solution_rules->>'requires_attachment') = 'true'",
            target_ids,
        )
        logger.info("before: уже помечено %d из %d", before, len(target_ids))

        status = await conn.execute(
            "UPDATE tasks "
            "SET solution_rules = jsonb_set(solution_rules, '{requires_attachment}', 'true'::jsonb, true) "
            "WHERE id = ANY($1::int[]) "
            "  AND coalesce((solution_rules->>'requires_attachment'), 'false') <> 'true'",
            target_ids,
        )
        logger.info("UPDATE статус: %s", status)

        after = await conn.fetchval(
            "SELECT count(*) FROM tasks "
            "WHERE id = ANY($1::int[]) AND (solution_rules->>'requires_attachment') = 'true'",
            target_ids,
        )
        logger.info("after-verify: requires_attachment=true у %d из %d", after, len(target_ids))
        if after != len(target_ids):
            raise RuntimeError(
                f"verify не сошёлся: помечено {after}, ожидалось {len(target_ids)}. Откат транзакции."
            )
    logger.info("COMMIT: транзакция зафиксирована.")


async def main(args: argparse.Namespace) -> int:
    ids = _parse_ids(args.ids, args.ids_file)
    conn = await asyncpg.connect(_dsn())
    try:
        candidates = await conn.fetch(
            _CANDIDATE_SQL,
            SA_MANUAL_COURSE_MIN, SA_MANUAL_COURSE_MAX,
            SA_COM_COURSE_MIN, SA_COM_COURSE_MAX,
        )
        if ids is not None:
            candidates = [r for r in candidates if r["task_id"] in ids]
        _print_report(candidates, ids)

        if not args.apply:
            logger.info("DRY-RUN: записей не было. Для применения — --apply с явным --ids/--ids-file.")
            return 0

        if ids is None:
            logger.error(
                "--apply отклонён: нужен явный замороженный список ID (--ids/--ids-file), "
                "согласованный с оператором по dry-run отчёту. Широкий селектор над-захватывает "
                "пограничные миссии — их отбирают пер-таск."
            )
            return 2

        target_ids = sorted({r["task_id"] for r in candidates})
        if not target_ids:
            logger.error("Пустое пересечение списка ID и кандидатов — нечего применять.")
            return 2
        logger.info("Применяю requires_attachment=true к %d заданиям: %s", len(target_ids), target_ids)
        await _apply(conn, target_ids)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-227 Фаза 4: requires_attachment=true")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run).")
    parser.add_argument("--ids", help="Явный список task_id через запятую/пробел.")
    parser.add_argument("--ids-file", help="Файл со списком task_id (по одному в строке, # — коммент).")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
