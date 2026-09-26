"""tsk-1132: перенести чужие задания из курса 162 «Задание 11 ЕГЭ» в свои курсы (прод).

2938 (БД «Города и страны», задание 3) -> 138 «Задание №3. Базы данных в Excel»;
3474 (поиск слов в тексте Грина) -> 141 «Поиск информации в документах».
Обе сложности 3 (средняя) — «Сложные»-двойники (1381/1388) держат только HARD.

Почему UPDATE, а не API: боевой `PATCH /api/v1/tasks/{id}` (tsk-433,
`TaskManualPatch`) намеренно не меняет `course_id`, а `bulk_upsert`
перезаписывает `task_content` целиком. Триггеры `trg_set_task_order_position`
(ветка переезда tsk-802) и `trg_task_audit_update` срабатывают на UPDATE так же,
как из сервиса. Сдачи (`task_results`) привязаны к заданию и не трогаются;
кеш `student_course_state` у сдававших — только корень 112, общий для обоих курсов.

Запуск (из корня LMS):
  python scripts/tsk1132_move_foreign_tasks.py
  DBCHECK_OK=1 python scripts/tsk1132_move_foreign_tasks.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1132.move")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_COURSE_ID = 162
#: задание -> целевой курс
MOVES: Dict[int, int] = {2938: 138, 3474: 141}


def prod_dsn() -> Dict[str, Any]:
    """Прод-DSN из `.mcp.json` (алиас learn_prod_db)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def snapshot(cur: Any) -> Dict[str, Any]:
    """Снимок: курсы заданий, сдачи, целостность позиций трёх курсов."""
    ids = list(MOVES)
    cur.execute("SELECT id, course_id, order_position FROM tasks WHERE id = ANY(%s)", (ids,))
    tasks = {r["id"]: (r["course_id"], r["order_position"]) for r in cur.fetchall()}
    cur.execute(
        "SELECT task_id, count(*) AS n FROM task_results WHERE task_id = ANY(%s) GROUP BY task_id",
        (ids,),
    )
    results = {r["task_id"]: r["n"] for r in cur.fetchall()}
    courses = [SOURCE_COURSE_ID, *MOVES.values()]
    cur.execute(
        "SELECT course_id, count(*) AS n, max(order_position) AS mx, "
        "count(DISTINCT order_position) AS d FROM tasks WHERE course_id = ANY(%s) GROUP BY course_id",
        (courses,),
    )
    positions = {r["course_id"]: (r["n"], r["mx"], r["d"]) for r in cur.fetchall()}
    return {"tasks": tasks, "results": results, "positions": positions}


def main() -> int:
    """Перенести задания в транзакции с проверкой до и после."""
    parser = argparse.ArgumentParser(description="tsk-1132: перенос 2938/3474 из курса 162")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        before = snapshot(cur)
        logger.info("ДО: %s", before)
        if any(before["tasks"][t][0] != SOURCE_COURSE_ID for t in MOVES):
            logger.error("Задание уже не в курсе %s — останов", SOURCE_COURSE_ID)
            conn.rollback()
            return 1

        for task_id, target in MOVES.items():
            end_pos = before["positions"][target][1] + 1
            cur.execute(
                "UPDATE tasks SET course_id = %s, order_position = %s "
                "WHERE id = %s AND course_id = %s",
                (target, end_pos, task_id, SOURCE_COURSE_ID),
            )
            if cur.rowcount != 1:
                logger.error("Задание %s: обновлено %s строк — откат", task_id, cur.rowcount)
                conn.rollback()
                return 1

        after = snapshot(cur)
        logger.info("ПОСЛЕ: %s", after)
        ok = (
            all(after["tasks"][t][0] == c for t, c in MOVES.items())
            and after["results"] == before["results"]
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
        logger.info("COMMIT: задания перенесены")
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
