# -*- coding: utf-8 -*-
"""Course 153 (task 26, data processing) normalization.

Sources:
- nav_parser on https://victor-komlev.ru/navigator-po-zadaniyu-26-ege/
- explicit TG/Krylov level markers in task stems
- checklist rule: Krylov PDF/ext tasks -> Easy by default

Notes:
- material 411 is a task-link container and is inactive;
- tg:ege:440 is a helper artifact ("Вспомогательное задание 26_5") and is inactive;
- 61 hard navigator tasks are absent in LMS and remain in the missing-task registry.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 153

WP_NAV_EASY = {
    ("sdamgia", "35484"),
    ("sdamgia", "27423"),
    ("kompege", "17881"),
    ("kompege", "21424"),
    ("sdamgia", "29674"),
    ("sdamgia", "33198"),
    ("sdamgia", "33528"),
    ("sdamgia", "36039"),
    ("sdamgia", "37161"),
    ("sdamgia", "47230"),
    ("sdamgia", "59822"),
    ("sdamgia", "59851"),
    ("sdamgia", "60967"),
    ("sdamgia", "68527"),
    ("sdamgia", "69935"),
    ("sdamgia", "70553"),
    ("sdamgia", "76129"),
    ("sdamgia", "81492"),
    ("sdamgia", "81493"),
}

WP_NAV_MEDIUM = {
    ("sdamgia", "40742"),
    ("sdamgia", "27886"),
    ("yandex", "b24b2dd9-52dc-42a7-b9f8-766c46e4c737:26"),
    ("sdamgia", "46984"),
    ("sdamgia", "55822"),
    ("sdamgia", "59704"),
    ("sdamgia", "59821"),
    ("sdamgia", "72584"),
    ("sdamgia", "76694"),
    ("sdamgia", "78051"),
    ("sdamgia", "81810"),
    ("sdamgia", "83156"),
}

WP_NAV_HARD = {
    ("kompege", "9077"),
    ("sdamgia", "63075"),
}

TG_INACTIVE_UIDS = ("tg:ege:440",)
TG_EASY_UIDS = ("tg:ege:955", "tg:ege:506", "tg:ege:504")
TG_MEDIUM_UIDS = ("tg:ege:965", "tg:ege:944", "tg:ege:875", "tg:ege:744", "tg:ege:571", "tg:ege:277")
TG_HARD_UIDS = ("tg:ege:983", "tg:ege:971")
CRYLOV_DIRECT_HARD_UIDS = ("crylov:v11t26",)

MATERIAL_PLAN = {
    411: ("required", False, 0),
    631: ("required", True, 1),
    632: ("required", True, 2),
    633: ("required", True, 3),
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


def update_wp_nav(cur, pairs: set[tuple[str, str]], difficulty_id: int) -> int:
    cur.execute(
        """
        UPDATE tasks
        SET difficulty_id=%s
        WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'wp_nav:%%'
          AND (task_content->>'source_kind', task_content->>'source_task_id') IN (
        """
        + ",".join(["(%s,%s)"] * len(pairs))
        + ")",
        (difficulty_id, COURSE_ID, *[item for pair in sorted(pairs) for item in pair]),
    )
    return cur.rowcount


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
        before_rows = cur.fetchall()
        for row in before_rows:
            print(" ", row)

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        before_total = cur.fetchone()[0]
        print(f"Active tasks before: {before_total}")
        checks["active total before = 65 or 66"] = before_total in (65, 66)

        section("Materials")
        for mat_id, (req, active, pos) in MATERIAL_PLAN.items():
            cur.execute(
                """
                UPDATE materials
                SET requirement_level=%s, is_active=%s, order_position=%s
                WHERE course_id=%s AND id=%s
                """,
                (req, active, pos, COURSE_ID, mat_id),
            )
            print(f"id={mat_id}: req={req}, active={active}, pos={pos}: {cur.rowcount}")
            checks[f"material {mat_id} updated"] = cur.rowcount == 1

        section("Deactivate helper TG")
        cur.execute(
            """
            UPDATE tasks
            SET is_active=false, requirement_level='recommended'
            WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true
            """,
            (COURSE_ID, list(TG_INACTIVE_UIDS)),
        )
        print(f"TG helper tasks deactivated: {cur.rowcount}")
        checks["tg helper inactive update = 0 or 1"] = cur.rowcount in (0, 1)

        section("Difficulty")
        wp_easy = update_wp_nav(cur, WP_NAV_EASY, 2)
        print(f"wp_nav easy -> diff=2: {wp_easy}")
        checks["wp_nav easy = 19"] = wp_easy == 19

        wp_medium = update_wp_nav(cur, WP_NAV_MEDIUM, 3)
        print(f"wp_nav medium -> diff=3: {wp_medium}")
        checks["wp_nav medium = 12"] = wp_medium == 12

        wp_hard = update_wp_nav(cur, WP_NAV_HARD, 4)
        print(f"wp_nav hard -> diff=4: {wp_hard}")
        checks["wp_nav hard = 2"] = wp_hard == 2

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
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(CRYLOV_DIRECT_HARD_UIDS)),
        )
        print(f"Krylov direct hard -> diff=4: {cur.rowcount}")
        checks["crylov direct hard = 1"] = cur.rowcount == 1

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
        checks["tg medium = 6"] = cur.rowcount == 6

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG hard -> diff=4: {cur.rowcount}")
        checks["tg hard = 2"] = cur.rowcount == 2

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"diff<4 active tasks -> required: {cur.rowcount}")
        checks["diff<4 required = 60"] = cur.rowcount == 60

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 5"] = cur.rowcount == 5

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
        checks["active task ids = 65"] = len(task_ids) == 65

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
        expected = [
            (2, "required", 42, 1, 42),
            (3, "required", 18, 43, 60),
            (4, "recommended", 5, 61, 65),
        ]
        checks["final counts"] = after_rows == expected or after_rows == [
            (2, "required", 42, 1, 42),
            (3, "recommended", 0, None, None),
            (3, "required", 18, 43, 60),
            (4, "recommended", 5, 61, 65),
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
            ("required", True, 3),
            ("required", False, 1),
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
