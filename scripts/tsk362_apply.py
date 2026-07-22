# -*- coding: utf-8 -*-
"""tsk-362, шаг 4: записать правила проверки по плану (авто-ответ / ручная проверка).

ЧТО ДЕЛАЕТ
Берёт план шага 3 и переписывает `solution_rules` у активных заданий с «пустым правилом»:
  * `auto`   — `short_answer.accepted_answers = [{value, score}]`, нормализация
               `["trim","lower"]`, `manual_review_required=false` (механика [[tsk-325]]/[[tsk-100]]);
  * `manual` — `manual_review_required=true` без `accepted_answers`. Так задание попадает
               в ОБЯЗАТЕЛЬНУЮ очередь преподавателя: сейчас (правило пустое) движок
               возвращает `is_correct=None` и балл 0, а в обязательную очередь задание
               не попадает — ответ ученика зависает непроверенным навсегда.
  * `skip`   — не трогаем (опросники-профилировщики, у них верного ответа нет).

ГАРАНТИИ
* WHERE-guard повторяет предикат «пустого правила»: задание, у которого правило уже
  завели (руками или параллельной задачей), не перезаписывается — скрипт идемпотентен
  и не может затереть чужую работу.
* Всё в одной транзакции, с верификацией внутри неё: число обновлённых строк совпадает
  с планом, у auto ровно один `accepted_answers`, у manual флаг стоит и `short_answer`
  пуст, `max_score` правила равен `tasks.max_score`, за пределами плана ничего не задето.
* Обратимо: вернуть `solution_rules` в прежний вид можно из бэкапа, снимаемого в
  `--backup` перед записью (id → старое правило).

Запуск: dry-run по умолчанию (транзакция откатывается);
  python scripts/tsk362_apply.py --plan plan.json --backup backup.json
  DBCHECK_OK=1 python scripts/tsk362_apply.py --plan plan.json --backup backup.json --apply
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

from app.schemas.solution_rules import (  # noqa: E402
    SolutionRules,
    ShortAnswerRules,
    ShortAnswerAccepted,
)

HOLLOW_PREDICATE = """
  AND is_active
  AND jsonb_typeof(solution_rules) = 'object'
  AND (solution_rules->>'manual_review_required')::bool IS NOT TRUE
  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'), 0) = 0
  AND coalesce(jsonb_array_length(solution_rules->'correct_options'), 0) = 0
  AND coalesce(solution_rules->>'text_answer', '') = ''
  AND solution_rules->'custom_scoring_config' IS NOT DISTINCT FROM 'null'::jsonb
  AND jsonb_typeof(solution_rules->'quiz') IS DISTINCT FROM 'object'
"""

UPDATE_ONE = f"UPDATE tasks SET solution_rules = $2::jsonb WHERE id = $1 {HOLLOW_PREDICATE}"

COUNT_HOLLOW = f"SELECT count(*) FROM tasks WHERE true {HOLLOW_PREDICATE}"


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


def rules_auto(value: str, max_score: int | None) -> dict:
    ms = max_score if (max_score or 0) > 0 else 1
    return SolutionRules(
        max_score=ms,
        scoring_mode="all_or_nothing",
        auto_check=True,
        manual_review_required=False,
        short_answer=ShortAnswerRules(
            normalization=["trim", "lower"],
            accepted_answers=[ShortAnswerAccepted(value=value, score=ms)],
        ),
    ).model_dump()


def rules_manual(max_score: int | None) -> dict:
    ms = max_score if (max_score or 0) > 0 else 1
    return SolutionRules(
        max_score=ms,
        scoring_mode="all_or_nothing",
        auto_check=True,
        manual_review_required=True,
    ).model_dump()


async def main(plan_path: Path, backup_path: Path, apply: bool) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    auto = {a["id"]: a for a in plan["auto"]}
    manual = {m["id"]: m for m in plan["manual"]}
    print(f"План: авто={len(auto)}, ручная проверка={len(manual)}, не трогаем={len(plan['skip'])}")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            target_ids = sorted(set(auto) | set(manual))
            rows = await conn.fetch(
                f"SELECT id, max_score, solution_rules FROM tasks WHERE id = ANY($1::int[]) {HOLLOW_PREDICATE}",
                target_ids,
            )
            found = {r["id"]: r for r in rows}
            missing = sorted(set(target_ids) - set(found))
            if missing:
                print(f"  Пропущены (правило уже заведено / не активны): {len(missing)} → {missing[:15]}")

            backup_path.write_text(json.dumps(
                {str(r["id"]): json.loads(r["solution_rules"]) for r in rows},
                ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  Бэкап прежних правил: {backup_path} ({len(rows)} строк)")

            hollow_before = await conn.fetchval(COUNT_HOLLOW)
            print(f"Заданий с пустым правилом ДО: {hollow_before}")

            n_auto = n_manual = 0
            for tid, row in found.items():
                if tid in auto:
                    payload = rules_auto(auto[tid]["value"], row["max_score"])
                    n_auto += 1
                else:
                    payload = rules_manual(row["max_score"])
                    n_manual += 1
                res = await conn.execute(UPDATE_ONE, tid, json.dumps(payload))
                if int(res.split()[-1]) != 1:
                    raise AssertionError(f"id={tid}: обновлено {res}, ожидали 1")

            print(f"Записано: авто={n_auto}, ручная проверка={n_manual}")

            # ---- Верификация внутри транзакции, независимым чтением ----
            ids_auto = [i for i in found if i in auto]
            ids_manual = [i for i in found if i in manual]

            ok_auto = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}') = 1 "
                "AND (solution_rules->>'manual_review_required')::bool IS FALSE",
                ids_auto)
            if ok_auto != len(ids_auto):
                raise AssertionError(f"auto: {ok_auto} из {len(ids_auto)}")

            ok_manual = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'manual_review_required')::bool IS TRUE "
                "AND jsonb_typeof(solution_rules->'short_answer') = 'null'",
                ids_manual)
            if ok_manual != len(ids_manual):
                raise AssertionError(f"manual: {ok_manual} из {len(ids_manual)}")

            # Значение ответа совпадает с планом — построчно, не агрегатом (урок tsk-317).
            wrong = await conn.fetch(
                "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS v "
                "FROM tasks WHERE id = ANY($1::int[])", ids_auto)
            bad = [(r["id"], r["v"]) for r in wrong if r["v"] != auto[r["id"]]["value"]]
            if bad:
                raise AssertionError(f"значение ответа разошлось с планом у {len(bad)}: {bad[:5]}")

            ms_mismatch = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'max_score')::int IS DISTINCT FROM max_score", target_ids)
            if ms_mismatch:
                raise AssertionError(f"max_score расходится у {ms_mismatch}")

            hollow_after = await conn.fetchval(COUNT_HOLLOW)
            print(f"Заданий с пустым правилом ПОСЛЕ: {hollow_after} "
                  f"(снижение на {hollow_before - hollow_after})")
            if hollow_before - hollow_after != len(found):
                raise AssertionError(
                    f"снижение ({hollow_before - hollow_after}) != числу целей ({len(found)}) — "
                    "задеты задания вне плана?")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(Path(args.plan), Path(args.backup), args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
