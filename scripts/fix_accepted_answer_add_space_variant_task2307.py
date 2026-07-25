"""Добавить второй равнозначный вариант ответа для задания 2307 (ЕГЭ №18, курс 146).

Проблема (оператор, живой разбор): stem требует "без разделительных знаков"
(1204502), но это единственное задание в банке с таким форматом — во всех
остальных составных ответах катaлога принят пробел как разделитель (см. tsk-343/344).
Ученик (user 4512, task_results.id=9329, 2026-07-25T07:09:12) ввёл "1204 502" —
оба числа верны, но score=0 из-за формата. Оператор: "Неверен эталон" — эталон
дополняется, не сужается (строгий вариант без пробела остаётся первым/каноническим).

solution_rules.short_answer.accepted_answers становится массивом из двух
равнозначных записей (score:1 у каждой) — паттерн из assignment-rules.md §9 п.3
("массив — все равнозначны, любой засчитывается").

Безопасность (/db-check Режим записи): dry-run по умолчанию; --apply — точная
сверка текущего accepted_answers перед записью → транзакция → verify → commit.
Прод-подключение — из .mcp.json, пароль не печатается.

Запуск (из корня LMS):
  python scripts/fix_accepted_answer_add_space_variant_task2307.py
  DBCHECK_OK=1 python scripts/fix_accepted_answer_add_space_variant_task2307.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_ID = 2307

_mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
_dsn = _mcp["mcpServers"]["learn_prod_db"]["args"][-1]
_parsed = urlparse(_dsn)
PROD = dict(
    host=_parsed.hostname,
    port=_parsed.port or 5432,
    dbname=_parsed.path.lstrip("/"),
    user=unquote(_parsed.username or ""),
    password=unquote(_parsed.password or ""),
)

OLD_ACCEPTED = [{"score": 1, "value": "1204502"}]
NEW_ACCEPTED = [{"score": 1, "value": "1204502"}, {"score": 1, "value": "1204 502"}]


def main() -> int:
    parser = argparse.ArgumentParser(description="Добавить пробельный вариант ответа для задания 2307")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    conn = psycopg2.connect(**PROD)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=== Задание 2307: добавить вариант ответа с пробелом ===")
    print(f"Подключение: {PROD['user']}@{PROD['host']}/{PROD['dbname']}")
    print(f"Режим: {'APPLY' if args.apply else 'DRY-RUN'}")

    cur.execute("SELECT solution_rules FROM tasks WHERE id = %s", (TASK_ID,))
    row = cur.fetchone()
    if row is None:
        print(f"ОТКАЗ: задание {TASK_ID} не найдено.")
        conn.rollback()
        conn.close()
        return 1

    sr = row["solution_rules"]
    current = sr.get("short_answer", {}).get("accepted_answers")
    if current != OLD_ACCEPTED:
        print("ОТКАЗ: текущий accepted_answers не совпал дословно с ожидаемым "
              "(мог измениться с момента диагностики). Разобрать вручную.")
        print("--- ТЕКУЩИЙ accepted_answers ---")
        print(json.dumps(current, ensure_ascii=False, indent=2))
        conn.rollback()
        conn.close()
        return 1

    print("\n--- BEFORE ---")
    print(json.dumps(OLD_ACCEPTED, ensure_ascii=False, indent=2))
    print("--- AFTER ---")
    print(json.dumps(NEW_ACCEPTED, ensure_ascii=False, indent=2))

    if not args.apply:
        print("\nDRY-RUN: изменения НЕ записаны. Для записи — DBCHECK_OK=1 ... --apply.")
        conn.rollback()
        conn.close()
        return 0

    new_sr = dict(sr)
    new_sr["short_answer"] = dict(sr["short_answer"])
    new_sr["short_answer"]["accepted_answers"] = NEW_ACCEPTED
    cur.execute(
        "UPDATE tasks SET solution_rules = %s::jsonb WHERE id = %s",
        (json.dumps(new_sr, ensure_ascii=False), TASK_ID),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        print(f"ROLLBACK: обновлено {cur.rowcount} строк вместо 1.")
        return 1

    cur.execute("SELECT solution_rules FROM tasks WHERE id = %s", (TASK_ID,))
    verified = cur.fetchone()["solution_rules"]
    if verified["short_answer"]["accepted_answers"] != NEW_ACCEPTED:
        conn.rollback()
        conn.close()
        print("ROLLBACK: верификация после UPDATE не прошла.")
        return 1
    # остальные поля solution_rules не должны были измениться
    other_before = {k: v for k, v in sr.items() if k != "short_answer"}
    other_after = {k: v for k, v in verified.items() if k != "short_answer"}
    sa_before = {k: v for k, v in sr["short_answer"].items() if k != "accepted_answers"}
    sa_after = {k: v for k, v in verified["short_answer"].items() if k != "accepted_answers"}
    if other_before != other_after or sa_before != sa_after:
        conn.rollback()
        conn.close()
        print("ROLLBACK: затронуты другие поля solution_rules помимо accepted_answers.")
        return 1

    conn.commit()
    print(f"\nCOMMIT: accepted_answers задания {TASK_ID} обновлён и верифицирован.")
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
