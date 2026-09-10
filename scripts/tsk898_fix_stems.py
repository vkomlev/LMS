# -*- coding: utf-8 -*-
"""tsk-898: правка пяти неточных формулировок условий задания 5 ЕГЭ.

Источник: tsk-895 (вычислительная проверка 108 заданий, batch-156.md /
batch-1383-C.md). Все пять эталонов подтверждены верными независимым
пересчётом — эта правка меняет ТОЛЬКО текст условия (stem), ни один
accepted_answers/correct_options не трогается.

1. id=2151, id=3289 — «повторяется пункт N» не называло число повторений.
   Перебор гипотез (1/2/3 повторения) показал: эталон сходится только при
   ДВУХ суммарных выполнениях шага. Перепроверено заново прогоном здесь же
   (не поверю прошлой проверке на слово): 2151 -> N=32 при reps=2 (при
   reps=1 -> N=128, при reps=3 -> N=16, ни то ни другое не 32); 3289 ->
   N=41 при reps=2 (reps=1 -> 84, reps=3 -> 21). Текст уточнён явным числом.
2. id=4037 — «цифра, которая встречается чаще» допускала два прочтения.
   Перепроверено: эталон 9918 воспроизводится ТОЛЬКО при прочтении «чаще
   всех остальных цифр в записи» (не «чаще, чем именно 5 и 7»).
3. id=4464 — «идут в порядке убывания» сработало только при СТРОГОМ
   прочтении (875), нестрогое даёт другой результат (999). Добавлено слово
   «строго».
4. id=2156 — единственное задание этого структурного типа без блока
   «Например…». Добавлен пример на основе самого эталона (N=79, base9
   "87" -> "8745" -> "3745" = 2795), прослеженный заново здесь же перед
   вставкой в текст.

Запуск (после протокола /db-check):
    DBCHECK_OK=1 python scripts/tsk898_fix_stems.py --apply
Без --apply — только план (показывает старый/новый stem целиком).
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

logger = logging.getLogger("tsk898-fix-stems")

# Точечные замены текста (найти -> заменить) внутри существующего stem.
# Заменяется подстрока, а не весь stem целиком — так безопаснее видеть
# в диффе именно то, что изменилось, и невозможно случайно затронуть
# соседний абзац. Якоря выбраны короткими и БЕЗ хвостовых пробелов —
# в реальном тексте (веб-импорт) встречаются неразрывные пробелы (\xa0)
# и двойные пробелы, которые не совпадают с обычным пробелом посимвольно.
REPLACEMENTS: dict[int, tuple[str, str]] = {
    2151: (
        "3) Повторяется пункт 2",
        "3) Пункт 2 повторяется ещё один раз (то есть выполняется всего дважды)",
    ),
    3289: (
        "<p>Предыдущий пункт повторяется</p>",
        "<p>Предыдущий пункт повторяется ещё один раз (то есть выполняется всего дважды)</p>",
    ),
    4037: (
        "В противном случае в конец записи добавляется цифра, которая встречается чаще.",
        "В противном случае в конец записи добавляется цифра, которая встречается в "
        "записи чаще всех остальных цифр (не только пятёрок и семёрок).",
    ),
    4464: (
        "в котором все цифры десятичной записи идут в порядке убывания.",
        "в котором все цифры десятичной записи идут в строго убывающем порядке "
        "(каждая следующая цифра меньше предыдущей, без повторов).",
    ),
    2156: (
        "<p>Полученная таким образом запись является девятеричной",
        "<p>Например, для исходного числа 79₁₀ = 87₉ (девятеричная "
        "запись не начинается на 7): справа приписывается «45», получаем "
        "8745₉, первый разряд заменяется на 3, получаем "
        "3745₉ = 2795₁₀.</p>"
        "<p>Полученная таким образом запись является девятеричной",
    ),
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


async def main_async(apply: bool) -> int:
    import asyncpg

    conn = await asyncpg.connect(load_prod_dsn_asyncpg_style().replace("+asyncpg", ""))
    try:
        rows = await conn.fetch(
            "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])",
            list(REPLACEMENTS.keys()),
        )
        by_id = {r["id"]: r for r in rows}

        logger.info("=== План (было -> станет) ===")
        new_stems: dict[int, str] = {}
        for task_id, (old_frag, new_frag) in REPLACEMENTS.items():
            row = by_id[task_id]
            tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
            stem = tc.get("stem") or ""
            if old_frag not in stem:
                logger.error(
                    "id=%s: искомый фрагмент НЕ найден в текущем stem — правка отменена "
                    "для этого задания (условие уже изменилось с момента диагностики)",
                    task_id,
                )
                continue
            if stem.count(old_frag) != 1:
                logger.error(
                    "id=%s: фрагмент встречается %d раз(а), а не 1 — небезопасно заменять "
                    "автоматически, пропускаю",
                    task_id, stem.count(old_frag),
                )
                continue
            new_stem = stem.replace(old_frag, new_frag)
            new_stems[task_id] = new_stem
            logger.info("--- id=%s ---", task_id)
            logger.info("БЫЛО:   ...%s...", old_frag)
            logger.info("СТАНЕТ: ...%s...", new_frag)

            sa = (json.loads(row["solution_rules"]) if isinstance(row["solution_rules"], str) else row["solution_rules"]).get("short_answer") or {}
            logger.info("эталон (не меняется): %s", [a.get("value") for a in (sa.get("accepted_answers") or [])])

        if not apply:
            logger.info("Без --apply: только план. Для записи — DBCHECK_OK=1 ... --apply")
            return 0

        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        async with conn.transaction():
            for task_id, new_stem in new_stems.items():
                row = by_id[task_id]
                tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
                tc["stem"] = new_stem
                prov = json.dumps(
                    {
                        "source": "manual_script",
                        "edited_at": stamp,
                        "edited_by": "tsk-898",
                        "fields": ["task_content"],
                        "reason": "уточнена формулировка условия (число повторений / "
                        "строгость / пример) — эталон не менялся, tsk-895",
                    },
                    ensure_ascii=False,
                )
                await conn.execute(
                    "UPDATE tasks SET task_content=$1::jsonb, content_provenance=$2::jsonb WHERE id=$3",
                    json.dumps(tc, ensure_ascii=False), prov, task_id,
                )

        logger.info("=== Проверка после записи ===")
        rows_after = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            list(new_stems.keys()),
        )
        for r in rows_after:
            logger.info("id=%s stem=%s", r["id"], r["stem"])
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
