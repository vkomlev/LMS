"""Две ловушки формата ответа, найденные разбором tsk-602 (курсы 157 и 893).

1. Задания 4836 и 4837 (курс 157, координаты Черепахи). Стем просит ввести ответ
   «в формате x,y», эталон записан без пробела (`10,0`, `3,5`), а нормализация
   задания (`trim`, `lower`) пробел после запятой не снимает. Ученик 4519 ввёл
   `10, 0` и `3, 5` — ответ верный по смыслу, вердикт «не зачёт» из-за формата
   (task_results 11396, 11403). Эталон ДОПОЛНЯЕТСЯ вариантом с пробелом, не
   сужается — тот же приём, что в `fix_accepted_answer_add_space_variant_task2307.py`
   и в tsk-343/344.

2. Задание 5757 (курс 893). Пять его соседей по классу «напиши строку-заголовок»
   (5499, 5527, 5633, 5634, 5768) заканчиваются подсказкой «(Только строку с … и
   двоеточием.)», и эталон у всех требует двоеточие. У 5757 подсказки нет, а эталон
   двоеточие требует — ловушка ждёт первого ученика, который напишет строку без него.
   Дописывается та же фраза, что у соседей.

Что НЕ делается: вердикты прошлых работ не пересчитываются (решение оператора,
tsk-602). Список работ, которые по нынешним правилам зачлись бы, показывает
`scripts/audit_stale_false_verdicts_tsk602.py`.

Безопасность (/db-check, режим записи): dry-run по умолчанию; при `--apply` каждое
поле сверяется дословно с ожидаемым «до», запись идёт в одной транзакции с проверкой
числа строк и верификацией после. Прод-подключение — из .mcp.json, пароль не печатается.

Запуск (из корня LMS):
  python scripts/fix_answer_format_traps_tsk602.py
  DBCHECK_OK=1 python scripts/fix_answer_format_traps_tsk602.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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

# Задание → (эталон «до», эталон «после»)
ACCEPTED_CHANGES: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
    4836: (
        [{"score": 1, "value": "10,0"}],
        [{"score": 1, "value": "10,0"}, {"score": 1, "value": "10, 0"}],
    ),
    4837: (
        [{"score": 1, "value": "3,5"}],
        [{"score": 1, "value": "3,5"}, {"score": 1, "value": "3, 5"}],
    ),
}

STEM_TASK_ID = 5757
STEM_SUFFIX = " (Только строку с for и двоеточием.)"
STEM_BEFORE_TAIL = "повторит тело 36 раз, используя переменную i."


def _fetch(cur: Any, task_id: int) -> dict[str, Any]:
    cur.execute(
        "SELECT solution_rules, task_content FROM tasks WHERE id = %s", (task_id,)
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"задание {task_id} не найдено")
    return row


def _apply_accepted(cur: Any, task_id: int, new_accepted: list[dict[str, Any]]) -> None:
    """Записывает новый accepted_answers, не трогая остальные поля правил."""
    rules = _fetch(cur, task_id)["solution_rules"]
    new_rules = dict(rules)
    new_rules["short_answer"] = dict(rules["short_answer"])
    new_rules["short_answer"]["accepted_answers"] = new_accepted
    cur.execute(
        "UPDATE tasks SET solution_rules = %s::jsonb WHERE id = %s",
        (json.dumps(new_rules, ensure_ascii=False), task_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"задание {task_id}: обновлено {cur.rowcount} строк вместо 1")

    verified = _fetch(cur, task_id)["solution_rules"]
    if verified["short_answer"]["accepted_answers"] != new_accepted:
        raise RuntimeError(f"задание {task_id}: верификация accepted_answers не прошла")
    if {k: v for k, v in rules.items() if k != "short_answer"} != {
        k: v for k, v in verified.items() if k != "short_answer"
    }:
        raise RuntimeError(f"задание {task_id}: затронуты посторонние поля solution_rules")
    if {k: v for k, v in rules["short_answer"].items() if k != "accepted_answers"} != {
        k: v for k, v in verified["short_answer"].items() if k != "accepted_answers"
    }:
        raise RuntimeError(f"задание {task_id}: затронуты посторонние поля short_answer")


def _apply_stem(cur: Any, task_id: int, new_stem: str) -> None:
    """Записывает новый стем, не трогая остальные поля содержимого задания."""
    content = _fetch(cur, task_id)["task_content"]
    new_content = dict(content)
    new_content["stem"] = new_stem
    cur.execute(
        "UPDATE tasks SET task_content = %s::jsonb WHERE id = %s",
        (json.dumps(new_content, ensure_ascii=False), task_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"задание {task_id}: обновлено {cur.rowcount} строк вместо 1")

    verified = _fetch(cur, task_id)["task_content"]
    if verified["stem"] != new_stem:
        raise RuntimeError(f"задание {task_id}: верификация стема не прошла")
    if {k: v for k, v in content.items() if k != "stem"} != {
        k: v for k, v in verified.items() if k != "stem"
    }:
        raise RuntimeError(f"задание {task_id}: затронуты посторонние поля task_content")


def main() -> int:
    parser = argparse.ArgumentParser(description="Починить две ловушки формата ответа (tsk-602)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    conn = psycopg2.connect(**PROD)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=== tsk-602: ловушки формата ответа ===")
    print(f"Подключение: {PROD['user']}@{PROD['host']}/{PROD['dbname']}")
    print(f"Режим: {'ЗАПИСЬ' if args.apply else 'DRY-RUN'}\n")

    try:
        # --- сверка «до» ---
        for task_id, (before, after) in ACCEPTED_CHANGES.items():
            current = (_fetch(cur, task_id)["solution_rules"].get("short_answer") or {}).get(
                "accepted_answers"
            )
            if current != before:
                print(f"ОТКАЗ: задание {task_id} — accepted_answers не совпал с ожидаемым.")
                print("--- ТЕКУЩИЙ ---")
                print(json.dumps(current, ensure_ascii=False, indent=2))
                conn.rollback()
                conn.close()
                return 1
            print(f"задание {task_id}:")
            print(f"   было:  {json.dumps(before, ensure_ascii=False)}")
            print(f"   стало: {json.dumps(after, ensure_ascii=False)}")

        stem_current = _fetch(cur, STEM_TASK_ID)["task_content"]["stem"]
        if not stem_current.endswith(STEM_BEFORE_TAIL):
            print(f"ОТКАЗ: задание {STEM_TASK_ID} — стем не оканчивается ожидаемой фразой.")
            print(f"--- ХВОСТ ТЕКУЩЕГО ---\n{stem_current[-120:]}")
            conn.rollback()
            conn.close()
            return 1
        stem_new = stem_current + STEM_SUFFIX
        print(f"\nзадание {STEM_TASK_ID} (стем):")
        print(f"   было:  …{stem_current[-70:]}")
        print(f"   стало: …{stem_new[-70:]}")

        if not args.apply:
            print("\nDRY-RUN: изменения НЕ записаны. Для записи — DBCHECK_OK=1 … --apply.")
            conn.rollback()
            conn.close()
            return 0

        # --- запись ---
        for task_id, (_before, after) in ACCEPTED_CHANGES.items():
            _apply_accepted(cur, task_id, after)
        _apply_stem(cur, STEM_TASK_ID, stem_new)

        conn.commit()
        print("\nCOMMIT: 3 задания обновлены и верифицированы.")
        return 0
    except Exception as exc:  # noqa: BLE001 — любая осечка откатывает всю правку
        conn.rollback()
        print(f"\nROLLBACK: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
