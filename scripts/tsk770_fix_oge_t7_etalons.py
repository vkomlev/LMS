"""tsk-770: исправление эталонов партии ``oge:reshu:t7:*`` (курс 1152, ОГЭ задание 7).

Партия синтетическая — как и ``t4``, ключ ``external_uid`` содержит порядковый номер,
а не ID задачи на РешуОГЭ. Задание: собрать адрес (файла, почтового ящика или IP) из
перенумерованных фрагментов. Целевой адрес задан самим условием, поэтому ответ
проверяется строго: перестановка фрагментов обязана дать ровно эту строку.

Ловушка разбора: у последнего фрагмента точка конца предложения слипается с самим
фрагментом («Ж) pic..», «7) ru.»), поэтому для него проверяются оба прочтения.

Запуск::

    python scripts/tsk770_fix_oge_t7_etalons.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk770_fix_oge_t7_etalons.py --apply
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk770")

UID_LIKE = "oge:reshu:t7:%"

LABEL_RE = re.compile(r"([А-ЗA-Z0-9])\)\s*")
IP_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def parse_fragments(stem: str) -> tuple[list[str], list[str]]:
    """Вернуть (метки, фрагменты). Хвост условия отбрасывается: «(например, АБВГ)»
    содержит «Г)» и иначе принимается за метку фрагмента."""
    head = re.split(r"(?=Восстановите|Запишите)", stem, maxsplit=1)[0]
    marks = list(LABEL_RE.finditer(head))
    if len(marks) < 4:
        raise ValueError("в условии меньше четырёх фрагментов")
    labels, frags = [], []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(head)
        labels.append(mark.group(1))
        frags.append(head[mark.end():end].strip().rstrip(","))
    return labels, frags


def valid_ip(value: str) -> bool:
    match = IP_RE.match(value)
    return bool(match) and all(
        0 <= int(g) <= 255 and (g == "0" or not g.startswith("0"))
        for g in match.groups()
    )


def target_address(stem: str) -> str | None:
    """Адрес, который должен получиться, — из формулировки условия."""
    match = re.search(
        r"файлу\s+(\S+?)\s+на сервере\s+(\S+?)\s+осуществляется по протоколу\s+(\w+)",
        stem,
    )
    if match:
        return f"{match.group(3)}://{match.group(2)}/{match.group(1)}"
    match = re.search(
        r"[Пп]очтовый ящик\s+(\S+?)\s+находится на сервере\s+([^\s.]+(?:\.[^\s.]+)*?)\.\s",
        stem,
    )
    if match:
        return f"{match.group(1)}@{match.group(2)}"
    match = re.search(
        r"На сервере\s+(\S+?)\s+(?:находится|расположен)\s+почтовый ящик\s+(\S+?)\.", stem
    )
    if match:
        return f"{match.group(2)}@{match.group(1)}"
    return None


def solve(stem: str) -> tuple[str, str]:
    """Единственный верный порядок фрагментов. Ошибка, если решений не одно."""
    labels, frags = parse_fragments(stem)
    target = target_address(stem)
    tails = {frags[-1]}
    if frags[-1].endswith("."):
        tails.add(frags[-1][:-1])

    solutions: set[str] = set()
    for tail in tails:
        table = dict(zip(labels, frags[:-1] + [tail]))
        for perm in itertools.permutations(labels):
            joined = "".join(table[p] for p in perm)
            if (joined == target) if target else valid_ip(joined):
                solutions.add("".join(perm))
    if len(solutions) != 1:
        raise ValueError(f"решений не одно, а {len(solutions)}: {sorted(solutions)}")
    return solutions.pop(), target or "валидный IPv4"


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только показать)")
    parser.add_argument("--backup-dir", default="reviews")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    conn = psycopg2.connect(dsn_from_mcp())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        """
        SELECT id, external_uid, task_content->>'title' AS title,
               replace(split_part(task_content->>'stem', 'Источник:', 1), E'\n', ' ') AS stem,
               solution_rules
        FROM tasks WHERE external_uid LIKE %s AND is_active ORDER BY id
        """,
        (UID_LIKE,),
    )
    rows = cur.fetchall()
    logger.info("Активных заданий в партии: %d", len(rows))

    planned: list[tuple[int, str, str, str, str]] = []
    backup: list[dict[str, Any]] = []
    for row in rows:
        answer, target = solve(row["stem"])
        rules = row["solution_rules"] or {}
        accepted = (rules.get("short_answer") or {}).get("accepted_answers") or []
        if len(accepted) != 1:
            raise RuntimeError(f"[{row['id']}] эталонов не один, а {len(accepted)}")
        current = str(accepted[0].get("value"))
        if current == answer:
            continue
        planned.append((row["id"], row["external_uid"], current, answer, target))
        backup.append({"id": row["id"], "external_uid": row["external_uid"],
                       "title": row["title"], "solution_rules": rules})

    logger.info("К правке: %d заданий", len(planned))
    for tid, uid, current, answer, target in planned:
        logger.info("  [%s] %s: %s -> %s   (собирает %s)", tid, uid, current, answer, target)

    if not planned or not args.apply:
        if planned:
            logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = (Path(args.backup_dir)
                   / f"{stamp}-tsk770-oge-t7-solution-rules-backup.json")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    logger.info("Снимок solution_rules до правки: %s", backup_path)

    try:
        for tid, _uid, _current, answer, _target in planned:
            cur.execute(
                """
                UPDATE tasks
                SET solution_rules = jsonb_set(
                        solution_rules,
                        '{short_answer,accepted_answers,0,value}',
                        to_jsonb(%s::text), false)
                WHERE id = %s
                """,
                (answer, tid),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"[{tid}] UPDATE затронул {cur.rowcount} строк")

        cur.execute(
            """
            SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS v
            FROM tasks WHERE id = ANY(%s) ORDER BY id
            """,
            ([p[0] for p in planned],),
        )
        actual = {r["id"]: r["v"] for r in cur.fetchall()}
        for tid, _uid, _current, answer, _target in planned:
            if actual.get(tid) != answer:
                raise RuntimeError(
                    f"[{tid}] после UPDATE эталон {actual.get(tid)!r}, ждали {answer!r}"
                )
        conn.commit()
        logger.info("Записано и проверено: %d заданий", len(planned))
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
