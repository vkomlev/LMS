# -*- coding: utf-8 -*-
"""Course 147 (tasks 19-21, game theory) normalization.

Sources:
- nav_parser on https://victor-komlev.ru/navigator-po-zadaniyu-19-21-ege/
- explicit TG level markers in task stems
- checklist rule: Krylov PDF/ext tasks -> Easy by default

Notes:
- material 392 is a task-link container; active LMS tasks already cover its links;
- material 604 duplicated material 598 and is reused for the missing lru_cache
  Telegram material from the navigator;
- sdamgia 28087/28093/28099 come from the "oral solutions" content block,
  not from the navigator "hard" section, so they stay Medium.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 147

WP_NAV_EASY_KOMPEGE = {"18", "63", "411", "840", "841"}
WP_NAV_MEDIUM_YANDEX = {
    "5a55834b-8221-4fe0-bdb9-f5b356188024:19",
    "a97d888a-5402-4044-bb08-35bcc66f9ec7:19",
}
WP_NAV_MEDIUM_ORAL_SDAMGIA = {"28087", "28093", "28099"}

EXT_HARD_UIDS = (
    "ext:d4:sdamgia:20260602:47016",
    "ext:d4:sdamgia:20260602:58527",
)

CRYLOV_DIRECT_EASY_UIDS = ("crylov:v16t19", "crylov:v1t19")

TG_EASY_UIDS = ("tg:ege:575", "tg:ege:574", "tg:ege:476", "tg:ege:438")
TG_MEDIUM_UIDS = (
    "tg:ege:987",
    "tg:ege:793",
    "tg:ege:592",
    "tg:ege:545",
    "tg:ege:518",
    "tg:ege:445",
)
TG_HARD_UIDS = ("tg:ege:460",)

MATERIAL_PLAN = {
    391: {
        "external_uid": "wp:mat:komlev:zadanie-19-21-ege-po-informatike-teoriya-igr:0",
        "title": "Теория",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 0,
    },
    392: {
        "external_uid": "wp:mat:komlev:zadanie-19-21-ege-po-informatike-teoriya-igr:1",
        "title": "Разбор типовых заданий",
        "requirement_level": "required",
        "is_active": False,
        "order_position": 1,
    },
    596: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:0",
        "title": "Разбор заданий 19-21 (одна куча, устное решение)",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 2,
    },
    597: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:1",
        "title": "Дополнительно разбираю устный способ решения заданий 19-21 без введения в теорию игр",
        "requirement_level": "recommended",
        "is_active": True,
        "order_position": 3,
    },
    598: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:2",
        "title": "Разбор решения заданий 19-21 (одна куча) с помощью рекурсивной функции.",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 4,
    },
    599: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:3",
        "title": "Решение заданий 19-21 через написание рекурсивной функции.",
        "requirement_level": "recommended",
        "is_active": True,
        "order_position": 5,
    },
    600: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:4",
        "title": "Задания 19-21. пишем программу по устному алгоритму. Ускоряем с помощью кэша.",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 6,
    },
    601: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:5",
        "title": "Ускорение рекурсивного способа решения заданий 19-21 с помощью кэша",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 7,
    },
    604: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:6",
        "title": "Что делать, если не работает lru_cache?",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 8,
        "type": "video",
        "content": {
            "sources": [
                {
                    "url": "https://t.me/cyberguru_ege/158",
                    "type": "url",
                    "quality": None,
                    "file_path": None,
                    "thumbnail_url": None,
                    "duration_seconds": None,
                    "telegram_file_id": None,
                }
            ],
            "default_source": 0,
        },
    },
    602: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:7",
        "title": "Решение заданий 19-21 на две кучи с помощью рекурсивной функции",
        "requirement_level": "recommended",
        "is_active": True,
        "order_position": 9,
    },
    603: {
        "external_uid": "wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:8",
        "title": "Задание 19-21. Пишем программу на две кучи",
        "requirement_level": "required",
        "is_active": True,
        "order_position": 10,
    },
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
        checks["active total stable = 140"] = before_total == 140

        section("Materials")
        cur.execute(
            """
            UPDATE materials
            SET external_uid = 'tmp:c147:material:' || id::text
            WHERE course_id=%s AND id IN (602, 603, 604)
            """,
            (COURSE_ID,),
        )
        checks["material temp uid updates = 3"] = cur.rowcount == 3

        for mat_id, plan in MATERIAL_PLAN.items():
            values = {
                "title": plan["title"],
                "external_uid": plan["external_uid"],
                "requirement_level": plan["requirement_level"],
                "is_active": plan["is_active"],
                "order_position": plan["order_position"],
            }
            if "content" in plan:
                cur.execute(
                    """
                    UPDATE materials
                    SET title=%(title)s,
                        external_uid=%(external_uid)s,
                        requirement_level=%(requirement_level)s,
                        is_active=%(is_active)s,
                        order_position=%(order_position)s,
                        type=%(type)s,
                        content=%(content)s
                    WHERE course_id=%(course_id)s AND id=%(mat_id)s
                    """,
                    {
                        **values,
                        "type": plan["type"],
                        "content": Json(plan["content"]),
                        "course_id": COURSE_ID,
                        "mat_id": mat_id,
                    },
                )
            else:
                cur.execute(
                    """
                    UPDATE materials
                    SET title=%(title)s,
                        external_uid=%(external_uid)s,
                        requirement_level=%(requirement_level)s,
                        is_active=%(is_active)s,
                        order_position=%(order_position)s
                    WHERE course_id=%(course_id)s AND id=%(mat_id)s
                    """,
                    {**values, "course_id": COURSE_ID, "mat_id": mat_id},
                )
            print(
                f"id={mat_id}: req={plan['requirement_level']}, "
                f"active={plan['is_active']}, pos={plan['order_position']}: {cur.rowcount}"
            )
            checks[f"material {mat_id} updated"] = cur.rowcount == 1

        section("Difficulty")
        cur.execute(
            """
            UPDATE tasks SET difficulty_id=3
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'ext:d4:kompege:%%'
                OR external_uid ILIKE 'ext:d4:polyakov:%%'
                OR external_uid ILIKE 'ext:d4:sdamgia:%%'
                OR external_uid ILIKE 'ext:calib:yandex:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"ext d4/calib baseline -> diff=3: {cur.rowcount}")
        checks["ext baseline medium = 9"] = cur.rowcount == 9

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
                task_content->>'source_kind'='kompege'
                AND task_content->>'source_task_id'=ANY(%s)
              )
              AND NOT (
                task_content->>'source_kind'='yandex'
                AND task_content->>'source_task_id'=ANY(%s)
              )
              AND NOT (
                task_content->>'source_kind'='sdamgia'
                AND task_content->>'source_task_id'=ANY(%s)
              )
            """,
            (
                COURSE_ID,
                list(WP_NAV_EASY_KOMPEGE),
                list(WP_NAV_MEDIUM_YANDEX),
                list(WP_NAV_MEDIUM_ORAL_SDAMGIA),
            ),
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
        checks["wp_nav easy = 5"] = cur.rowcount == 5

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=3
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND (
                (
                  task_content->>'source_kind'='yandex'
                  AND task_content->>'source_task_id'=ANY(%s)
                )
                OR (
                  task_content->>'source_kind'='sdamgia'
                  AND task_content->>'source_task_id'=ANY(%s)
                )
              )
            """,
            (COURSE_ID, list(WP_NAV_MEDIUM_YANDEX), list(WP_NAV_MEDIUM_ORAL_SDAMGIA)),
        )
        print(f"wp_nav medium -> diff=3: {cur.rowcount}")
        checks["wp_nav medium = 5"] = cur.rowcount == 5

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
        checks["crylov pdf/ext = 60"] = cur.rowcount == 60

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(CRYLOV_DIRECT_EASY_UIDS)),
        )
        print(f"Krylov direct easy -> diff=2: {cur.rowcount}")
        checks["crylov direct easy = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG easy -> diff=2: {cur.rowcount}")
        checks["tg easy = 4"] = cur.rowcount == 4

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
        checks["diff<4 required = 92"] = cur.rowcount == 92

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 48"] = cur.rowcount == 48

        section("Reorder")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c147:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 3"] = len(vvod_ids) == 3

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c147:vvod:%%'
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        practice_ids = [row[0] for row in cur.fetchall()]
        checks["practice count = 137"] = len(practice_ids) == 137
        task_ids = vvod_ids + practice_ids
        checks["active task ids = 140"] = len(task_ids) == 140

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
            (2, "required", 74, 1, 74),
            (3, "required", 18, 75, 92),
            (4, "recommended", 48, 93, 140),
        ]

        cur.execute(
            """
            SELECT id, external_uid, order_position
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c147:vvod:%%'
            ORDER BY order_position
            """,
            (COURSE_ID,),
        )
        vvod_after = cur.fetchall()
        print("vvod:", vvod_after)
        checks["vvod positions = 1..3"] = [row[2] for row in vvod_after] == [1, 2, 3]

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
            ("recommended", True, 3),
            ("required", True, 7),
            ("required", False, 1),
        ]

        cur.execute(
            """
            SELECT content->'sources'->0->>'url'
            FROM materials
            WHERE course_id=%s AND id=604 AND external_uid='wp:mat:komlev:navigator-po-zadaniyu-19-21-ege:6'
            """,
            (COURSE_ID,),
        )
        checks["material 604 is lru_cache link"] = cur.fetchone()[0] == "https://t.me/cyberguru_ege/158"

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
