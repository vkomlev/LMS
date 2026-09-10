"""tsk-895: read-only выгрузка условий и телеметрии заданий 5 (курсы 156 и 1383).

Только ЧИТАЕТ боевую базу. Ничего не пишет и не меняет.

Методологическая правка (10.09, в процессе работы над tsk-895): исходная версия
скрипта исключала submissions с `source_system='manual_teacher'` по аналогии с
`weekly_hard_tasks.py`. Проверка сырых строк `task_results` по id=2291 показала,
что под этим source_system скрываются РЕАЛЬНЫЕ ученики (email реальный, роль
`student`) — учитель заносит их результат вручную, а не тестирует сам. Прежний
фильтр занижал охват (2 ученика вместо 11 для id=2291). Оставлен только фильтр
по РОЛИ аккаунта (STAFF_ROLES) — так же, как это сделано в `SELECT_STUCK` в
`weekly_hard_tasks.py`; исключать по source_system здесь нельзя.

Запуск: python scripts/tsk895_dump_task5.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk895-dump")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "specs"
COURSE_IDS = (156, 1383)

STAFF_ROLES = ("admin", "teacher", "curator", "superadmin", "methodist", "marketer", "parent")


def dsn(alias: str = "learn_prod_db") -> str:
    cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


SELECT_TASKS = """
SELECT
    t.id, t.external_uid, t.course_id, c.title AS course_title,
    t.task_content->>'title' AS title,
    t.task_content->>'stem' AS stem,
    t.task_content->>'type' AS task_type,
    t.task_content->'options' AS options,
    t.difficulty_id, d.code AS difficulty_code,
    t.order_position, t.requirement_level, t.is_active,
    t.solution_rules,
    t.max_score, t.max_attempts
FROM tasks t
JOIN courses c ON c.id = t.course_id
JOIN difficulties d ON d.id = t.difficulty_id
WHERE t.course_id = ANY(%(course_ids)s) AND t.is_active
ORDER BY t.course_id, t.order_position, t.id
"""

SELECT_TELEMETRY = """
WITH students AS (
    SELECT u.id FROM users u
    WHERE NOT EXISTS (
        SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = u.id AND r.name = ANY(%(staff)s)
    )
),
first_try AS (
    SELECT DISTINCT ON (tr.user_id, tr.task_id)
           tr.user_id, tr.task_id, tr.is_correct AS first_ok
    FROM task_results tr
    WHERE tr.user_id IN (SELECT id FROM students)
    ORDER BY tr.user_id, tr.task_id, tr.submitted_at
),
first_agg AS (
    SELECT task_id,
           count(*) AS students_attempted,
           count(*) FILTER (WHERE first_ok) AS passed_first
    FROM first_try GROUP BY task_id
),
all_tries AS (
    SELECT tr.task_id,
           count(*) AS submissions,
           count(DISTINCT tr.user_id) AS students_all,
           count(*) FILTER (WHERE tr.score > 0) AS passes,
           max(tr.submitted_at) AS last_try
    FROM task_results tr
    WHERE tr.user_id IN (SELECT id FROM students)
    GROUP BY tr.task_id
),
helps AS (
    SELECT task_id, count(*) AS help_requests
    FROM help_requests
    WHERE task_id IS NOT NULL
    GROUP BY task_id
)
SELECT t.id AS task_id,
       coalesce(fa.students_attempted, 0) AS students_attempted,
       coalesce(fa.passed_first, 0) AS passed_first,
       coalesce(at.submissions, 0) AS submissions,
       coalesce(at.students_all, 0) AS students_all,
       coalesce(at.passes, 0) AS passes,
       at.last_try,
       coalesce(h.help_requests, 0) AS help_requests
FROM tasks t
LEFT JOIN first_agg fa ON fa.task_id = t.id
LEFT JOIN all_tries at ON at.task_id = t.id
LEFT JOIN helps h ON h.task_id = t.id
WHERE t.course_id = ANY(%(course_ids)s) AND t.is_active
"""


def fetch(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    conn = psycopg2.connect(dsn())
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(query, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    tasks = fetch(SELECT_TASKS, {"course_ids": list(COURSE_IDS)})
    telemetry = fetch(SELECT_TELEMETRY, {"course_ids": list(COURSE_IDS), "staff": list(STAFF_ROLES)})
    tel_by_id = {row["task_id"]: row for row in telemetry}

    for row in tasks:
        row["telemetry"] = tel_by_id.get(row["id"], {})
        for k, v in list(row["telemetry"].items()):
            if hasattr(v, "isoformat"):
                row["telemetry"][k] = v.isoformat()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "tsk895-task5-dump.json"
    out_path.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Выгружено заданий: %d (курс 156: %d, курс 1383: %d)",
                len(tasks),
                sum(1 for t in tasks if t["course_id"] == 156),
                sum(1 for t in tasks if t["course_id"] == 1383))
    logger.info("Файл: %s", out_path)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
