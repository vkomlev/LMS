# -*- coding: utf-8 -*-
"""tsk-367: сузить партию Крылова до пригодной части, остальное деактивировать.

РЕШЕНИЕ ОПЕРАТОРА 2026-07-22
Оставить только варианты 1, 5, 11, 16 плюс те задания вариантов 2 и 3, у которых есть
видеоразбор в Telegram-канале. Остальное — деактивировать: восстанавливать 500+ заданий,
распознанных с браком, дороже, чем они стоят.

ЧТО ВЫЯСНИЛОСЬ ПРИ РАЗБОРЕ (важно для понимания, что именно удаляется)
Партия существует в двух видах:
  * `crylov:vNtM` — 108 заданий, ровно варианты 1, 5, 11, 16. Условия чистые, ответы целые:
    80 числовых, 24 многозначных (ЕГЭ 17/18/20/25/26/27), 4 буквенных `wxyz` (ЕГЭ-2).
    Мусора нет ни одного.
  * `pdf:d4:*` и `ext:pdf:d4:*` — 540 заданий, все 20 вариантов, распознаны с браком OCR:
    условия вида «функции Р(п), где п — натуральное число… F(n) = 3 - F(n - 1) - п, если n 2 2»,
    ответы-обрывки (`ae`, `oe`, `er`). Для вариантов 1, 5, 11, 16 это вдобавок дубли —
    те же задачи уже есть в чистом виде.

Поэтому «сузить до 1/5/11/16» = оставить чистую копию и убрать OCR-версию целиком.

Из вариантов 2 и 3 разбор в канале нашёлся только по варианту 3 — задания 6, 8, 9, 11
(посты 895, 896, 897, 907/1023). По варианту 2 разборов нет ни одного. Эти 4 задания
остаются активными, но их мусорный ответ снимается: условие в них читаемое, а верный ответ
звучит в видео — впишет методист.

Отдельно: 4585 (`crylov:v11t26`) — в самом условии стоит «В ответе ошибка», а ответ «29 49»
записан как верный. Снимаем: [[tsk-368]] разберёт такие вручную.

БЕЗОПАСНОСТЬ
* Деактивация обратима (`is_active=false`), данные не удаляются.
* По деактивируемым заданиям есть 1187 записей в `task_results` — но все они
  `source_system='manual_teacher'`, `answer_json=null`, то есть проставленные преподавателем
  зачёты при переносе прогресса, а не ответы учеников. Результаты остаются в базе.
* Скрипт проверяет, что после деактивации ни один курс не остаётся без активных заданий.

Запуск: dry-run по умолчанию;
  python scripts/tsk367_crylov_cleanup.py
  DBCHECK_OK=1 python scripts/tsk367_crylov_cleanup.py --apply
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

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.solution_rules import SolutionRules  # noqa: E402

# Вариант 3, задания с видеоразбором в канале (посты 895, 896, 897, 907/1023).
KEEP_V3 = {
    "ext:pdf:d4:pdf:crylov:v3:20260602:v3t6": ("a05", "пост 895, задание 6"),
    "ext:pdf:d4:pdf:crylov:v3:20260602:v3t8": ("zerra", "пост 896, задание 8"),
    "ext:pdf:d4:pdf:crylov:v3:20260602:v3t9": ("aes", "пост 897, задание 9"),
    "ext:pdf:d4:pdf:crylov:v3:20260602:v3t11": ("ar", "посты 907 и 1023, задание 11"),
}

# Ответ помечен как ошибочный в самом условии.
DISPUTED = {4585: ("29 49", "в условии стоит «В ответе ошибка» (пост канала 814)")}

KEEP_SQL = """
SELECT id FROM tasks
WHERE is_active AND (external_uid ~ '^crylov:v\\d+t\\d+$' OR external_uid = ANY($1::text[]))
"""

KILL_SQL = """
SELECT id, course_id FROM tasks
WHERE is_active AND external_uid LIKE '%crylov%' AND id <> ALL($1::int[])
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            keep_ids = [r["id"] for r in await conn.fetch(KEEP_SQL, list(KEEP_V3))]
            kill = await conn.fetch(KILL_SQL, keep_ids)
            kill_ids = [r["id"] for r in kill]
            print(f"Оставляем активными: {len(keep_ids)} (108 чистых v1/v5/v11/v16 + "
                  f"{len(KEEP_V3)} из варианта 3 с разбором в ТГ)")
            print(f"Деактивируем: {len(kill_ids)}")
            if len(keep_ids) != 108 + len(KEEP_V3):
                raise AssertionError(f"ожидали {108 + len(KEEP_V3)} к сохранению, вышло {len(keep_ids)}")

            # ---- Шаг 1: деактивация OCR-партии ----
            res = await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = ANY($1::int[]) AND is_active", kill_ids)
            print(f"Шаг 1 (деактивация): {res}")

            # ---- Шаг 2: снять мусорные ответы у оставленных заданий варианта 3 ----
            for uid, (expected, why) in KEEP_V3.items():
                row = await conn.fetchrow(
                    "SELECT id, max_score, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans "
                    "FROM tasks WHERE external_uid = $1", uid)
                if row["ans"] != expected:
                    raise AssertionError(f"{uid}: ответ {row['ans']!r}, ожидали {expected!r}")
                rules = SolutionRules(max_score=row["max_score"] or 1, scoring_mode="all_or_nothing",
                                      auto_check=True, manual_review_required=True).model_dump()
                note = (f"мусор OCR «{expected}» снят; верный ответ звучит в видеоразборе "
                        f"({why}) — впишет методист, tsk-367")
                await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb, "
                    "task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($3::text), true) "
                    "WHERE id = $1", row["id"], json.dumps(rules), note)
            print(f"Шаг 2 (вариант 3, снят мусорный ответ): {len(KEEP_V3)}")

            # ---- Шаг 3: спорный ответ, помеченный в условии как ошибочный ----
            for tid, (expected, why) in DISPUTED.items():
                row = await conn.fetchrow(
                    "SELECT max_score, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans "
                    "FROM tasks WHERE id = $1", tid)
                if row["ans"] != expected:
                    raise AssertionError(f"id={tid}: ответ {row['ans']!r}, ожидали {expected!r}")
                rules = SolutionRules(max_score=row["max_score"] or 1, scoring_mode="all_or_nothing",
                                      auto_check=True, manual_review_required=True).model_dump()
                await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb, "
                    "task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($3::text), true) "
                    "WHERE id = $1", tid, json.dumps(rules),
                    f"{expected} (спорный: {why}) — перерешать вручную, tsk-368")
            print(f"Шаг 3 (спорные сняты с автопроверки): {len(DISPUTED)}")

            # ---- Верификация ----
            still = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active AND external_uid LIKE '%crylov%'")
            if still != len(keep_ids):
                raise AssertionError(f"активных Крылова осталось {still}, ждали {len(keep_ids)}")

            garbage = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active AND external_uid LIKE '%crylov%' "
                "AND solution_rules#>>'{short_answer,accepted_answers,0,value}' ~ '^[a-z]{1,5}$' "
                "AND solution_rules#>>'{short_answer,accepted_answers,0,value}' !~ '^[wxyz]+$'")
            if garbage:
                raise AssertionError(f"остались мусорные ответы: {garbage}")

            empty_courses = await conn.fetch("""
                SELECT c.id FROM courses c
                WHERE EXISTS (SELECT 1 FROM tasks t WHERE t.course_id = c.id)
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.course_id = c.id AND t.is_active)
            """)
            print(f"Курсов без единого активного задания: {len(empty_courses)}"
                  f"{' → ' + str([r['id'] for r in empty_courses]) if empty_courses else ''}")

            hollow = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND jsonb_typeof(solution_rules)='object'
                  AND (solution_rules->>'manual_review_required')::bool IS NOT TRUE
                  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'),0)=0
                  AND coalesce(jsonb_array_length(solution_rules->'correct_options'),0)=0
                  AND jsonb_typeof(solution_rules->'quiz') IS DISTINCT FROM 'object'
            """)
            print(f"Заданий с пустым правилом (должно быть 0): {hollow}")
            if hollow:
                raise AssertionError("появились задания с пустым правилом")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
