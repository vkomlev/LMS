# -*- coding: utf-8 -*-
"""Course 152 (task 25, numeric data processing) normalization.

Sources:
- nav_parser on https://victor-komlev.ru/navigator-po-zadaniyu-25-ege/
- explicit TG/Krylov level markers in task stems
- checklist rule: Krylov PDF/ext tasks -> Easy by default

Notes:
- lms:c152:vvod:01-17 are generated introductory/control tasks and stay first;
- TG intro/helper tasks are deactivated because LMS intro tasks cover them;
- material 410 is a task-link container; material 630 duplicates material 628;
- yandex:c01534c6-0b3e-4da7-9d99-6c8d759babaf:25 is absent in LMS and
  remains in the missing-task registry.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 152

EXT_EASY_UIDS = (
    "ext:d4:kompege:20260602:17880",
    "ext:d4:kompege:20260602:20814",
    "ext:d4:sdamgia:20260602:47229",
    "ext:calib:yandex:tier1:20260525:b24b2dd9-52dc-42a7-b9f8-766c46e4c737:25",
)
EXT_HARD_UIDS = ("ext:d4:sdamgia:20260602:33104", "ext:d4:kompege:20260602:23207")

WP_NAV_EASY_YANDEX = {"b24b2dd9-52dc-42a7-b9f8-766c46e4c737:25"}

CRYLOV_DIRECT_EASY_UIDS = ("crylov:v11t25",)

TG_INACTIVE_UIDS = ("tg:ege:331", "tg:ege:427", "tg:ege:428", "tg:ege:429", "tg:ege:737", "tg:ege:979")
TG_EASY_UIDS = ("tg:ege:468", "tg:ege:473")
TG_MEDIUM_UIDS = ("tg:ege:467", "tg:ege:584", "tg:ege:961")
TG_HARD_UIDS = ("tg:ege:593",)

MATERIAL_PLAN = {
    409: ("Теория", "required", True, 0),
    410: ("Типовые решения", "required", False, 1),
    628: ("Задания 25. Как получать делители в очень больших числах. Попарный перебор", "required", True, 2),
    629: ("Лайфхак. Как оценить скорость работы программы. Стоит ли ждать окончания или ваш алгоритм неэффективен и нужно прервать выполнение и подумать над оптимизацией.", "required", True, 3),
    630: ("Задания 25. Как получать делители в очень больших числах. Попарный перебор", "required", False, 4),
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
        checks["active total before = 117 or 123"] = before_total in (117, 123)

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

        section("Deactivate covered TG intro/helper tasks")
        cur.execute(
            """
            UPDATE tasks
            SET is_active=false, requirement_level='recommended'
            WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true
            """,
            (COURSE_ID, list(TG_INACTIVE_UIDS)),
        )
        print(f"TG intro/helper tasks deactivated: {cur.rowcount}")
        checks["tg intro/helper inactive update = 0 or 6"] = cur.rowcount in (0, 6)

        section("Difficulty")
        cur.execute(
            """
            UPDATE tasks SET difficulty_id=3
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'ext:d4:kompege:%%'
                OR external_uid ILIKE 'ext:d4:sdamgia:%%'
                OR external_uid ILIKE 'ext:calib:yandex:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"ext d4/calib baseline -> diff=3: {cur.rowcount}")
        checks["ext baseline medium = 9"] = cur.rowcount == 9

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_EASY_UIDS)),
        )
        print(f"ext easy -> diff=2: {cur.rowcount}")
        checks["ext easy = 4"] = cur.rowcount == 4

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_HARD_UIDS)),
        )
        print(f"ext hard -> diff=4: {cur.rowcount}")
        checks["ext hard = 2"] = cur.rowcount == 2

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND NOT (
                task_content->>'source_kind'='yandex'
                AND task_content->>'source_task_id'=ANY(%s)
              )
            """,
            (COURSE_ID, list(WP_NAV_EASY_YANDEX)),
        )
        print(f"wp_nav hard -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 63"] = cur.rowcount == 63

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_kind'='yandex'
              AND task_content->>'source_task_id'=ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_EASY_YANDEX)),
        )
        print(f"wp_nav easy -> diff=2: {cur.rowcount}")
        checks["wp_nav easy = 1"] = cur.rowcount == 1

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
        checks["tg easy = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG medium -> diff=3: {cur.rowcount}")
        checks["tg medium = 3"] = cur.rowcount == 3

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG hard -> diff=4: {cur.rowcount}")
        checks["tg hard = 1"] = cur.rowcount == 1

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"diff<4 active tasks -> required: {cur.rowcount}")
        checks["diff<4 required = 48"] = cur.rowcount == 48

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 69"] = cur.rowcount == 69

        section("Reorder")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c152:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 17"] = len(vvod_ids) == 17

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c152:vvod:%%'
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        practice_ids = [row[0] for row in cur.fetchall()]
        checks["practice count = 100"] = len(practice_ids) == 100
        task_ids = vvod_ids + practice_ids
        checks["active task ids = 117"] = len(task_ids) == 117

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
            (2, "required", 36, 1, 45),
            (3, "required", 12, 4, 51),
            (4, "recommended", 69, 10, 117),
        ]
        checks["final counts"] = after_rows == expected or after_rows == [
            (2, "recommended", 0, None, None),
            *expected,
        ] or after_rows == [
            (2, "required", 36, 1, 45),
            (3, "recommended", 0, None, None),
            (3, "required", 12, 4, 51),
            (4, "recommended", 69, 10, 117),
        ]

        cur.execute(
            """
            SELECT id, external_uid, order_position
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c152:vvod:%%'
            ORDER BY order_position
            """,
            (COURSE_ID,),
        )
        vvod_after = cur.fetchall()
        checks["vvod positions = 1..17"] = [row[2] for row in vvod_after] == list(range(1, 18))

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
