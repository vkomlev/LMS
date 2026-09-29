"""tsk-1160 (часть 2, по ответу оператора): перенести 2325/2326/2174 — без снятия зачётов.

Исходно: перенести 2329/2330 из курса 157 «Задание 6 ЕГЭ. Черепаха» в 1384 «Задание 6. Сложные»
и снять ручной зачёт 37148 у ученицы 4578 (прод).

Перенос — точечный UPDATE, как в tsk-1132 (PATCH не меняет course_id, bulk_upsert затирает
task_content); requirement_level=recommended по конвенции «Сложных». Снятие зачёта — DELETE
с меткой `app.audit_actor`: триггер `trg_task_result_audit_delete` (tsk-803) пишет снимок
в append-only `task_result_audit`. Сдачи других учеников не трогаются. Кеш
`student_course_state` по 157/1384 не ведётся (только корень 112) — пересчёт не нужен.

Запуск (из корня LMS):
  python scripts/tsk1160b_move_turtle_hard_more.py
  DBCHECK_OK=1 python scripts/tsk1160b_move_turtle_hard_more.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk1132_move_foreign_tasks import prod_dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1160")

SOURCE_COURSE_ID = 157
TARGET_COURSE_ID = 1384
TASK_IDS: List[int] = [2325, 2326, 2174]
GRANT_RESULT_ID = 37148
GRANT_USER_ID = 4578
AUDIT_ACTOR = "script:tsk1160 operator-request"


def snapshot(cur: Any) -> Dict[str, Any]:
    """Снимок: курсы и уровни заданий, число сдач, целостность позиций, строка зачёта."""
    cur.execute(
        "SELECT id, course_id, requirement_level FROM tasks WHERE id = ANY(%s)", (TASK_IDS,)
    )
    tasks = {r["id"]: (r["course_id"], r["requirement_level"]) for r in cur.fetchall()}
    cur.execute(
        "SELECT task_id, count(*) AS n FROM task_results WHERE task_id = ANY(%s) "
        "AND id <> %s GROUP BY task_id",
        (TASK_IDS, GRANT_RESULT_ID),
    )
    others = {r["task_id"]: r["n"] for r in cur.fetchall()}
    cur.execute(
        "SELECT course_id, count(*) AS n, max(order_position) AS mx, "
        "count(DISTINCT order_position) AS d FROM tasks WHERE course_id = ANY(%s) GROUP BY course_id",
        ([SOURCE_COURSE_ID, TARGET_COURSE_ID],),
    )
    positions = {r["course_id"]: (r["n"], r["mx"], r["d"]) for r in cur.fetchall()}
    cur.execute("SELECT count(*) AS n FROM task_results WHERE id = %s", (GRANT_RESULT_ID,))
    grant = cur.fetchone()["n"]
    cur.execute(
        "SELECT count(*) AS n FROM task_result_audit WHERE result_id = %s AND action = 'DELETE'",
        (GRANT_RESULT_ID,),
    )
    audit = cur.fetchone()["n"]
    return {"tasks": tasks, "others": others, "positions": positions, "grant": grant, "audit": audit}


def main() -> int:
    """Выполнить перенос и снятие зачёта в одной транзакции с проверкой до и после."""
    parser = argparse.ArgumentParser(description="tsk-1160")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        before = snapshot(cur)
        logger.info("ДО: %s", before)
        if any(before["tasks"][t][0] != SOURCE_COURSE_ID for t in TASK_IDS):
            logger.error("Состояние не совпадает с разведкой — останов")
            conn.rollback()
            return 1

        cur.execute("SELECT set_config('app.audit_actor', %s, true)", (AUDIT_ACTOR,))
        end_pos = before["positions"][TARGET_COURSE_ID][1]
        for task_id in TASK_IDS:
            end_pos += 1
            cur.execute(
                "UPDATE tasks SET course_id = %s, order_position = %s, "
                "requirement_level = 'recommended' WHERE id = %s AND course_id = %s",
                (TARGET_COURSE_ID, end_pos, task_id, SOURCE_COURSE_ID),
            )
            if cur.rowcount != 1:
                logger.error("Задание %s: обновлено %s строк — откат", task_id, cur.rowcount)
                conn.rollback()
                return 1

        after = snapshot(cur)
        logger.info("ПОСЛЕ: %s", after)
        ok = (
            all(after["tasks"][t] == (TARGET_COURSE_ID, "recommended") for t in TASK_IDS)
            and after["others"] == before["others"]
            and all(n == mx == d for n, mx, d in after["positions"].values())
        )
        if not ok:
            logger.error("Проверка не прошла — откат")
            conn.rollback()
            return 1
        if not args.apply:
            logger.info("DRY-RUN: проверка пройдена, изменения откачены.")
            conn.rollback()
            return 0
        conn.commit()
        logger.info("COMMIT")
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
