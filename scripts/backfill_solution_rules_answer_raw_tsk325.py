# -*- coding: utf-8 -*-
"""tsk-325 (F1): перенести известный ответ answer_raw → solution_rules у заданий ЕГЭ.

ЧТО ДЕЛАЕТ
790 заданий (КомпЕГЭ 496 / Поляков 251 / РешуЕГЭ 43) имеют solution_rules = JSON
null, хотя верный ответ уже лежит в task_content.answer_raw (аудит tsk-299).
Скрипт собирает из answer_raw правило автопроверки короткого ответа (SA_COM) и
кладёт его в solution_rules. После этого приём ответа по заданию проходит
автопроверку вместо падения (падение чинит F5, это — данные).

ПОЧЕМУ answer_raw НЕЛЬЗЯ ПЕРЕНОСИТЬ СЛЕПО (главная ловушка задачи)
Разбор реального формата answer_raw на проде (все 790 — SA_COM, answer_is_code=null,
max_score=1, читано через MCP read-only) показал три класса:
  1. Чистые ответы — число (410+205+17), группа чисел через пробел «8433 5»
     (70+35+9), буквенный токен «АДВБГ»/«ywxz»/«Петя»/«C38412»/«ВЕРХ 1743».
     Переносятся как есть с текстовой нормализацией.
  2. Чинибельный артефакт скрейпа — префикс «— » у 13 заданий РешуЕГЭ
     («— 469784 511»). Ведущий em/en-dash со скрейпа снимается → «469784 511».
  3. Неоднозначные — многочастный ответ Полякова «1) 28 2) 16 18 3) 11» (8 шт,
     три подвопроса в одной строке) и мусор РешуЕГЭ («на первый вопрос …»,
     «номер дома … Ответ: …»). Слепой перенос сделал бы задание «всегда неверно»
     (ни один ввод ученика не совпал бы) — ХУЖЕ, чем ручная деградация F5.
     Такие задания уходят в ручную проверку (manual_review_required=true), а не в
     accepted_answers. Классификация — по данным (regex-признаки), без хардкода id.

Нормализация: текстовая ["trim","lower"] — доминирующая у уже работающих SA_COM
(813 заданий на проде) и минимальная (без ложных зачётов). Все 790 —
answer_is_code=null, поэтому code_ast здесь не нужен (см. tsk-262 — он для Python).

Правило сериализуется через саму схему app.schemas.solution_rules.SolutionRules —
хранимый JSON гарантированно валиден и совпадает по форме с рабочими заданиями.

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE трогает только задания, у которых solution_rules ещё null (WHERE-guard),
и только из целевой выборки (790). Задания с уже заведённым правилом не
затрагиваются. 0 попыток учеников по этим заданиям (task_results) — низкий риск.
Обратимо: solution_rules можно вернуть в null.

Запуск: dry-run по умолчанию (транзакция откатывается); --apply — запись
(нужен DBCHECK_OK=1 и go оператора, прод-хост 5.42.107.253).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

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

# Ведущий em/en-dash с пробелом — артефакт скрейпа РешуЕГЭ («— 469784 511»).
_DASH_PREFIX = re.compile(r"^[–—]\s*")
# Многочастный ответ «1) … 2) … 3) …» — не единый короткий ответ.
_MULTIPART = re.compile(r"\d\)")
# Две+ кириллических слова подряд — проза/мусор («на первый вопрос», «номер дома»).
# «ВЕРХ 1743» не ловится (после слова — цифры, не слово) и остаётся авто-ответом.
_PROSE = re.compile(r"[а-яё]+\s+[а-яё]+", re.IGNORECASE)

SELECT_TARGETS = """
SELECT t.id,
       t.max_score,
       t.task_content->>'source_kind' AS src,
       t.task_content->>'answer_raw'  AS answer_raw
FROM tasks t
WHERE t.is_active
  AND (t.solution_rules IS NULL OR jsonb_typeof(t.solution_rules) = 'null')
  AND t.task_content->>'source_kind' IN ('kompege', 'polyakov', 'sdamgia')
  AND btrim(COALESCE(t.task_content->>'answer_raw', '')) <> ''
ORDER BY t.id
"""

# Идемпотентно: пишем только там, где правило ещё пустое.
UPDATE_ONE = """
UPDATE tasks
SET solution_rules = $2::jsonb
WHERE id = $1
  AND (solution_rules IS NULL OR jsonb_typeof(solution_rules) = 'null')
