# -*- coding: utf-8 -*-
"""tsk-100 (F2): записать извлечённые из Яндекс.Учебника ответы в solution_rules.

ЧТО ДЕЛАЕТ
280 заданий ЕГЭ с source_kind='yandex' имеют solution_rules = JSON null и не
имеют answer_raw (в отличие от 790 заданий F1/tsk-325, где ответ уже лежал в
task_content.answer_raw). Верный ответ извлечён отдельно: через авторизованный
API Яндекса `POST /api/v5/gpttr` тип `get_task_by_id` (редактор подборки отдаёт
поле ответа под учёткой). Извлечённый маппинг {task_id: answer} сохранён в
JSON-файле и подаётся сюда параметром --answers.

Скрипт собирает из ответа правило автопроверки короткого ответа (SA_COM) и кладёт
его в solution_rules — тем же способом и в той же форме, что и tsk-325, чтобы
приём ответа проходил автопроверку. Множества заданий не пересекаются: tsk-325
пишет kompege/polyakov/sdamgia, здесь — только yandex.

КЛАССИФИКАЦИЯ ОТВЕТА (та же ловушка, что в tsk-325)
Ответ нельзя переносить слепо. Три класса (по данным, без хардкода id):
  1. Чистые — число, группа чисел через пробел, буквенный токен. Авто с текстовой
     нормализацией.
  2. Чинибельный артефакт — ведущий «— »/«Ответ:» снимается.
  3. Неоднозначные — многочастный «1) .. 2) ..» или проза → ручная проверка
     (manual_review_required=true), НЕ в accepted_answers. Слепой перенос сделал
     бы задание «всегда неверно» — хуже ручной деградации.

Нормализация: текстовая ["trim","lower"] — как у tsk-325 и доминирующая у уже
работающих SA_COM. Все yandex-задания — SA_COM, answer_is_code=null, поэтому
code_ast не нужен.

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE трогает только задания с solution_rules ещё null (WHERE-guard) и только из
целевой выборки (переданный маппинг ∩ source_kind='yandex'). Обратимо (вернуть в
null). Задания с уже заведённым правилом не затрагиваются.

Запуск: dry-run по умолчанию (транзакция откатывается);
  python scripts/backfill_solution_rules_yandex_tsk100.py --answers <file.json>
  DBCHECK_OK=1 python scripts/backfill_solution_rules_yandex_tsk100.py --answers <file.json> --apply
"""
from __future__ import annotations

import argparse
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

# Ведущий «Ответ:», «— », «–» — артефакты извлечения из UI/скрейпа.
_ANS_PREFIX = re.compile(r"^\s*(ответ|answer)\s*[:：]\s*", re.IGNORECASE)
_DASH_PREFIX = re.compile(r"^[–—]\s*")
# Многочастный ответ «1) … 2) …» — не единый короткий ответ.
_MULTIPART = re.compile(r"\d\)")
# Две+ кириллических слова подряд — проза/мусор.
_PROSE = re.compile(r"[а-яё]+\s+[а-яё]+", re.IGNORECASE)

# Только yandex и только те id, что пришли в маппинге.
SELECT_TARGETS = """
SELECT t.id,
       t.max_score,
       t.task_content->>'source_kind'    AS src,
       t.task_content->>'source_task_id' AS uuid
FROM tasks t
WHERE t.is_active
  AND (t.solution_rules IS NULL OR jsonb_typeof(t.solution_rules) = 'null')
  AND t.task_content->>'source_kind' = 'yandex'
  AND t.id = ANY($1::int[])
ORDER BY t.id
"""

UPDATE_ONE = """
UPDATE tasks
SET solution_rules = $2::jsonb
WHERE id = $1
  AND (solution_rules IS NULL OR jsonb_typeof(solution_rules) = 'null')
  AND task_content->>'source_kind' = 'yandex'
"""


def clean_answer(raw: str) -> str:
    """Снять ведущий «Ответ:»/«— », обрезать края, схлопнуть пробелы."""
    s = _ANS_PREFIX.sub("", raw)
    s = _DASH_PREFIX.sub("", s)
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


