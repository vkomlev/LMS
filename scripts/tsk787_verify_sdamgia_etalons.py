# -*- coding: utf-8 -*-
"""tsk-787: сверка эталонов партии sdamgia с чистым ответом источника (read-only).

Зачем. Эталоны этой партии импортированы разбором HTML: ответ вытягивался регуляркой
из текста решения (`div.solution`), и туда попадала пунктуация фразы — «получим ответ
 — 2640.» давало эталон «— 2640». Но в том же сыром HTML есть ВТОРОЕ, чистое место с
ответом: блок `div.answer` вида «Ответ: 2640». Он записан автором задачи и разбором не
трогался, поэтому годится как независимая сверка: расходится эталон с ним — значит
разбор что-то потерял или добавил.

Почему это не «пересчёт по условию», а сверка. Пересчёт (открыть приложенный файл,
выполнить алгоритм из условия) — сильнее, но выполним не для всех заданий и стоит
дорого. Сверка с `div.answer` покрывает партию целиком и ловит именно дефекты РАЗБОРА,
а не ошибки самого источника. Для задания 2223 пересчёт по файлу уже сделан вручную и
дал 2640 — ровно то, что стоит в `div.answer`, так что источнику можно верить.

Что делает. Читает эталоны LMS и чистые ответы из `external_tasks.task.payload_data`
ContentBackbone, сверяет по составу значений и печатает расхождения. Ни одного UPDATE.

Запуск::

    python scripts/tsk787_verify_sdamgia_etalons.py
    python scripts/tsk787_verify_sdamgia_etalons.py --quiet   # только расхождения
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk787-verify")

#: `div.answer` у sdamgia разделяет несколько значений символом «&» (в HTML — `&amp;`),
#: эталон LMS — пробелом. Для сверки и то и другое разбивается на части.
SPLIT_PARTS = re.compile(r"[&\s,;]+")
#: Ведущий мусор, который сверка не считает расхождением: он уже вычищен из LMS
#: (tsk-787) и мог остаться в старых снимках.
GARBAGE_LEAD = re.compile(r"^(?:[\s—–:;,]|[-−](?=\s))+")


def normalize(value: str) -> tuple[str, ...]:
    """Значение как набор частей, сравнимый между источником и LMS."""
    text = html.unescape(value or "")
    text = GARBAGE_LEAD.sub("", text).strip().rstrip(".").strip()
    return tuple(part for part in SPLIT_PARTS.split(text.lower()) if part)


def dsn_from_mcp(alias: str) -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="печатать только расхождения")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    # Чистые ответы источника.
    cb = psycopg2.connect(dsn_from_mcp("content_backbone_prod_db"))
    try:
        cur = cb.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(
            """
            SELECT external_uid,
                   substring(payload_data->'task_content'->>'stem'
                             from 'class="answer"><span>Ответ:([^<]{0,200})') AS clean
            FROM external_tasks.task
            WHERE source LIKE 'd4:sdamgia%'
            """
        )
        source = {r["external_uid"]: r["clean"] for r in cur.fetchall() if r["clean"]}
    finally:
        cb.close()
    logger.info("Чистых ответов в источнике (div.answer): %d", len(source))

    # Эталоны LMS.
    lms = psycopg2.connect(dsn_from_mcp("learn_prod_db"))
    try:
        cur = lms.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(
            """
            SELECT t.id, t.external_uid, t.is_active, t.task_content->>'title' AS title,
                   ae.val->>'value' AS value
            FROM tasks t
            CROSS JOIN LATERAL jsonb_array_elements(
                     t.solution_rules #> '{short_answer,accepted_answers}') AS ae(val)
            WHERE t.external_uid LIKE 'ext:%sdamgia%'
              AND jsonb_typeof(t.solution_rules) = 'object'
              AND jsonb_typeof(t.solution_rules #> '{short_answer,accepted_answers}') = 'array'
            ORDER BY t.id
            """
        )
        rows = cur.fetchall()
    finally:
        lms.close()
    logger.info("Эталонов sdamgia в LMS: %d", len(rows))

    matched = 0
    mismatch: list[tuple[int, str, str, str, bool]] = []
    no_source: list[int] = []

    for row in rows:
        # Ключ источника отличается партией («20260602» против «calib:20260525»),
        # общего у них только ID задачи на sdamgia — по нему и сводим.
        task_id_on_source = row["external_uid"].rsplit(":", 1)[-1]
        clean = next((v for uid, v in source.items()
                      if uid.rsplit(":", 1)[-1] == task_id_on_source), None)
        if clean is None:
            no_source.append(row["id"])
            continue
        if normalize(row["value"]) == normalize(clean):
            matched += 1
        else:
            mismatch.append((row["id"], row["external_uid"], row["value"],
                             clean.strip(), row["is_active"]))

    logger.info("Сошлись с источником: %d", matched)
    if no_source:
        logger.info("Нет чистого ответа в источнике (сверить нечем): %d — %s",
                    len(no_source), no_source[:20])
    if mismatch:
        logger.info("РАСХОДЯТСЯ с источником: %d", len(mismatch))
        for tid, uid, value, clean, active in mismatch:
            logger.info("  [%s]%s %s: эталон %r, источник %r",
                        tid, "" if active else " (отключено)", uid, value, clean)
        return 1
    if not args.quiet:
        logger.info("OK: все сверенные эталоны совпали с чистым ответом источника.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