"""


def clean_answer(raw: str) -> str:
    """Снять артефакт скрейпа (ведущий «— »), обрезать края, схлопнуть пробелы."""
    s = _DASH_PREFIX.sub("", raw)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def route_to_manual(cleaned: str) -> bool:
    """Ответ неоднозначен для автопроверки → в ручную (не в accepted_answers)."""
    if not cleaned:
        return True
    if _MULTIPART.search(cleaned):
        return True
    if _PROSE.search(cleaned):
        return True
    return False


def build_rules(answer_raw: str, max_score: Optional[int]) -> Tuple[dict, str]:
    """Собрать solution_rules из answer_raw. Возвращает (json-dict, метка маршрута).

    Метка: 'auto' — заведён accepted_answers; 'manual' — ушло в ручную проверку.
    """
    ms = max_score if (max_score or 0) > 0 else 1
    cleaned = clean_answer(answer_raw)

    if route_to_manual(cleaned):
        rules = SolutionRules(
            max_score=ms,
            scoring_mode="all_or_nothing",
            auto_check=True,
            manual_review_required=True,
        )
        return rules.model_dump(), "manual"

    rules = SolutionRules(
        max_score=ms,
        scoring_mode="all_or_nothing",
        auto_check=True,
        manual_review_required=False,
        short_answer=ShortAnswerRules(
            normalization=["trim", "lower"],
            accepted_answers=[ShortAnswerAccepted(value=cleaned, score=ms)],
        ),
    )
    return rules.model_dump(), "auto"


def _dsn() -> str:
    """Прод-DSN для learn. Берём из окружения или из .mcp.json (learn_prod_db)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        # Фолбэк: читаем DSN сервера learn_prod_db из .mcp.json (без пароля в коде).
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_TARGETS)
            total = len(rows)
            print(f"Целевых заданий (kompege/polyakov/sdamgia, sr=null, answer_raw есть): {total}")
            if total == 0:
                raise RuntimeError("кандидатов нет — возможно, уже применено")

            by_route = {"auto": 0, "manual": 0}
            by_src = {}
            samples = []
            payloads: dict[int, str] = {}
            for r in rows:
                payload, route = build_rules(r["answer_raw"], r["max_score"])
                payloads[r["id"]] = json.dumps(payload)
                by_route[route] += 1
                by_src.setdefault(r["src"], {"auto": 0, "manual": 0})[route] += 1
                if len(samples) < 15 or route == "manual":
                    accepted = None
                    if route == "auto":
                        accepted = payload["short_answer"]["accepted_answers"][0]["value"]
                    samples.append((r["id"], r["src"], route, r["answer_raw"], accepted))

            print(f"Маршрут: auto (accepted_answers) = {by_route['auto']}, "
                  f"manual (ручная проверка) = {by_route['manual']}")
            print(f"По источникам: {by_src}")
            print("\nПримеры до/после (answer_raw → маршрут → accepted):")
            for tid, src, route, raw, accepted in samples[:25]:
                after = f"accepted='{accepted}'" if route == "auto" else "manual_review_required=true"
                print(f"  [{route:6}] id={tid} {src:8} '{raw}' → {after}")

            target_ids = list(payloads.keys())
            null_before = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')"
            )
            print(f"\nВсего заданий с solution_rules=null ДО: {null_before}")

            updated = 0
            for tid, pj in payloads.items():
                res = await conn.execute(UPDATE_ONE, tid, pj)
                # res вида 'UPDATE 1'
                updated += int(res.split()[-1])

            # ---- Верификация внутри транзакции (независимым чтением) ----
            if updated != total:
                raise AssertionError(f"ожидали обновить {total}, обновлено {updated}")

            still_null_in_targets = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')",
                target_ids,
            )
            if still_null_in_targets != 0:
                raise AssertionError(f"после записи остались null в выборке: {still_null_in_targets}")

            null_after = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')"
            )
            print(f"Всего заданий с solution_rules=null ПОСЛЕ: {null_after} "
                  f"(снижение на {null_before - null_after})")
            if null_before - null_after != total:
                raise AssertionError(
                    f"снижение null ({null_before - null_after}) != числу целей ({total}) — "
                    "задеты задания вне выборки?"
                )

            # accepted_answers реально проставлены у auto-заданий.
            auto_ok = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND jsonb_array_length(COALESCE(solution_rules#>'{short_answer,accepted_answers}','[]'::jsonb)) = 1",
                target_ids,
            )
            manual_ok = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'manual_review_required')::bool IS TRUE "
                # short_answer у ручных заданий — JSON null (не SQL NULL): ключ есть,
                # значение null. SQL `IS NULL` для JSON-null ложно, проверяем typeof.
                "AND jsonb_typeof(solution_rules->'short_answer') = 'null'",
                target_ids,
            )
            print(f"Проверка: auto с 1 accepted = {auto_ok} (ждём {by_route['auto']}); "
                  f"manual без short_answer = {manual_ok} (ждём {by_route['manual']})")
            if auto_ok != by_route["auto"]:
                raise AssertionError(f"auto accepted mismatch: {auto_ok} != {by_route['auto']}")
            if manual_ok != by_route["manual"]:
                raise AssertionError(f"manual mismatch: {manual_ok} != {by_route['manual']}")

            # max_score правила совпадает с tasks.max_score у всех целей.
            ms_mismatch = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'max_score')::int IS DISTINCT FROM max_score",
                target_ids,
            )
            if ms_mismatch != 0:
                raise AssertionError(f"max_score расходится с задачей у {ms_mismatch} заданий")

            print("\nOK: перенос выполнен, коллатералей нет, правила валидны.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