def build_rules(answer: str, max_score: Optional[int]) -> Tuple[dict, str]:
    """Собрать solution_rules из ответа. Возвращает (json-dict, метка маршрута)."""
    ms = max_score if (max_score or 0) > 0 else 1
    cleaned = clean_answer(answer)

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
    """Прод-DSN для learn. Из окружения или из .mcp.json (learn_prod_db)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
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


def build_manual_rules(max_score: Optional[int]) -> dict:
    """Собрать solution_rules с обязательной ручной проверкой (без accepted_answers).

    Используется для table_match заданий Яндекса (ЕГЭ 17/18/25 и т.п.): верный
    ответ — таблица ячеек (напр. [["805","-1028"]] или многострочная), а не единый
    короткий ответ. Слепой перенос как строки сделал бы задание «всегда неверно»
    (см. tsk-325). Сырой табличный ответ сохранён в audit-файле маппинга.
    """
    ms = max_score if (max_score or 0) > 0 else 1
    rules = SolutionRules(
        max_score=ms,
        scoring_mode="all_or_nothing",
        auto_check=True,
        manual_review_required=True,
    )
    return rules.model_dump()


def load_maps(path: Path) -> Tuple[dict[int, str], dict[int, str]]:
    """Прочитать {textMap, tableMap}. Возвращает (text{id:answer}, table{id:raw})."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    text_map: dict[int, str] = {}
    for k, v in (raw.get("textMap") or {}).items():
        if v is None:
            continue
        val = str(v).strip()
        if val:
            text_map[int(k)] = val
    table_map: dict[int, str] = {}
    for k, v in (raw.get("tableMap") or {}).items():
        table_map[int(k)] = str(v)
    return text_map, table_map


async def main(answers_path: Path, apply: bool) -> None:
    text_map, table_map = load_maps(answers_path)
    target_ids = sorted(set(text_map) | set(table_map))
    print(f"В маппинге: text={len(text_map)}, table={len(table_map)}, всего={len(target_ids)}")
    if not target_ids:
        raise RuntimeError("маппинг пуст")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_TARGETS, target_ids)
            total = len(rows)
            print(f"Целевых заданий (yandex, sr=null, есть в маппинге): {total}")
            skipped = set(target_ids) - {r["id"] for r in rows}
            if skipped:
                print(f"  Пропущены (не yandex / уже с правилом / нет в БД): {len(skipped)} "
                      f"→ {sorted(skipped)[:20]}{'...' if len(skipped) > 20 else ''}")
            if total == 0:
                raise RuntimeError("кандидатов нет — возможно, уже применено")

            by_route = {"auto": 0, "manual": 0}
            samples = []
            payloads: dict[int, str] = {}
            for r in rows:
                tid = r["id"]
                if tid in text_map:
                    payload, route = build_rules(text_map[tid], r["max_score"])
                    ans = text_map[tid]
                else:
                    payload, route = build_manual_rules(r["max_score"]), "manual"
                    ans = f"table:{table_map.get(tid, '')[:40]}"
                payloads[tid] = json.dumps(payload)
                by_route[route] += 1
                if len(samples) < 20 or route == "manual":
                    accepted = None
                    if route == "auto":
                        accepted = payload["short_answer"]["accepted_answers"][0]["value"]
                    samples.append((tid, route, ans, accepted))

            print(f"Маршрут: auto (accepted_answers) = {by_route['auto']}, "
                  f"manual (ручная проверка) = {by_route['manual']}")
            print("\nПримеры (answer → маршрут → accepted):")
            for tid, route, ans, accepted in samples[:30]:
                after = f"accepted='{accepted}'" if route == "auto" else "manual_review_required=true"
                print(f"  [{route:6}] id={tid} '{ans}' → {after}")

            written_ids = list(payloads.keys())
            null_before = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')"
            )
            print(f"\nВсего заданий с solution_rules=null ДО: {null_before}")

            updated = 0
            for tid, pj in payloads.items():
                res = await conn.execute(UPDATE_ONE, tid, pj)
                updated += int(res.split()[-1])

            # ---- Верификация внутри транзакции (независимым чтением) ----
            if updated != total:
                raise AssertionError(f"ожидали обновить {total}, обновлено {updated}")

            still_null = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')",
                written_ids,
            )
            if still_null != 0:
                raise AssertionError(f"после записи остались null в выборке: {still_null}")

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

            auto_ok = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND jsonb_array_length(COALESCE(solution_rules#>'{short_answer,accepted_answers}','[]'::jsonb)) = 1",
                written_ids,
            )
            manual_ok = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'manual_review_required')::bool IS TRUE "
                "AND jsonb_typeof(solution_rules->'short_answer') = 'null'",
                written_ids,
            )
            print(f"Проверка: auto с 1 accepted = {auto_ok} (ждём {by_route['auto']}); "
                  f"manual без short_answer = {manual_ok} (ждём {by_route['manual']})")
            if auto_ok != by_route["auto"]:
                raise AssertionError(f"auto accepted mismatch: {auto_ok} != {by_route['auto']}")
            if manual_ok != by_route["manual"]:
                raise AssertionError(f"manual mismatch: {manual_ok} != {by_route['manual']}")

            ms_mismatch = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'max_score')::int IS DISTINCT FROM max_score",
                written_ids,
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True, help="JSON-файл {task_id: answer}")
    ap.add_argument("--apply", action="store_true", help="реально записать (нужен DBCHECK_OK=1)")
    args = ap.parse_args()
    try:
        asyncio.run(main(Path(args.answers), args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
