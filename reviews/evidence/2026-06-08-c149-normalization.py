# -*- coding: utf-8 -*-
"""Course 149 (task 22, parallel processes) normalization.

Sources:
- nav_parser on https://victor-komlev.ru/navigator-po-zadaniyu-22-ege/
- explicit TG level markers in task stems
- checklist rule: Krylov PDF/ext tasks -> Easy by default

Notes:
- material 405 is a task-link container; active LMS tasks already cover its links;
- material 610 duplicates material 609;
- tg:ege:68 is a video-only placeholder with the same VK URL as material 609,
  not an assignment, so it is deactivated.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 149

EXT_EASY_UIDS = (
    "ext:d4:kompege:20260602:4708",
    "ext:d4:kompege:20260602:4794",
)

WP_NAV_EASY_KOMPEGE = {"4793", "4795", "4798"}

TG_EASY_UIDS = ("tg:ege:423", "tg:ege:424", "tg:ege:425", "tg:ege:426", "tg:ege:960")
TG_MEDIUM_UIDS = ("tg:ege:733", "tg:ege:886", "tg:ege:893")
TG_PLACEHOLDER_UIDS = ("tg:ege:68",)

MATERIAL_PLAN = {
    404: ("Теория", "required", True, 0),
    405: ("Разбор типовых заданий", "required", False, 1),
    609: ("Задание 22. Параллельные и последовательные процессы.", "recommended", True, 2),
    610: ("Задание 22. Параллельные и последовательные процессы.", "required", False, 3),
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
        checks["active total before = 89 or 90"] = before_total in (89, 90)

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

        section("Deactivate placeholders")
        cur.execute(
            """
            UPDATE tasks
            SET is_active=false, requirement_level='recommended'
            WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true
            """,
            (COURSE_ID, list(TG_PLACEHOLDER_UIDS)),
        )
        print(f"TG video placeholders deactivated: {cur.rowcount}")
        checks["tg placeholder inactive update = 0 or 1"] = cur.rowcount in (0, 1)

        section("Difficulty")
        cur.execute(
            """
            UPDATE tasks SET difficulty_id=3
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'ext:d4:kompege:%%'
                OR external_uid ILIKE 'ext:d4:sdamgia:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"ext d4 baseline -> diff=3: {cur.rowcount}")
        checks["ext baseline medium = 13"] = cur.rowcount == 13

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_EASY_UIDS)),
        )
        print(f"ext easy -> diff=2: {cur.rowcount}")
        checks["ext easy = 2"] = cur.rowcount == 2

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND NOT (
                task_content->>'source_kind'='kompege'
                AND task_content->>'source_task_id'=ANY(%s)
              )
            """,
            (COURSE_ID, list(WP_NAV_EASY_KOMPEGE)),
        )
        print(f"wp_nav hard -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 45"] = cur.rowcount == 45

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
        checks["wp_nav easy = 3"] = cur.rowcount == 3

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
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG easy -> diff=2: {cur.rowcount}")
        checks["tg easy = 5"] = cur.rowcount == 5

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG medium -> diff=3: {cur.rowcount}")
        checks["tg medium = 3"] = cur.rowcount == 3

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"diff<4 active tasks -> required: {cur.rowcount}")
        checks["diff<4 required = 44"] = cur.rowcount == 44

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 45"] = cur.rowcount == 45

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
        checks["active task ids = 89"] = len(task_ids) == 89

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
            (2, "required", 30, 1, 30),
            (3, "recommended", 0, None, None),
            (3, "required", 14, 31, 44),
            (4, "recommended", 45, 45, 89),
        ] or after_rows == [
            (2, "required", 30, 1, 30),
            (3, "required", 14, 31, 44),
            (4, "recommended", 45, 45, 89),
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
            ("recommended", True, 1),
            ("required", True, 1),
            ("required", False, 2),
        ]

        cur.execute(
            """
            SELECT count(*)
            FROM tasks
            WHERE course_id=%s AND external_uid='tg:ege:68' AND is_active=false
            """,
            (COURSE_ID,),
        )
        checks["tg:ege:68 inactive"] = cur.fetchone()[0] == 1

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
