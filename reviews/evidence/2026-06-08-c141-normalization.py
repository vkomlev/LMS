# -*- coding: utf-8 -*-
"""Курс 141 (Задание 10, поиск информации в документах) — нормализация.

Источник:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-10-ege/
- чек-лист docs/specs/2026-06-07-ege-normalization-checklist.md

Навигатор:
- Простые: 5 kompege -> diff=2
- Средние: 4 sdamgia -> diff=3
- Сложные: 45 заданий -> diff=4

Особенности:
- вводных lms:tsk109:c141:* нет;
- Крылов PDF + ext (20) -> diff=2;
- tg:ege:889 содержит "Уровень простой" -> diff=2;
- материал id=368 "Задания для подготовки" уже inactive: ссылки перенесены в tasks.
"""
import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 141

WP_NAV_EASY_STIDS = {"9", "93", "401", "427", "478"}

WP_NAV_HARD_STIDS = {
    "6761", "5900", "4322", "1341", "3363", "575", "462", "302", "267", "54",
    "7859", "7858", "7326", "7325", "7324", "7323", "7322", "7321", "7320",
    "7319", "7318", "7317", "6334", "6333", "6332", "6331", "6330", "6329",
    "6327",
    "0acc2e28-7bb8-4afe-855a-4633ea81bfce",
    "2bd40d49-4872-4390-b133-6f5243461e7a",
    "f6e4c51c-dbe7-4d78-b47d-e3ec5c5f5eb4",
    "e0f7e2d7-267b-4cc0-8f7e-6840fc0a0920",
    "abcb1c2b-320b-4dc1-ae72-1db00837e1ca",
    "ec719645-fdae-4c3c-8305-b71ac5ca42d6",
    "28ce3944-786b-460a-b998-554358a6199c",
    "b94cf097-23b0-4bc2-81c2-ab768aab9811",
    "514fc6b4-eac4-4cc8-bf20-b764f1437253",
    "37af09da-66f9-4413-9977-4182f527a0d7",
    "9e0fad07-ed42-4b8f-898a-9629c80c205e",
    "fa3bc5ed-9188-4ee5-96cc-31c1d85da508",
    "6778a0c6-3fc0-40e6-a68c-ba3921aadd2e",
    "7eae174e-0f5d-41f9-b838-0deb4be732bc",
    "b75e33aa-b533-40bc-bd8e-bbc19c774e9a",
}

SDAMGIA_HARD_UID = "ext:d4:sdamgia:20260602:72568"
TG_EASY_UID = "tg:ege:889"


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
    print(f"\n── {title} {'─' * max(0, 55 - len(title))}")


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")
        cur.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")

        section("Снимок ДО — задачи")
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
        before_active = cur.fetchone()[0]
        print(f"  active total: {before_active}")
        checks["active before = 75"] = before_active == 75

        section("Материалы")
        material_updates = {
            366: ("Теоретический блок", "required", True, 0),
            367: ("Разбор типовых заданий", "required", True, 1),
            368: ("Задания для подготовки", "required", False, 2),
        }
        for mat_id, (title, req, active, pos) in material_updates.items():
            cur.execute(
                """
                UPDATE materials
                SET title=%s, requirement_level=%s, is_active=%s, order_position=%s
                WHERE id=%s AND course_id=%s
                """,
                (title, req, active, pos, mat_id, COURSE_ID),
            )
            print(f"  id={mat_id}: {cur.rowcount}")
            checks[f"material {mat_id} updated"] = cur.rowcount == 1

        section("Difficulty")
        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_task_id' = ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_EASY_STIDS)),
        )
        print(f"  wp_nav Простые -> diff=2: {cur.rowcount}")
        checks["wp_nav easy = 5"] = cur.rowcount == 5

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_task_id' = ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_HARD_STIDS)),
        )
        print(f"  wp_nav Сложные -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 44"] = cur.rowcount == 44

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid=%s AND is_active=true
            """,
            (COURSE_ID, SDAMGIA_HARD_UID),
        )
        print(f"  sdamgia:72568 -> diff=4: {cur.rowcount}")
        checks["sdamgia hard = 1"] = cur.rowcount == 1

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
        print(f"  Крылов PDF/ext -> diff=2: {cur.rowcount}")
        checks["crylov = 20"] = cur.rowcount == 20

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=%s AND is_active=true",
            (COURSE_ID, TG_EASY_UID),
        )
        print(f"  tg:ege:889 -> diff=2: {cur.rowcount}")
        checks["tg easy = 1"] = cur.rowcount == 1

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"  diff<4 -> required: {cur.rowcount}")
        checks["diff<4 required = 30"] = cur.rowcount == 30

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"  diff=4 -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 45"] = cur.rowcount == 45

        section("Переупорядочивание")
        cur.execute(
            """
            SELECT id FROM tasks
            WHERE course_id=%s AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        active_ids = [row[0] for row in cur.fetchall()]
        cur.execute("UPDATE tasks SET order_position=order_position+2000 WHERE course_id=%s", (COURSE_ID,))
        for pos, task_id in enumerate(active_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (pos, task_id))

        section("Снимок ПОСЛЕ — задачи")
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

        cur.execute(
            """
            SELECT order_position, count(*)
            FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*) > 1
            """,
            (COURSE_ID,),
        )
        checks["нет дублей task order"] = len(cur.fetchall()) == 0

        cur.execute(
            """
            SELECT difficulty_id, requirement_level, count(*),
                   min(order_position), max(order_position)
            FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        final_rows = cur.fetchall()
        checks["final blocks"] = final_rows == [
            (2, "required", 26, 1, 26),
            (3, "required", 4, 27, 30),
            (4, "recommended", 45, 31, 75),
        ]

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
        checks["materials active 2 inactive 1"] = (
            sum(1 for r in materials if r[3]) == 2 and
            sum(1 for r in materials if not r[3]) == 1
        )

        section("Проверки")
        all_ok = True
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")
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
