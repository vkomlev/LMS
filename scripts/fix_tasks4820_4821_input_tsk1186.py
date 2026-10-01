"""tsk-1186: дать вспомогательным заданиям 5_4 и 5_5 (курс 156) ввод и эталон.

Задания 4820 (`lms:c156:vvod:5_4`) и 4821 (`lms:c156:vvod:5_5`) курса
«Задание 5 ЕГЭ. Анализ алгоритмов для исполнителей» говорили «Дана строка S»
без самой строки: однозначного ответа нет, эталон пустой, задания держались на
ручной проверке (tsk-590, 15.09). Ручная проверка должна быть редкой (tsk-658),
а здесь две подряд во вводном блоке.

Решение оператора (01.10, вариант «Автопроверка»): вписать S = '1011101',
порядковое слово — «второй по счёту слева», эталоны:
  * 5_4: '1011101'[:-1] + '11'      = 10111011
  * 5_5: '1011101'[:-1] + S[1]      = 1011100
Эталоны считаются в скрипте и сверяются с константами (не по памяти).
Ручная проверка снимается, критерии разбора кода остаются (поправлены
пометки «любое пробное значение» → строка из условия).

Протокол /db-check (режим записи): dry-run по умолчанию; текущее условие
сверяется дословно; course_id/order_position не трогаем; content_provenance
помечается source='manual_script' (tsk-760); одна транзакция, верификация,
затем COMMIT.

Запуск (из корня LMS):
  python scripts/fix_tasks4820_4821_input_tsk1186.py
  DBCHECK_OK=1 python scripts/fix_tasks4820_4821_input_tsk1186.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1186.fix4820_4821")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
S = "1011101"
ANSWER_HINT = "<p>В ответ введите получившуюся строку — только цифры, без кавычек и пробелов.</p>"

FIXES: Dict[int, Dict[str, Any]] = {
    4820: {
        "uid": "lms:c156:vvod:5_4",
        "old_stem": (
            "<p>Вспомогательное задание 5_4.</p>\n"
            "<p>Дана строка S. Удалите её последний символ и допишите справа <code>'11'</code>.</p>"
        ),
        "new_stem": (
            "<p>Вспомогательное задание 5_4.</p>\n"
            f"<p>Дана строка S&nbsp;=&nbsp;<code>'{S}'</code>. Удалите её последний символ "
            "и допишите справа <code>'11'</code>.</p>\n" + ANSWER_HINT
        ),
        "answer": S[:-1] + "11",
        "expected": "10111011",
    },
    4821: {
        "uid": "lms:c156:vvod:5_5",
        "old_stem": (
            "<p>Вспомогательное задание 5_5.</p>\n"
            "<p>Дана строка S. Замените её последний символ на второй слева символ.</p>"
        ),
        "new_stem": (
            "<p>Вспомогательное задание 5_5.</p>\n"
            f"<p>Дана строка S&nbsp;=&nbsp;<code>'{S}'</code>. Замените её последний символ "
            "на второй по счёту слева символ этой строки.</p>\n" + ANSWER_HINT
        ),
        "answer": S[:-1] + S[1],
        "expected": "1011100",
    },
}

NOTES = (
    "Строка задана в условии, число-строка — в поле ответа (эталон стоит), код — в "
    "комментарии; критерии судят код. Ввод и эталон добавлены по решению оператора "
    "2026-10-01 (tsk-1186), критерии — из tsk-590."
)
ACCEPT_FIX = "Любое имя переменной; строка из условия или input(), если для неё результат верный"


def prod_dsn() -> Dict[str, Any]:
    """Параметры подключения к боевой базе из `.mcp.json` (пароль не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def new_rules(rules: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """Собрать solution_rules: эталон, автопроверка, поправленные критерии."""
    out = dict(rules)
    out["short_answer"] = {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower", "strip_brackets_commas"],
        "accepted_answers": [{"score": 1, "value": answer}],
    }
    out["auto_check"] = True
    out["manual_review_required"] = False
    gc = dict(out.get("grading_criteria") or {})
    gc["notes"] = NOTES
    gc["accept"] = [ACCEPT_FIX] + [
        a for a in gc.get("accept", []) if "пробное значение" not in a and "Любое имя переменной" not in a
    ]
    out["grading_criteria"] = gc
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Ввод и эталон для 4820/4821 (tsk-1186)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    for tid, fx in FIXES.items():
        if fx["answer"] != fx["expected"]:
            logger.error("Эталон %s посчитан как %s, ожидали %s", tid, fx["answer"], fx["expected"])
            return 1

    now = datetime.now(timezone.utc).isoformat()
    conn = psycopg2.connect(**prod_dsn())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for tid, fx in FIXES.items():
                cur.execute(
                    "SELECT external_uid, course_id, order_position, task_content->>'stem' AS stem, "
                    "solution_rules FROM tasks WHERE id = %s FOR UPDATE",
                    (tid,),
                )
                row = cur.fetchone()
                if row is None or row["external_uid"] != fx["uid"] or row["course_id"] != 156:
                    logger.error("Задание %s не то или не найдено: %s", tid, row and row["external_uid"])
                    conn.rollback()
                    return 1
                if row["stem"] != fx["old_stem"]:
                    logger.error("Условие %s разошлось с ожидаемым — правил кто-то ещё:\n%s", tid, row["stem"])
                    conn.rollback()
                    return 1
                rules = new_rules(row["solution_rules"], fx["answer"])
                logger.info("\n=== %s (%s) ===\n--- станет ---\n%s\nЭталон: %s\naccept: %s",
                            tid, fx["uid"], fx["new_stem"], fx["answer"],
                            json.dumps(rules["grading_criteria"]["accept"], ensure_ascii=False))
                if not args.apply:
                    continue
                prov = {
                    "source": "manual_script",
                    "fields": ["solution_rules", "task_content"],
                    "edited_at": now,
                    "edited_by": 0,
                    "script": "scripts/fix_tasks4820_4821_input_tsk1186.py",
                    "task": "tsk-1186",
                }
                cur.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb(%s::text), true), "
                    "solution_rules = %s::jsonb, content_provenance = %s::jsonb WHERE id = %s",
                    (fx["new_stem"], json.dumps(rules, ensure_ascii=False),
                     json.dumps(prov, ensure_ascii=False), tid),
                )
                cur.execute(
                    "SELECT task_content->>'stem' AS stem, order_position, solution_rules, "
                    "content_provenance->>'source' AS src FROM tasks WHERE id = %s",
                    (tid,),
                )
                after = cur.fetchone()
                sr = after["solution_rules"]
                ok = (
                    after["stem"] == fx["new_stem"]
                    and after["order_position"] == row["order_position"]
                    and sr["short_answer"]["accepted_answers"][0]["value"] == fx["answer"]
                    and sr["manual_review_required"] is False
                    and after["src"] == "manual_script"
                )
                if not ok:
                    conn.rollback()
                    logger.error("Верификация %s не прошла — откат.", tid)
                    return 1
            if not args.apply:
                conn.rollback()
                logger.info("\nDry-run: ничего не записано.")
                return 0
            conn.commit()
            logger.info("\nЗаписано и проверено: %s", list(FIXES))
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
