# -*- coding: utf-8 -*-
"""tsk-900: перекалибровка MC-заданий онбординга + 4 точечные находки.

Источник: /naive-learner-review, `docs/qa/onboarding-review-2026-09-10/`.

Часть А (системная, P1). Посчитано по всему блоку: MC-задания («отметь всё
верное», scoring_mode=all_or_nothing) провалены в 90.9% случаев (10 из 11)
против 32.4% у SC. Причина в механике: у всех 11 MC max_score=1, а формула
partial-режима — int(max_score * num_correct / len(correct_set)) — при
max_score=1 всегда округляется вниз до 0, если выбраны не ВСЕ верные
варианты. То есть один забытый/лишний чекбокс обнуляет задание целиком,
и partial-режим БЕЗ повышения max_score тут не поможет вообще (проверено
прогоном формулы, не предположением).

Фикс: max_score -> число верных вариантов (по одному баллу за каждый
верно опознанный факт), scoring_mode -> "partial". Это чистая правка
ДАННЫХ (solution_rules), backend-код `_check_multiple_choice` уже
поддерживает partial-режим, ничего не меняем в app/.

Известная граница (не чинится в этой задаче, зафиксирована отдельно):
partial-формула считает is_correct = (base_score == max_score), что
достигается когда отмечены ВСЕ верные — ДАЖЕ если попутно отмечены лишние
неверные (штраф extra_wrong_mc применяется только при `not is_correct`,
то есть не сработает в этом случае). «Отметить всё» гарантирует полный
балл. Для низкоставочного онбординг-квиза (не экзаменационная оценка) риск
признан приемлемым; системный фикс механизма — предмет отдельной задачи.

Часть Б (точечные находки).
- id=10177 «Что можно приложить к ответу» — ХУДШИЙ результат недели (11 из
  13 мимо, 5 заявок). Спрашивал про UI развёрнутой сдачи, которого физически
  нет в этом курсе. Переведён из MC в SA: теперь спрашивает число полей на
  ОБРАЗЕ ЭКРАНА, который уже есть в материале (и назван текстом «три поля»
  в том же абзаце) — отвечаем по тому, что видно, не по памяти. Больше не
  входит в MC-батч (Часть А), max_score/scoring_mode не трогаем.
- id=10173 «Кнопка сдачи» — уточнено, что речь про задания с выбором/
  коротким ответом (в этом курсе развёрнутой сдачи и нет).
- id=10221 «Знание, умение, навык» — добавлен явный решающий признак
  («не «понял», а «могу сделать»»), сценарий лексически пересекался с
  обеими соседними категориями.
- id=10192 «Где посмотреть свои прошлые попытки» — добавлена отсылка
  «вспомни разделы 1 и 2» (факты введены в разных ранних разделах без
  моста); остаётся MC, получает и фикс Части А.

Эталоны (правильные варианты/принятые ответы) НЕ меняются нигде — только
механика (max_score/scoring_mode) и формулировки. content_provenance
проставлен на все изменённые задания.

Запуск (после протокола /db-check):
    DBCHECK_OK=1 python scripts/tsk900_fix_onboarding_mc.py --apply
Без --apply — только план.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("tsk900-fix")

# Часть А: MC-задания, получающие max_score=len(correct_options) + partial.
# (10177 сюда не входит — переезжает в SA отдельным блоком.)
MC_PARTIAL_IDS = [10171, 10181, 10187, 10192, 10196, 10202, 10211, 10214, 10216, 10226]

# Часть Б: точечные правки формулировок (stem), эталоны не меняются.
STEM_FIXES: dict[int, str] = {
    10173: (
        "Открой любое задание этого курса (они все с выбором ответа или "
        "коротким ответом, без развёрнутой сдачи) и посмотри на кнопку "
        "внизу. Как она называется? Впиши двумя словами, как написано на "
        "кнопке."
    ),
    10221: (
        "Ты разобрал тему, понял её и можешь решить задачу, если "
        "сосредоточиться и не торопиться. Решающий признак здесь — не "
        "«понял», а «могу сделать»: какая это ступень?"
    ),
    10192: (
        "Перед занятием ты хочешь вспомнить, на чём застревал всю неделю. "
        "Вспомни разделы 1 и 2 этого курса: где это видно? Отметь все "
        "верные места."
    ),
}

# id=10177: полная замена MC -> SA (эталон новый, потому что тип задания
# меняется целиком — старого числового эталона для SA не существовало).
TASK_10177_NEW = {
    "type": "SA",
    "stem": (
        "Посмотри на образ экрана развёрнутой сдачи в материале этого "
        "раздела (пример из предметного курса). Сколько там полей для "
        "ответа, включая поле «Ответ»? Впиши только число."
    ),
    "accepted": ["3", "три"],
}


def load_prod_dsn_asyncpg_style() -> str:
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


def _provenance(reason: str) -> str:
    return json.dumps(
        {
            "source": "manual_script",
            "edited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "edited_by": "tsk-900",
            "fields": ["task_content", "solution_rules"],
            "reason": reason,
        },
        ensure_ascii=False,
    )


async def main_async(apply: bool) -> int:
    import asyncpg

    conn = await asyncpg.connect(load_prod_dsn_asyncpg_style().replace("+asyncpg", ""))
    try:
        all_ids = list(set(MC_PARTIAL_IDS) | set(STEM_FIXES.keys()) | {10177})
        rows = await conn.fetch(
            "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])",
            all_ids,
        )
        by_id = {r["id"]: r for r in rows}

        logger.info("=== План (было -> станет) ===")
        for task_id in MC_PARTIAL_IDS:
            row = by_id[task_id]
            sr = json.loads(row["solution_rules"]) if isinstance(row["solution_rules"], str) else row["solution_rules"]
            n_correct = len(sr.get("correct_options") or [])
            logger.info(
                "id=%s: max_score %s -> %s, scoring_mode %s -> partial",
                task_id, sr.get("max_score"), n_correct, sr.get("scoring_mode"),
            )
        for task_id, new_stem in STEM_FIXES.items():
            logger.info("id=%s: stem уточнена (см. код)", task_id)
        logger.info("id=10177: MC -> SA, stem заменена, эталон '3'/'три' (новый тип задания)")

        if not apply:
            logger.info("Без --apply: только план. Для записи — DBCHECK_OK=1 ... --apply")
            return 0

        async with conn.transaction():
            # Часть А: MC partial
            for task_id in MC_PARTIAL_IDS:
                row = by_id[task_id]
                sr = json.loads(row["solution_rules"]) if isinstance(row["solution_rules"], str) else row["solution_rules"]
                n_correct = len(sr.get("correct_options") or [])
                sr["max_score"] = n_correct
                sr["scoring_mode"] = "partial"
                prov = _provenance("MC all_or_nothing -> partial, max_score поднят до числа верных (tsk-900)")
                await conn.execute(
                    "UPDATE tasks SET solution_rules=$1::jsonb, "
                    "max_score=$2, content_provenance=$3::jsonb WHERE id=$4",
                    json.dumps(sr, ensure_ascii=False),
                    n_correct, prov, task_id,
                )

            # Часть Б: точечные формулировки (10192 уже обработан выше для Части А —
            # здесь довносим только правку stem поверх уже обновлённого task_content)
            for task_id, new_stem in STEM_FIXES.items():
                row = await conn.fetchrow("SELECT task_content FROM tasks WHERE id=$1", task_id)
                tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
                tc["stem"] = new_stem
                prov = _provenance("уточнена формулировка stem (tsk-900, naive-learner-review)")
                await conn.execute(
                    "UPDATE tasks SET task_content=$1::jsonb, content_provenance=$2::jsonb WHERE id=$3",
                    json.dumps(tc, ensure_ascii=False), prov, task_id,
                )

            # id=10177: полная замена MC -> SA
            row = by_id[10177]
            tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
            sr = json.loads(row["solution_rules"]) if isinstance(row["solution_rules"], str) else row["solution_rules"]
            tc["type"] = TASK_10177_NEW["type"]
            tc["stem"] = TASK_10177_NEW["stem"]
            tc["options"] = None
            sr["correct_options"] = []
            sr["short_answer"] = {
                "regex": None,
                "use_regex": False,
                "normalization": ["trim", "lower"],
                "accepted_answers": [
                    {"score": 1, "value": v} for v in TASK_10177_NEW["accepted"]
                ],
            }
            sr["max_score"] = 1
            sr["scoring_mode"] = "all_or_nothing"
            prov = _provenance("переведено MC -> SA: вопрос про UI, отсутствующий в курсе, заменён на вопрос по картинке в материале (tsk-900)")
            await conn.execute(
                "UPDATE tasks SET task_content=$1::jsonb, solution_rules=$2::jsonb, "
                "max_score=1, content_provenance=$3::jsonb WHERE id=$4",
                json.dumps(tc, ensure_ascii=False), json.dumps(sr, ensure_ascii=False), prov, 10177,
            )

        logger.info("=== Проверка после записи ===")
        rows_after = await conn.fetch(
            "SELECT id, task_content->>'type' AS type, solution_rules->>'max_score' AS max_score, "
            "solution_rules->>'scoring_mode' AS scoring_mode, task_content->>'stem' AS stem "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            all_ids,
        )
        for r in rows_after:
            logger.info(dict(r))
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись (иначе только план)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    if sys.platform == "win32":
        import os
        os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
