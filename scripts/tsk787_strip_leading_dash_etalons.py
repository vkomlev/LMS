# -*- coding: utf-8 -*-
"""tsk-787: снять мусорное ведущее тире с эталонов коротких ответов.

Что за мусор. У части заданий партии sdamgia эталон записан как ``«— 2640»`` —
на экране ученика и преподавателя это читается как ОТРИЦАТЕЛЬНОЕ количество
строк, то есть бессмыслица. Приёму ответа тире не мешает (нормализация
``strip_punctuation`` превращает знак в пробел, ``collapse_spaces`` схлопывает),
и именно поэтому дефект невидим для тестов: верный ответ проходит. Цена другая —
преподаватель, увидев бессмыслицу, решает, что эталон битый, и верит ученику.
03.09 по заданию 2223 так зачли неверный ответ 563 при верном 2640.

Откуда мусор. Парсер ``monolith/external_tasks/parsers/html/sdamgia.py``
(ContentBackbone) берёт первый подходящий блок из ``_ANSWER_SELECTORS``, где
``div.solution`` стоит РАНЬШЕ чистого ``div.answer``, и вытягивает ответ
регуляркой ``\\bответ\\s*:?\\s*([^\\.;]+)`` из текста решения. У sdamgia текст
решения кончается фразой «...получим ответ  — 2640.», так что в группу попадает
тире — пунктуация фразы, а не знак числа.

Что правит скрипт. Только ведущее ДЛИННОЕ тире (U+2014) и среднее (U+2013) с
последующими пробелами. Дефис ``-`` и типографский минус ``−`` (U+2212) НЕ
трогаются: это законный знак отрицательного ответа (``-8`` «Округление числа
вниз», ``−392`` «Минимальная сумма пути ладьи»), и снять его значило бы испортить
верный эталон. Само значение не пересчитывается — оно уже сверено с чистым
``div.answer`` того же сырого HTML (33 из 33 совпали).

Запуск::

    python scripts/tsk787_strip_leading_dash_etalons.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk787_strip_leading_dash_etalons.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk787")

#: Ведущий мусор: длинное/среднее тире и пробелы за ним. Дефис и U+2212 сюда
#: намеренно не входят — см. модульную строку документации.
GARBAGE_LEAD = re.compile(r"^[\s—–]+")

#: Остаток после снятия мусора обязан начинаться с цифры, буквы или скобки.
#: Иначе мы имеем дело не с «тире перед числом», а с чем-то ещё, и правка вслепую
#: могла бы превратить эталон в другой мусор.
SANE_TAIL = re.compile(r"^[0-9A-Za-zА-Яа-яЁё(\[{]")


def clean(value: str) -> str | None:
    """Значение без ведущего мусорного тире; ``None``, если правка не нужна.

    :raises ValueError: остаток не похож на осмысленный ответ.
    """
    stripped = GARBAGE_LEAD.sub("", value)
    if stripped == value:
        return None
    if not stripped or not SANE_TAIL.match(stripped):
        raise ValueError(f"после снятия тире осталось {stripped!r} — не правим")
    return stripped


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только показать)")
    parser.add_argument("--include-inactive", action="store_true",
                        help=("захватить и отключённые задания. Нужно, потому что именно "
                              "неполная чистка и даёт рецидив: tsk-687 в августе вычистил "
                              "мусор в одном задании из 33, и класс вернулся. Отключённые "
                              "дубли (партия ext:calib:) ученику не видны, но при "
                              "активации вернут тот же мусор на экран"))
    parser.add_argument("--backup-dir", default="reviews")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    conn = psycopg2.connect(dsn_from_mcp())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        """
        SELECT id, external_uid, task_content->>'title' AS title, is_active, solution_rules
        FROM tasks
        WHERE (is_active OR %s)
          AND jsonb_typeof(solution_rules) = 'object'
          AND jsonb_typeof(solution_rules #> '{short_answer,accepted_answers}') = 'array'
        ORDER BY id
        """,
        (args.include_inactive,),
    )
    rows = cur.fetchall()
    logger.info("Заданий с эталонами короткого ответа в охвате: %d (отключённые %s)",
                len(rows), "включены" if args.include_inactive else "пропущены")

    # (task_id, индекс эталона, было, стало)
    planned: list[tuple[int, int, str, str]] = []
    backup: list[dict[str, Any]] = []
    refused: list[tuple[int, str, str]] = []

    for row in rows:
        rules = row["solution_rules"] or {}
        accepted = (rules.get("short_answer") or {}).get("accepted_answers") or []
        touched = False
        for idx, item in enumerate(accepted):
            value = item.get("value")
            if not isinstance(value, str):
                continue
            try:
                fixed = clean(value)
            except ValueError as exc:
                refused.append((row["id"], value, str(exc)))
                continue
            if fixed is None:
                continue
            planned.append((row["id"], idx, value, fixed))
            touched = True
        if touched:
            backup.append({"id": row["id"], "external_uid": row["external_uid"],
                           "title": row["title"], "is_active": row["is_active"],
                           "solution_rules": rules})

    logger.info("К правке эталонов: %d (в %d заданиях)", len(planned), len(backup))
    for tid, idx, was, now in planned:
        logger.info("  [%s] #%d: %r -> %r", tid, idx, was, now)
    if refused:
        logger.info("НЕ правим — остаток не похож на ответ:")
        for tid, value, why in refused:
            logger.info("  [%s] %r: %s", tid, value, why)

    if not planned or not args.apply:
        if planned:
            logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Суффикс охвата в имени: иначе второй прогон затрёт снимок первого, и откатывать
    # правку стало бы нечем.
    scope = "with-inactive" if args.include_inactive else "active"
    backup_path = (Path(args.backup_dir)
                   / f"{stamp}-tsk787-leading-dash-{scope}-solution-rules-backup.json")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    logger.info("Снимок solution_rules до правки: %s", backup_path)

    try:
        for tid, idx, _was, now in planned:
            cur.execute(
                """
                UPDATE tasks
                SET solution_rules = jsonb_set(
                        solution_rules,
                        ARRAY['short_answer', 'accepted_answers', %s, 'value'],
                        to_jsonb(%s::text), false)
                WHERE id = %s
                """,
                (str(idx), now, tid),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"[{tid}] UPDATE затронул {cur.rowcount} строк")

        # Верификация поштучно, а не агрегатом: агрегат «столько-то строк обновлено»
        # не отличает записанное значение от ожидаемого.
        for tid, idx, _was, now in planned:
            cur.execute(
                """
                SELECT solution_rules #> ARRAY['short_answer','accepted_answers',%s,'value'] ->> 0 AS v
                FROM tasks WHERE id = %s
                """,
                (str(idx), tid),
            )
            actual = cur.fetchone()["v"]
            if actual != now:
                raise RuntimeError(
                    f"[{tid}] #{idx}: после UPDATE эталон {actual!r}, ждали {now!r}"
                )
        conn.commit()
        logger.info("Записано и проверено: %d эталонов в %d заданиях",
                    len(planned), len(backup))
    except Exception:
        conn.rollback()
        logger.exception("Откат транзакции — изменения не применены")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
