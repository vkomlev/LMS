"""tsk-770: исправление эталонов партии ``oge:reshu:t2:*`` (курс 1112, ОГЭ задание 2).

Партия синтетическая — как ``t4`` и ``t7``, ключ ``external_uid`` содержит порядковый
номер, а не ID задачи на РешуОГЭ. Задание: расшифровать строку по кодовой таблице.

Правится ТОЛЬКО то, где верный ответ вычисляется однозначно: строка разбирается
кодами ровно одним способом и этот разбор не совпадает с эталоном. Намеренно НЕ
трогаются два других класса, найденных в этой партии, — их чинить нечем:
  * строка не разбирается вообще (сломано само условие, верного ответа нет);
  * строка разбирается несколькими словами (неоднозначное условие; эталон — один
    из верных, и подмена его другим ничего не улучшит).
Оба класса вынесены оператору отчётом, а не правкой вслепую.

Запуск::

    python scripts/tsk770_fix_oge_t2_etalons.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk770_fix_oge_t2_etalons.py --apply
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

logger = logging.getLogger("tsk770")

UID_LIKE = "oge:reshu:t2:%"

#: «Буква = код»; код может содержать пробелы («П=_ _+»), они незначащие.
PAIR_RE = re.compile(r"([А-ЯЁA-Z])\s*=\s*([^\s,.;]+(?:\s+[^\s,.;А-ЯЁA-Z]+)*)")
#: Задания «найдите цепочку с единственной расшифровкой» — другой класс, пропускаем.
CHAINS_RE = re.compile(r"(?:цепочки|шифровки)\s*:\s*([0-9,\s]+)")


def parse_table(stem: str) -> dict[str, str]:
    """Кодовая таблица из условия; пробелы внутри кодов снимаются."""
    table: dict[str, str] = {}
    for letter, code in PAIR_RE.findall(stem):
        table.setdefault(letter, code.replace(" ", ""))
    return table


def extract_message(stem: str, table: dict[str, str]) -> str | None:
    """Закодированная строка.

    Ищем не по формулировке (их в партии с десяток разных), а по составу: это самая
    длинная цепочка символов кодового алфавита, длиннее любого отдельного кода.
    """
    alphabet = "".join(sorted(set("".join(table.values()))))
    runs = re.findall("[" + re.escape(alphabet) + "]+", stem.replace(" ", ""))
    longest_code = max(len(code) for code in table.values())
    candidates = [run for run in runs if len(run) > longest_code]
    return max(candidates, key=len) if candidates else None


def decode_all(msg: str, table: dict[str, str], limit: int = 500) -> list[str]:
    """Все разборы строки по кодовой таблице."""
    out: list[str] = []

    def walk(pos: int, acc: list[str]) -> None:
        if len(out) >= limit:
            return
        if pos == len(msg):
            out.append("".join(acc))
            return
        for letter, code in table.items():
            if code and msg.startswith(code, pos):
                acc.append(letter)
                walk(pos + len(code), acc)
                acc.pop()

    walk(0, [])
    return out


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

    planned: list[tuple[int, str, str, str]] = []
    backup: list[dict[str, Any]] = []
    skipped_broken: list[int] = []
    skipped_ambiguous: list[tuple[int, list[str]]] = []

    for row in rows:
        if CHAINS_RE.search(row["stem"]):
            continue  # класс «найди однозначную цепочку» — не этот скрипт
        table = parse_table(row["stem"])
        msg = extract_message(row["stem"], table) if table else None
        if not msg:
            continue
        variants = sorted(set(decode_all(msg, table)))
        rules = row["solution_rules"] or {}
        accepted = (rules.get("short_answer") or {}).get("accepted_answers") or []
        if len(accepted) != 1:
            raise RuntimeError(f"[{row['id']}] эталонов не один, а {len(accepted)}")
        current = str(accepted[0].get("value")).strip().upper()

        if not variants:
            skipped_broken.append(row["id"])
            continue
        if len(variants) > 1:
            if current not in variants:
                skipped_broken.append(row["id"])
            else:
                skipped_ambiguous.append((row["id"], variants))
            continue
        if variants[0] == current:
            continue
        planned.append((row["id"], row["external_uid"], current, variants[0]))
        backup.append({"id": row["id"], "external_uid": row["external_uid"],
                       "title": row["title"], "solution_rules": rules})

    logger.info("К правке (разбор единственный и расходится с эталоном): %d", len(planned))
    for tid, uid, current, answer in planned:
        logger.info("  [%s] %s: %s -> %s", tid, uid, current, answer)
    if skipped_broken:
        logger.info("НЕ правим — условие не читается ни одним разбором: %s", skipped_broken)
    if skipped_ambiguous:
        logger.info("НЕ правим — условие допускает несколько ответов:")
        for tid, variants in skipped_ambiguous:
            logger.info("  [%s] верны все: %s", tid, variants)

    if not planned or not args.apply:
        if planned:
            logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = (Path(args.backup_dir)
                   / f"{stamp}-tsk770-oge-t2-solution-rules-backup.json")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    logger.info("Снимок solution_rules до правки: %s", backup_path)

    try:
        for tid, _uid, _current, answer in planned:
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
        for tid, _uid, _current, answer in planned:
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
