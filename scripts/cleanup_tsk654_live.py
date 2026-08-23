"""
Уборка следов живого прогона гейта непустоты TA на проде (tsk-654).

Живая проверка гейта 2.3g делалась на боевом API (`source_system='tsk654_live'`,
ученик 142, задание 9762): одна попытка с пустым ответом (ожидался не-зачёт) и
одна с текстом (ожидался зачёт). Обе — искусственные, в учебной истории ученика
им не место: без `checked_at` они висят в очереди ручной проверки преподавателя.

Порядок как в `/db-check` Режим записи: сначала читаем, что удаляем, показываем
выборку, удаляем в ОДНОЙ транзакции, затем верифицируем, что не осталось ничего.
Без `--apply` идёт сухой прогон: только показывает план.

Запуск (прод-DSN берётся из `.mcp.json`, не из `.env` — там dev):
    DBCHECK_OK=1 python scripts/cleanup_tsk654_live.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MARKER = "tsk654_live"


def _prod_dsn() -> str:
    """Прод-DSN из `.mcp.json` (алиас learn_prod_db)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json").read_text(encoding="utf-8"))
    for name, entry in cfg.get("mcpServers", {}).items():
        if "learn_prod" not in name:
            continue
        # DSN лежит отдельным аргументом команды — берём его целиком, а не
        # регуляркой по блобу: в пароле встречаются любые символы.
        for arg in entry.get("args", []):
            if isinstance(arg, str) and arg.startswith("postgresql://"):
                return arg
    raise RuntimeError("прод-DSN для learn_prod_db не найден в .mcp.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить удаление (иначе сухой прогон)")
    args = parser.parse_args()

    conn = psycopg2.connect(_prod_dsn())
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Читаем текущее состояние — что именно попадёт под удаление.
            cur.execute(
                """
                SELECT tr.id, tr.user_id, tr.task_id, tr.score, tr.is_correct,
                       tr.attempt_id, tr.checked_at, tr.submitted_at
                  FROM task_results tr
                 WHERE tr.attempt_id IN (SELECT id FROM attempts WHERE source_system = %s)
                 ORDER BY tr.id
                """,
                (MARKER,),
            )
            results = cur.fetchall()
            cur.execute(
                "SELECT id, user_id, course_id, created_at FROM attempts "
                "WHERE source_system = %s ORDER BY id",
                (MARKER,),
            )
            attempts = cur.fetchall()

            logger.info("Под удаление (маркер %s):", MARKER)
            for row in results:
                logger.info(
                    "  task_result id=%s user=%s task=%s score=%s is_correct=%s attempt=%s",
                    row["id"], row["user_id"], row["task_id"],
                    row["score"], row["is_correct"], row["attempt_id"],
                )
            for row in attempts:
                logger.info(
                    "  attempt     id=%s user=%s course=%s created=%s",
                    row["id"], row["user_id"], row["course_id"], row["created_at"],
                )
            if not attempts and not results:
                logger.info("  ничего не найдено — уборка не нужна")
                return 0

            # Страховка: маркер узкий, но чужие строки под него попасть не должны.
            alien = [r for r in results if r["user_id"] != 142] + [
                a for a in attempts if a["user_id"] != 142
            ]
            if alien:
                logger.error("СТОП: под маркер попали строки чужого ученика — %s", alien)
                return 2

            if not args.apply:
                logger.info("Сухой прогон: ничего не удалено. Повторить с --apply.")
                return 0

            # 2. Удаляем в одной транзакции: сначала результаты, потом попытки.
            cur.execute(
                "DELETE FROM task_results WHERE attempt_id IN "
                "(SELECT id FROM attempts WHERE source_system = %s)",
                (MARKER,),
            )
            deleted_results = cur.rowcount
            cur.execute("DELETE FROM attempts WHERE source_system = %s", (MARKER,))
            deleted_attempts = cur.rowcount
            conn.commit()
            logger.info("Удалено: task_results=%s, attempts=%s", deleted_results, deleted_attempts)

            # 3. Верифицируем поштучно, что не осталось ничего.
            cur.execute("SELECT count(*) AS c FROM attempts WHERE source_system = %s", (MARKER,))
            left_attempts = cur.fetchone()["c"]
            cur.execute(
                "SELECT count(*) AS c FROM task_results WHERE attempt_id IN "
                "(SELECT id FROM attempts WHERE source_system = %s)",
                (MARKER,),
            )
            left_results = cur.fetchone()["c"]
            logger.info("После уборки осталось: attempts=%s, task_results=%s", left_attempts, left_results)
            return 0 if (left_attempts == 0 and left_results == 0) else 3
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
