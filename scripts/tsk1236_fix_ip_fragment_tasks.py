"""tsk-1236: задания «IP-адрес из фрагментов» (ОГЭ 7) — однозначное условие и валидный эталон.

Решатель перебором всех перестановок фрагментов (4 числа 0..255 без ведущих нулей):
- 6686 (курс 1152): эталон 12.8.1.256 невалиден, корректных адресов 6 — переписано по
  формату ОГЭ 7 (фрагменты А–Г, ответ — буквы), корректный адрес ровно один.
- 6683 (курс 1992): фрагмент «.201» даёт двойную точку, эталон 10.17.56.201 собрать нельзя;
  без точки корректных адресов 6 — переписано тем же способом на тот же адрес.
- 6706 (курс 1152, РешуОГЭ 538): лишняя точка в «Г) 23.» — адрес из пяти частей; по
  первоисточнику фрагмент «23», ответ ГВБА = 233.203.232.33.
- 6702–6705: решения единственны, но точка после последнего фрагмента сливается с точкой
  предложения (класс дефекта 6706) — фрагменты взяты в кавычки, эталон не меняется.

Перед записью каждое новое условие прогоняется решателем: ровно одно решение, равное
эталону, иначе выход без записи.

Протокол /db-check: dry-run по умолчанию; external_uid и текущее условие сверяются;
course_id/order_position не трогаем; content_provenance — source='manual_script' (tsk-760);
одна транзакция, верификация, затем COMMIT.

Запуск (из корня LMS):
  python scripts/tsk1236_fix_ip_fragment_tasks.py
  DBCHECK_OK=1 python scripts/tsk1236_fix_ip_fragment_tasks.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from urllib.parse import unquote, urlparse

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1236.ip_fragments")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LETTERS = "АБВГ"
FRAG_RE = re.compile(r"([А-Г])\) «([^»]*)»")


def valid_ip(s: str) -> bool:
    """Корректный IPv4: четыре числа 0..255 без ведущих нулей."""
    parts = s.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and not (len(p) > 1 and p[0] == "0") and int(p) <= 255 for p in parts)


def solve(stem: str) -> List[str]:
    """Все последовательности букв, дающие корректный адрес (фрагменты из текста условия)."""
    frags = dict(FRAG_RE.findall(stem))
    keys = sorted(frags, key=LETTERS.index)
    return [
        "".join(p) for p in permutations(keys) if valid_ip("".join(frags[k] for k in p))
    ]


HINT_6686 = (
    "Фрагмент, который начинается с точки, может стоять только в конце адреса. Остальные "
    "переставляй и проверяй каждое из четырёх чисел: оно должно быть от 0 до 255."
)
HINT_6683 = (
    "У чисел IP-адреса не пишут ведущий ноль, поэтому фрагмент «01» не может начинать число. "
    "Переставляй фрагменты и проверяй каждое из четырёх чисел: оно должно быть от 0 до 255."
)
LETTER_RULE_NORM = ["trim", "lower"]

# id → (external_uid, новое условие, новый эталон или None, новая подсказка или None, новое название или None)
EDITS: Dict[int, Dict[str, Any]] = {
    6686: dict(
        uid="authored:oge-informatika:zadanie-7#q7",
        stem=(
            "Петя записал IP-адрес школьного сервера на листке, но листок порвался на четыре "
            "кусочка с фрагментами: А) «2.18» Б) «.51» В) «13» Г) «9.204». Восстановите IP-адрес: "
            "запишите буквы фрагментов в том порядке, в котором они идут в адресе. IP-адрес — "
            "четыре числа от 0 до 255, разделённые точками. Впиши последовательность букв "
            "(например, АБВГ)."
        ),
        answer="ВАГБ",
        hint=HINT_6686,
        title="Восстановление IP-адреса из фрагментов",
    ),
    6683: dict(
        uid="authored:oge-informatika:zadanie-7#q4",
        stem=(
            "Адрес сервера записали на доске, но часть стёрли, и остались четыре фрагмента: "
            "А) «6.2» Б) «01» В) «1» Г) «0.17.5». Восстановите IP-адрес: запишите буквы "
            "фрагментов в том порядке, в котором они идут в адресе. IP-адрес — четыре числа "
            "от 0 до 255, разделённые точками. Впиши последовательность букв."
        ),
        answer="ВГАБ",
        hint=HINT_6683,
        title=None,
    ),
}
# Задания РешуОГЭ: только кавычки вокруг фрагментов (+ у 6706 убрана лишняя точка).
QUOTE_ONLY: Dict[int, Dict[str, Any]] = {
    6702: dict(uid="oge:reshu:t7:458", old="А) 2.17 Б) 16 В) .65 Г) 8.121.", new="А) «2.17» Б) «16» В) «.65» Г) «8.121».", answer="БАГВ"),
    6703: dict(uid="oge:reshu:t7:478", old="А) 4.243 Б) 116.2 В) 13 Г) .23.", new="А) «4.243» Б) «116.2» В) «13» Г) «.23».", answer="БВГА"),
    6704: dict(uid="oge:reshu:t7:498", old="А) 2.12 Б) 22 В) .30 Г) 5.121.", new="А) «2.12» Б) «22» В) «.30» Г) «5.121».", answer="БАГВ"),
    6705: dict(uid="oge:reshu:t7:518", old="А) 17 Б) .44 В) 4.144 Г) 9.13.", new="А) «17» Б) «.44» В) «4.144» Г) «9.13».", answer="АГВБ"),
    6706: dict(uid="oge:reshu:t7:538", old="А) .33 Б) 3.232 В) 3.20 Г) 23.", new="А) «.33» Б) «3.232» В) «3.20» Г) «23».", answer="ГВБА"),
}


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


def build(tid: int, content: Dict[str, Any], rules: Dict[str, Any]) -> Optional[tuple]:
    """Собрать новые task_content/solution_rules и список полей; None — расхождение."""
    content, rules = json.loads(json.dumps(content)), json.loads(json.dumps(rules))
    if tid in EDITS:
        e = EDITS[tid]
        content["stem"] = e["stem"]
        content["hints_text"] = [e["hint"]]
        content["has_hints"] = True
        if e["title"]:
            content["title"] = e["title"]
        rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": e["answer"]}]
        rules["short_answer"]["normalization"] = LETTER_RULE_NORM
        fields = ["task_content.stem", "task_content.hints_text", "solution_rules.short_answer"]
        if e["title"]:
            fields.append("task_content.title")
        answer = e["answer"]
    else:
        q = QUOTE_ONLY[tid]
        if content["stem"].count(q["old"]) != 1:
            logger.error("%s: фрагменты в условии не найдены дословно", tid)
            return None
        content["stem"] = content["stem"].replace(q["old"], q["new"])
        fields = ["task_content.stem"]
        answer = q["answer"]
        if rules["short_answer"]["accepted_answers"] != [{"score": 1, "value": answer}]:
            logger.error("%s: эталон разошёлся с ожидаемым", tid)
            return None
    sols = solve(content["stem"])
    if sols != [answer]:
        logger.error("%s: решатель дал %s, ожидался ровно [%s]", tid, sols, answer)
        return None
    return content, rules, fields


def main() -> int:
    """Сверить, показать и (с --apply) записать правку заданий."""
    parser = argparse.ArgumentParser(description="IP из фрагментов (tsk-1236)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    conn = psycopg2.connect(**prod_dsn())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for tid in [*EDITS, *QUOTE_ONLY]:
                uid = (EDITS.get(tid) or QUOTE_ONLY[tid])["uid"]
                cur.execute(
                    "SELECT external_uid, course_id, order_position, task_content, solution_rules "
                    "FROM tasks WHERE id = %s FOR UPDATE",
                    (tid,),
                )
                row = cur.fetchone()
                if row is None or row["external_uid"] != uid:
                    logger.error("Задание %s не то или не найдено", tid)
                    conn.rollback()
                    return 1
                built = build(tid, row["task_content"], row["solution_rules"])
                if built is None:
                    conn.rollback()
                    return 1
                content, rules, fields = built
                logger.info("\n=== %s (%s) ===\n%s\nэталон: %s | решатель: единственное решение",
                            tid, uid, content["stem"], rules["short_answer"]["accepted_answers"])
                if not args.apply:
                    continue
                prov = {
                    "source": "manual_script",
                    "fields": fields,
                    "edited_at": now,
                    "edited_by": 0,
                    "script": "scripts/tsk1236_fix_ip_fragment_tasks.py",
                    "task": "tsk-1236",
                    "reason": "IP из фрагментов: однозначное условие, эталон проверен перебором",
                }
                cur.execute(
                    "UPDATE tasks SET task_content = %s::jsonb, solution_rules = %s::jsonb, "
                    "content_provenance = %s::jsonb WHERE id = %s",
                    (json.dumps(content, ensure_ascii=False), json.dumps(rules, ensure_ascii=False),
                     json.dumps(prov, ensure_ascii=False), tid),
                )
                cur.execute(
                    "SELECT task_content, solution_rules, course_id, order_position FROM tasks WHERE id = %s",
                    (tid,),
                )
                after = cur.fetchone()
                if not (
                    after["task_content"] == content
                    and after["solution_rules"] == rules
                    and after["course_id"] == row["course_id"]
                    and after["order_position"] == row["order_position"]
                ):
                    conn.rollback()
                    logger.error("Верификация %s не прошла — откат.", tid)
                    return 1
            if not args.apply:
                conn.rollback()
                logger.info("\nDry-run: ничего не записано.")
                return 0
            conn.commit()
            logger.info("\nЗаписано и проверено: %s", [*EDITS, *QUOTE_ONLY])
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
