# -*- coding: utf-8 -*-
"""Course 150 (task 23, recursive tree traversal) normalization.

Sources:
- nav_parser on https://victor-komlev.ru/navigator-po-zadaniyu-23-ege/
- explicit TG/Krylov level markers in task stems
- checklist rule: Krylov PDF/ext tasks -> Easy by default

Notes:
- material 406 is a task-link container, not theory; active LMS tasks already
  cover its links, so it is deactivated;
- material 616 duplicates material 615;
- yandex:31d08c52-c86e-4487-b7e4-6f7435f63344:23 is absent in LMS and
  remains in the missing-task registry.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 150

WP_NAV_EASY_KOMPEGE = {"20", "413", "951", "21420", "21604"}
WP_NAV_MEDIUM_SDAMGIA = {"52194", "63039"}

CRYLOV_DIRECT_EASY_UIDS = ("crylov:v16t23",)

TG_EASY_UIDS = ("tg:ege:463", "tg:ege:561", "tg:ege:949")
TG_MEDIUM_UIDS = ("tg:ege:533",)

MATERIAL_PLAN = {
    406: ("Задания для закрепления", "required", False, 0),
    611: ("Урок 23_1. Рекурсивная функция для подсчета количества программ в простейшем случае", "recommended", True, 1),
    612: ("Урок 23_2. Усложненные задания. Обязательная траектория, исключаемая траектория.", "recommended", True, 2),
    613: ("Разбор решения заданий 23 с помощью рекурсивной функции.", "recommended", True, 3),
    614: ("Разбор заданий 23", "recommended", True, 4),
    615: ("Разбор решений заданий 23 разного уровня сложности.", "required", True, 5),
    616: ("Разбор решений заданий 23 разного уровня сложности.", "required", False, 6),
}


def load_dsn() -> str:
    if dsn := os.environ.get("LMS_DB_DSN"):
        return dsn
    env = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    with open(env, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    raise RuntimeError("DATABASE_URL not found in .env")


def section(title: str) -> None:
    print(f"\n-- {title} {'-' * max(1, 60 - len(title))}")


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")
        cur.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")

        section("Before")
        cur.execute(
            """
            SELECT difficulty_id, requirement_level,
                   count(*) FILTER (WHERE is_active) AS active,
                   min(order_position) FILTER (WHERE is_active),
                   max(order_position) FILTER (WHERE is_active)
            FROM tasks
            WHERE course_id=%s
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        for row in cur.fetchall():
            print(" ", row)

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        before_total = cur.fetchone()[0]
        print(f"Active tasks before: {before_total}")
        checks["active total stable = 81"] = before_total == 81

        section("Materials")
        for mat_id, (title, req, active, pos) in MATERIAL_PLAN.items():
            cur.execute(
                """
                UPDATE materials
                SET title=%s, requirement_level=%s, is_active=%s, order_position=%s
                WHERE course_id=%s AND id=%s
                """,
                (title, req, active, pos, COURSE_ID, mat_id),
            )
            print(f"id={mat_id}: req={req}, active={active}, pos={pos}: {cur.rowcount}")
            checks[f"material {mat_id} updated"] = cur.rowcount == 1

        section("Difficulty")
        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND NOT (
                task_content->>'source_kind'='kompege'
                AND task_content->>'source_task_id'=ANY(%s)
              )
              AND NOT (
                task_content->>'source_kind'='sdamgia'
                AND task_content->>'source_task_id'=ANY(%s)
              )
            """,
            (COURSE_ID, list(WP_NAV_EASY_KOMPEGE), list(WP_NAV_MEDIUM_SDAMGIA)),
        )
        print(f"wp_nav hard -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 49"] = cur.rowcount == 49

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_kind'='kompege'
              AND task_content->>'source_task_id'=ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_EASY_KOMPEGE)),
        )
        print(f"wp_nav easy -> diff=2: {cur.rowcount}")
        checks["wp_nav easy = 5"] = cur.rowcount == 5

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=3
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_kind'='sdamgia'
              AND task_content->>'source_task_id'=ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_MEDIUM_SDAMGIA)),
        )
        print(f"wp_nav medium -> diff=3: {cur.rowcount}")
        checks["wp_nav medium = 2"] = cur.rowcount == 2

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'pdf:d4:pdf:crylov:%%'
                OR external_uid ILIKE 'ext:pdf:d4:pdf:crylov:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"Krylov PDF/ext -> diff=2: {cur.rowcount}")
        checks["crylov pdf/ext = 20"] = cur.rowcount == 20

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(CRYLOV_DIRECT_EASY_UIDS)),
        )
        print(f"Krylov direct easy -> diff=2: {cur.rowcount}")
        checks["crylov direct easy = 1"] = cur.rowcount == 1

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG easy -> diff=2: {cur.rowcount}")
        checks["tg easy = 3"] = cur.rowcount == 3

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG medium -> diff=3: {cur.rowcount}")
        checks["tg medium = 1"] = cur.rowcount == 1

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"diff<4 active tasks -> required: {cur.rowcount}")
        checks["diff<4 required = 32"] = cur.rowcount == 32

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 49"] = cur.rowcount == 49

        section("Reorder")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        task_ids = [row[0] for row in cur.fetchall()]
        checks["active task ids = 81"] = len(task_ids) == 81

        cur.execute("UPDATE tasks SET order_position=order_position+3000 WHERE course_id=%s", (COURSE_ID,))
        for pos, task_id in enumerate(task_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (pos, task_id))
        print(f"active tasks reordered={len(task_ids)}")

        section("After")
        cur.execute(
            """
            SELECT difficulty_id, requirement_level,
                   count(*) FILTER (WHERE is_active) AS active,
                   min(order_position) FILTER (WHERE is_active),
                   max(order_position) FILTER (WHERE is_active)
            FROM tasks
            WHERE course_id=%s
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        after_rows = cur.fetchall()
        for row in after_rows:
            print(" ", row)
        checks["final diff blocks"] = after_rows == [
            (2, "required", 29, 1, 29),
            (3, "required", 3, 30, 32),
            (4, "recommended", 49, 33, 81),
        ]

        cur.execute(
            """
            SELECT order_position, count(*)
            FROM tasks WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*) > 1
            """,
            (COURSE_ID,),
        )
        checks["no duplicate task order"] = len(cur.fetchall()) == 0

        cur.execute(
            """
            SELECT requirement_level, is_active, count(*)
            FROM materials
            WHERE course_id=%s
            GROUP BY requirement_level, is_active
            ORDER BY is_active DESC, requirement_level
            """,
            (COURSE_ID,),
        )
        material_rows = cur.fetchall()
        print("materials:", material_rows)
        checks["material summary"] = material_rows == [
            ("recommended", True, 4),
            ("required", True, 1),
            ("required", False, 2),
        ]

        print("\nChecks:")
        for name, ok in checks.items():
            print(f"  {'OK' if ok else 'FAIL'} {name}")

        if not all(checks.values()):
            raise RuntimeError("One or more checks failed")

        if apply:
            conn.commit()
            print("\nCOMMIT applied.")
        else:
            conn.rollback()
            print("\nDry run only, rolled back. Re-run with --apply to commit.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
