"""Уборка артефактов живой проверки tsk-701 на проде (source_system='live-check-tsk701').

Живая проверка гейта активности задания создала на боевой БД три попытки и две
строки `task_results`. Реальной работой ученика они не являются и портили бы его
прогресс, поэтому удаляются.

Протокол `/db-check`, режим записи: сначала читаем текущее состояние, печатаем
план и выборку, выполняем в ОДНОЙ транзакции, верифицируем после. Без `--apply`
скрипт только показывает, что собирается сделать, и ничего не меняет.

DSN берётся из `.mcp.json` (боевая БД), а не из `.env` (там dev-контур).

Запуск:
    python scripts/tsk701_cleanup_live_check.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk701_cleanup_live_check.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

MARKER = "live-check-tsk701"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _prod_dsn() -> str:
    """Достать боевой DSN из `.mcp.json` (там же, где его берёт MCP-сервер)."""
    cfg = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8-sig"))
    for name, server in cfg.get("mcpServers", {}).items():
        if "learn_prod" not in name:
            continue
        blob = json.dumps(server, ensure_ascii=False)
        match = re.search(r"postgresql://[^\"'\s]+", blob)
        if match:
            return match.group(0)
    raise SystemExit("не нашёл боевой DSN в .mcp.json (сервер learn_prod_db)")


def _fetch(cur: Any, sql: str) -> list[dict[str, Any]]:
    cur.execute(sql, {"marker": MARKER})
    return [dict(row) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="выполнить удаление")
    args = parser.parse_args()

    conn = psycopg2.connect(_prod_dsn())
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Читаем ДО: что именно удаляем.
            before = _fetch(
                cur,
                "SELECT a.id AS attempt_id, a.user_id, tr.id AS result_id, "
                "       tr.task_id, tr.score "
                "FROM attempts a LEFT JOIN task_results tr ON tr.attempt_id = a.id "
                "WHERE a.source_system = %(marker)s ORDER BY a.id, tr.id",
            )
            print(f"Найдено строк по метке {MARKER!r}: {len(before)}")
            for row in before:
                print(f"  попытка {row['attempt_id']} (ученик {row['user_id']}) "
                      f"-> результат {row['result_id']} по заданию {row['task_id']}")

            if not before:
                print("Удалять нечего.")
                return 0

            if not args.apply:
                print("\nСухой прогон: ничего не удалено. Повторить с --apply.")
                return 0

            # 2. Удаляем в одной транзакции: сначала дети, потом родители.
            cur.execute(
                "DELETE FROM task_results WHERE attempt_id IN "
                "(SELECT id FROM attempts WHERE source_system = %(marker)s)",
                {"marker": MARKER},
            )
            deleted_results = cur.rowcount
            cur.execute(
                "DELETE FROM attempts WHERE source_system = %(marker)s",
                {"marker": MARKER},
            )
            deleted_attempts = cur.rowcount
            conn.commit()
            print(f"\nУдалено: результатов {deleted_results}, попыток {deleted_attempts}")

            # 3. Верифицируем ПОСЛЕ.
            after = _fetch(
                cur,
                "SELECT a.id AS attempt_id, a.user_id, tr.id AS result_id, "
                "       tr.task_id, tr.score "
                "FROM attempts a LEFT JOIN task_results tr ON tr.attempt_id = a.id "
                "WHERE a.source_system = %(marker)s",
            )
            print(f"Осталось строк по метке: {len(after)}")
            return 0 if not after else 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
