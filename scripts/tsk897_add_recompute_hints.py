# -*- coding: utf-8 -*-
"""tsk-897: добавить текстовую подсказку про пересчёт «от нового числа, не от N»
к пяти заданиям семьи «двукратное/трёхкратное дописывание бита» (курс 156).

Зачем. Разбор tsk-895/tsk-773 показал: 2149 — главный магнит заявок помощи
задания 5 (4 заявки, худший зачёт в курсе), 4129 и 2283 — тоже в списке.
Структурно все они простейшие (двоичная система, без «усложняющих» признаков
tsk-895) — перенос в «Сложные» не поможет. Реальная трудность процедурная:
ученик должен ДВАЖДЫ (2149/4129/2283/2070) или ТРИЖДЫ (2285) повторить один и
тот же шаг, каждый раз пересчитывая условие ОТ РЕЗУЛЬТАТА предыдущего шага, а
не от исходного N — и именно здесь теряются.

Материал курса (id=423, "Дописывание двух бит по сумме") уже содержит полный
разбор ровно этой ловушки с кодом и проверкой вручную — но идёт в общем потоке
из 13 материалов курса ДО всех заданий (движок LMS показывает все материалы
раньше всех заданий, а не рядом с конкретным заданием, см. tsk-689 "структурный
блокер порядка"), поэтому к моменту решения задания 2149 (восьмая по счёту
задача курса) разбор уже прочитан давно и не под рукой. Задача — дать короткую
подсказку ПРЯМО НА ЗАДАНИИ, с числами из примера, который уже есть в самом
условии (не выдумывать новый).

Эталоны НЕ трогаются. Правится только task_content.hints_text (добавляется
текстовая подсказка к уже существующим видео-подсказкам) + content_provenance
(чтобы будущий импорт из ContentBackbone не затёр правку, см. tsk-760).

Запуск (после протокола /db-check):
    DBCHECK_OK=1 python scripts/tsk897_add_recompute_hints.py --apply
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

logger = logging.getLogger("tsk897-hints")

HINTS: dict[int, str] = {
    2149: (
        "Шаг «б» — это шаг «а», применённый не к N, а к записи ПОСЛЕ шага «а» "
        "(она уже на 1 разряд длиннее). Считай сумму цифр заново — от новой "
        "записи, не от исходной. Пример: N=19 → 10011 (сумма 3, +1) → 100111 "
        "→ шаг «б»: сумма ИМЕННО 100111 = 4, +0 → 1001110 = 78."
    ),
    4129: (
        "Пункт 3 = пункт 2, но для записи ПОСЛЕ первой дописки, не для "
        "исходного N. В примере с N=13: после шага 2 получили 11011 — и на "
        "шаге 3 сумма считается от 11011 (=4), а не заново от 1101 (=3)."
    ),
    2283: (
        "Пункт 3 = пункт 2, но для записи ПОСЛЕ первой дописки, не для "
        "исходного N. В примере с N=13: после шага 2 получили 11011 — и на "
        "шаге 3 сумма считается от 11011 (=4), а не заново от 1101 (=3)."
    ),
    2070: (
        "Шаг «б» — это шаг «а», применённый к записи ПОСЛЕ шага «а», а не к "
        "исходному N. Для N=19: после шага «а» 10011 (сумма 3) стало 100111; "
        "на шаге «б» сумма считается от НОВОЙ записи 100111 (=4), а не от "
        "исходной 10011."
    ),
    2285: (
        "Шаги 2-4 — один и тот же приём, повторённый 3 раза, и каждый раз "
        "сумма считается от ДЕСЯТИЧНОЙ записи ТЕКУЩЕГО числа (уже "
        "изменённого на предыдущем шаге), не от исходного N. В примере "
        "N=17: сначала сумма цифр числа 17 → получаем 34; ЗАТЕМ сумма "
        "цифр уже числа 34 (не 17!) → получаем 69; и снова — сумма цифр "
        "числа 69 (не 17!) → получаем 139."
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
            "SELECT id, task_content, content_provenance FROM tasks WHERE id = ANY($1::int[])",
            list(HINTS.keys()),
        )
        by_id = {r["id"]: r for r in rows}

        logger.info("=== План (было -> станет) ===")
        for task_id, hint in HINTS.items():
            row = by_id[task_id]
            tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
            current_hints = tc.get("hints_text") or []
            if current_hints:
                logger.warning(
                    "id=%s: hints_text уже НЕ пуст (%r) — пропускаю, чтобы не задвоить",
                    task_id, current_hints,
                )
                continue
            logger.info("id=%s: hints_text [] -> [%r]", task_id, hint)

        if not apply:
            logger.info("Без --apply: только план. Для записи — DBCHECK_OK=1 ... --apply")
            return 0

        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        async with conn.transaction():
            for task_id, hint in HINTS.items():
                row = by_id[task_id]
                tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
                if tc.get("hints_text"):
                    continue
                tc["hints_text"] = [hint]
                tc["has_hints"] = True
                provenance = {
                    "source": "manual_script",
                    "edited_at": stamp,
                    "edited_by": "tsk-897",
                    "fields": ["task_content"],
                    "reason": "текстовая подсказка про пересчёт от результата, не от N",
                }
                await conn.execute(
                    "UPDATE tasks SET task_content=$1::jsonb, content_provenance=$2::jsonb WHERE id=$3",
                    json.dumps(tc, ensure_ascii=False), json.dumps(provenance, ensure_ascii=False), task_id,
                )

        logger.info("=== Проверка после записи ===")
        rows_after = await conn.fetch(
            "SELECT id, task_content->'hints_text' AS ht FROM tasks WHERE id = ANY($1::int[])",
            list(HINTS.keys()),
        )
        for r in rows_after:
            logger.info("id=%s hints_text=%s", r["id"], r["ht"])
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
