# -*- coding: utf-8 -*-
"""Курс 146 (Задание 18, жадные алгоритмы) — нормализация.

Источники:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-18-ege/
- явные TG-маркеры в stem или исходный task_id в stem
- правило чек-листа: Крылов PDF/ext -> Легко

Особенности:
- missing-задач по навигатору нет;
- material id=390 — контейнер ссылок на задачи, id=595 — дубль видео id=594.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 146

EXT_EASY_UIDS = (
    "ext:d4:kompege:20260602:17",
    "ext:d4:kompege:20260602:930",
)

WP_NAV_EASY_STIDS = {"410", "938", "939"}

TG_EASY_UIDS = ("tg:ege:422", "tg:ege:421", "tg:ege:420")
TG_MEDIUM_UIDS = (
    "tg:ege:742", "tg:ege:544", "tg:ege:260", "tg:ege:223",
    "tg:ege:221", "tg:ege:220", "tg:ege:66",
)

MATERIAL_PLAN = {
    389: ("Примеры решений заданий", "required", True, 0),
    390: ("Задания для подготовки", "required", False, 1),
    593: ("Агрегатные функции в Excel", "required", True, 2),
    594: ("Задание 18. Динамическое программирование.", "required", True, 3),
    595: ("Задание 18. Динамическое программирование.", "required", False, 4),
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
    raise RuntimeError("DATABASE_URL не найден в .env")


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

        section("Снимок ДО")
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
        print(f"Активных задач ДО: {before_total}")
        checks["active total stable = 92"] = before_total == 92

        section("Материалы")
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
            UPDATE tasks SET difficulty_id=3
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'ext:d4:kompege:%%'
                OR external_uid ILIKE 'ext:d4:polyakov:%%'
                OR external_uid ILIKE 'ext:d4:sdamgia:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"ext:d4 базово -> diff=3: {cur.rowcount}")
        checks["ext baseline medium = 15"] = cur.rowcount == 15

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_EASY_UIDS)),
        )
        print(f"ext Простые -> diff=2: {cur.rowcount}")
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
            (COURSE_ID, list(WP_NAV_EASY_STIDS)),
        )
        print(f"wp_nav Сложные -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 44"] = cur.rowcount == 44

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_kind'='kompege'
              AND task_content->>'source_task_id'=ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_EASY_STIDS)),
        )
        print(f"wp_nav Простые -> diff=2: {cur.rowcount}")
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
        print(f"Крылов PDF/ext -> diff=2: {cur.rowcount}")
        checks["crylov pdf/ext = 20"] = cur.rowcount == 20

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG простые/лёгкие -> diff=2: {cur.rowcount}")
        checks["tg easy = 3"] = cur.rowcount == 3

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG средние или без маркера -> diff=3: {cur.rowcount}")
        checks["tg medium = 7"] = cur.rowcount == 7

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
        checks["diff=4 recommended = 44"] = cur.rowcount == 44

        section("Переупорядочивание")
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
        checks["active task ids = 92"] = len(task_ids) == 92
        cur.execute("UPDATE tasks SET order_position=order_position+2000 WHERE course_id=%s", (COURSE_ID,))
        for pos, task_id in enumerate(task_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (pos, task_id))
        print(f"active tasks reordered={len(task_ids)}")

        section("Снимок ПОСЛЕ")
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
            (2, "required", 28, 1, 28),
            (3, "required", 20, 29, 48),
            (4, "recommended", 44, 49, 92),
        ]

        cur.execute(
            """
            SELECT order_position, count(*)
            FROM tasks WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*) > 1
            """,
            (COURSE_ID,),
        )
        checks["нет дублей task order"] = len(cur.fetchall()) == 0

        cur.execute(
            """
            SELECT id, title, requirement_level, is_active, order_position
            FROM materials WHERE course_id=%s ORDER BY order_position, id
            """,
            (COURSE_ID,),
        )
        materials = cur.fetchall()
        print("\nМатериалы ПОСЛЕ:")
        for row in materials:
            print(" ", row)
        checks["materials active/inactive"] = (
            sum(1 for r in materials if r[3]) == 3 and
            sum(1 for r in materials if not r[3]) == 2
        )
        checks["materials active required"] = (
            sum(1 for r in materials if r[3] and r[2] == "required") == 3
        )
        checks["practice/duplicate materials inactive"] = all(
            any(r[0] == mid and not r[3] for r in materials)
            for mid in (390, 595)
        )

        section("Проверки")
        all_ok = True
        for name, ok in checks.items():
            print(f"[{'OK' if ok else 'FAIL'}] {name}")
            all_ok = all_ok and ok

        if all_ok and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: COMMIT.")
        elif all_ok:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN успешен. Запусти с --apply.")
        else:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: проверки не пройдены, ROLLBACK.")
            sys.exit(1)
    except Exception:
        conn.rollback()
        print("\nРЕЗУЛЬТАТ: ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
