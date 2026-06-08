# -*- coding: utf-8 -*-
"""Course 151 (task 24, text processing) normalization.

Sources:
- nav_parser on https://victor-komlev.ru/navigator-po-zadaniyu-24-ege/
- explicit TG/Krylov level markers in task stems
- checklist rule: Krylov PDF/ext tasks -> Easy by default

Notes:
- lms:c151:vvod:01-19 are generated introductory/control tasks and stay first;
- TG intro/helper tasks are deactivated because LMS intro tasks cover them;
- material 408 is a task-link container; material 627 duplicates material 619.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 151

WP_NAV_EASY_KOMPEGE = {"21", "66", "279", "857", "860"}
WP_NAV_MEDIUM_YANDEX = {"a97d888a-5402-4044-bb08-35bcc66f9ec7:24"}

EXT_HARD_UIDS = (
    "ext:d4:sdamgia:20260602:59847",
    "ext:d4:sdamgia:20260602:76692",
    "ext:d4:sdamgia:20260602:78049",
)

CRYLOV_DIRECT_MEDIUM_UIDS = ("crylov:v11t24", "crylov:v16t24")

TG_INACTIVE_UIDS = ("tg:ege:291", "tg:ege:302", "tg:ege:710", "tg:ege:715")
TG_EASY_UIDS = ("tg:ege:976", "tg:ege:980")
TG_MEDIUM_UIDS = (
    "tg:ege:494",
    "tg:ege:567",
    "tg:ege:799",
    "tg:ege:800",
    "tg:ege:874",
    "tg:ege:954",
    "tg:ege:982",
)
TG_HARD_UIDS = ("tg:ege:839", "tg:ege:840", "tg:ege:964")

MATERIAL_PLAN = {
    407: ("Теория", "required", True, 0),
    408: ("Типовые решения", "required", False, 1),
    617: ("Инструкция по работе с файлами на ЕГЭ", "required", True, 2),
    618: ("Как правильно указать путь до файла. Как сделать так, чтобы файл и программа оказались в одной папке", "required", True, 3),
    619: ("Методы обработки текста в цикле часть 1", "required", True, 4),
    620: ("Методы обработки текста в цикле часть 2", "required", True, 5),
    621: ("Метод скользящих окон для решения заданий 24", "recommended", True, 6),
    622: ("Регулярные выражения для решений 24", "recommended", True, 7),
    623: ("Метод скользящих окон в конкретном задании", "required", True, 8),
    624: ("Метод скользящих окон 2", "required", True, 9),
    625: ("Тренажер для регулярных выражений", "required", True, 10),
    626: ("Регулярные выражения для решения 24 заданий.", "required", True, 11),
    627: ("Методы обработки текста в цикле часть 1", "required", False, 12),
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
        checks["active total before = 142 or 146"] = before_total in (142, 146)

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
        checks["tg intro/helper inactive update = 0 or 4"] = cur.rowcount in (0, 4)

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
        checks["ext baseline medium = 7"] = cur.rowcount == 7

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_HARD_UIDS)),
        )
        print(f"ext hard -> diff=4: {cur.rowcount}")
        checks["ext hard = 3"] = cur.rowcount == 3

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND NOT (
                task_content->>'source_kind'='kompege'
                AND task_content->>'source_task_id'=ANY(%s)
              )
              AND NOT (
                task_content->>'source_kind'='yandex'
                AND task_content->>'source_task_id'=ANY(%s)
              )
            """,
            (COURSE_ID, list(WP_NAV_EASY_KOMPEGE), list(WP_NAV_MEDIUM_YANDEX)),
        )
        print(f"wp_nav hard -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 76"] = cur.rowcount == 76

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
              AND task_content->>'source_kind'='yandex'
              AND task_content->>'source_task_id'=ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_MEDIUM_YANDEX)),
        )
        print(f"wp_nav medium -> diff=3: {cur.rowcount}")
        checks["wp_nav medium = 1"] = cur.rowcount == 1

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND is_active=true
              AND external_uid ILIKE 'pdf:d4:pdf:crylov:%%'
            """,
            (COURSE_ID,),
        )
        print(f"Krylov PDF -> diff=2: {cur.rowcount}")
        checks["crylov pdf = 20"] = cur.rowcount == 20

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(CRYLOV_DIRECT_MEDIUM_UIDS)),
        )
        print(f"Krylov direct medium -> diff=3: {cur.rowcount}")
        checks["crylov direct medium = 2"] = cur.rowcount == 2

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
        checks["tg medium = 7"] = cur.rowcount == 7

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG hard -> diff=4: {cur.rowcount}")
        checks["tg hard = 3"] = cur.rowcount == 3

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
        checks["diff=4 recommended = 82"] = cur.rowcount == 82

        section("Reorder")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c151:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 19"] = len(vvod_ids) == 19

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c151:vvod:%%'
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        practice_ids = [row[0] for row in cur.fetchall()]
        checks["practice count = 123"] = len(practice_ids) == 123
        task_ids = vvod_ids + practice_ids
        checks["active task ids = 142"] = len(task_ids) == 142

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
        checks["final counts"] = after_rows == [
            (1, "required", 1, 12, 12),
            (2, "required", 36, 1, 46),
            (3, "recommended", 0, None, None),
            (3, "required", 23, 3, 60),
            (4, "recommended", 82, 61, 142),
        ] or after_rows == [
            (1, "required", 1, 12, 12),
            (2, "required", 36, 1, 46),
            (3, "required", 23, 3, 60),
            (4, "recommended", 82, 61, 142),
        ]

        cur.execute(
            """
            SELECT id, external_uid, order_position
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c151:vvod:%%'
            ORDER BY order_position
            """,
            (COURSE_ID,),
        )
        vvod_after = cur.fetchall()
        checks["vvod positions = 1..19"] = [row[2] for row in vvod_after] == list(range(1, 20))

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
            ("recommended", True, 2),
            ("required", True, 9),
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
