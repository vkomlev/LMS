"""Точечная правка stem задания 188 (tsk-343): добавить пример формата ответа.

Проблема (skills-errors.md, 2026-07-21/22, 3-й эпизод класса): составной
(multi-run) короткий ответ описан только словами про разделитель («4 строки
подряд: строка 1+строка 2 для каждого запуска»), без готового примера итоговой
строки. Ученик (task_results.id=7744, user 4503) дал содержательно верную,
но иначе оформленную строку и получил 0/1 — балл выставлен вручную поверх
автопроверки (id=6755/6336/4946/4382, все manual_grant=true).

Правило зафиксировано в methodist/references/assignment-rules.md §9 п.3:
пример ОБЯЗАН использовать вход, НЕ входящий в зачётный расчёт (числа 73/55
названы в stem явно как зачётные — пример с этими же числами дословно выдал
бы готовый ответ). Использован демо-вход 41 (first=4, second=1).

Безопасность (/db-check Режим записи): по умолчанию DRY-RUN (печатает
before/after, ничего не пишет); --apply пишет в транзакции: UPDATE → verify
(stem действительно поменялся, JSON валиден, остальные поля task_content не
затронуты) → commit, иначе rollback. Прод-подключение — параметры из
.mcp.json (learn_prod_db), пароль не печатается и не логируется.

Запуск (из корня LMS):
  python scripts/fix_stem_add_example_tsk343_task188.py                       # dry-run
  DBCHECK_OK=1 python scripts/fix_stem_add_example_tsk343_task188.py --apply  # запись
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
TASK_ID = 188

# --- Прод-подключение (парсим из .mcp.json, пароль не печатается) -------------
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

OLD_TAIL = (
    "Запустите программу 2 раза с числами `73` и `55`.\n"
    "Поместите вывод обоих запусков в поле «Ответ» (4 строки подряд:\n"
    "строка 1+строка 2 для каждого запуска).\n"
)
NEW_TAIL = (
    "Запустите программу 2 раза с числами `73` и `55`.\n"
    "Поместите вывод обоих запусков в поле «Ответ» (4 строки подряд:\n"
    "строка 1+строка 2 для каждого запуска).\n\n"
    "Пример формата для ДЕМО-числа `41` (в расчёт не входит, только показывает "
    "вид ответа): первая строка — `Первая цифра больше`, вторая строка — "
    "`Цифры не одинаковы`. Для 73 и 55 нужны свои две пары строк, без пустых "
    "строк между блоками — итого 4 строки подряд.\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Добавить пример формата в stem задания 188 (tsk-343)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    conn = psycopg2.connect(**PROD)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=== tsk-343: пример формата в stem задания 188 ===")
    print(f"Подключение: {PROD['user']}@{PROD['host']}/{PROD['dbname']}")
    print(f"Режим: {'APPLY' if args.apply else 'DRY-RUN'}")

    cur.execute(
        "SELECT id, task_content FROM tasks WHERE id = %s",
        (TASK_ID,),
    )
    row = cur.fetchone()
    if row is None:
        print(f"ОТКАЗ: задание {TASK_ID} не найдено.")
        conn.rollback()
        conn.close()
        return 1

    content = row["task_content"]
    stem = content.get("stem") or ""
    if OLD_TAIL not in stem:
        print("ОТКАЗ: ожидаемый хвост stem не найден дословно — stem мог измениться "
              "с момента диагностики. Разобрать вручную, ничего не записано.")
        print("\n--- ТЕКУЩИЙ STEM ---\n" + stem)
        conn.rollback()
        conn.close()
        return 1

    new_stem = stem.replace(OLD_TAIL, NEW_TAIL)
    print("\n--- BEFORE (хвост) ---\n" + OLD_TAIL)
    print("--- AFTER (хвост) ---\n" + NEW_TAIL)

    if not args.apply:
        print("\nDRY-RUN: изменения НЕ записаны. Для записи — DBCHECK_OK=1 ... --apply.")
        conn.rollback()
        conn.close()
        return 0

    new_content = dict(content)
    new_content["stem"] = new_stem
    cur.execute(
        "UPDATE tasks SET task_content = %s::jsonb WHERE id = %s",
        (json.dumps(new_content, ensure_ascii=False), TASK_ID),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        print(f"ROLLBACK: обновлено {cur.rowcount} строк вместо 1.")
        return 1

    # verify в той же транзакции: stem изменился, остальные ключи task_content не тронуты
    cur.execute("SELECT task_content FROM tasks WHERE id = %s", (TASK_ID,))
    verified = cur.fetchone()["task_content"]
    other_keys_before = {k: v for k, v in content.items() if k != "stem"}
    other_keys_after = {k: v for k, v in verified.items() if k != "stem"}
    if verified.get("stem") != new_stem or other_keys_after != other_keys_before:
        conn.rollback()
        conn.close()
        print("ROLLBACK: верификация после UPDATE не прошла (stem не совпал или "
              "затронуты другие поля task_content).")
        return 1

    conn.commit()
    print(f"\nCOMMIT: stem задания {TASK_ID} обновлён и верифицирован. Другие поля task_content не тронуты.")
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
