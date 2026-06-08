# -*- coding: utf-8 -*-
"""Курс 145 (Задание 17, обработка числовых последовательностей) — нормализация.

Источники:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-17-ege/
- явные TG-маркеры в stem
- правило чек-листа: Крылов PDF/ext -> Легко

Особенности:
- вводные lms:c145:vvod:01-07 покрывают материал "Задания для подготовки";
- yandex:c01534c6-0b3e-4da7-9d99-6c8d759babaf:17 отсутствует в LMS;
- material id=388 — контейнер вводных задач, id=592 — дубль видео id=590.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 145

EXT_EASY_UIDS = ("ext:d4:kompege:20260602:25356",)

WP_NAV_EASY_STIDS = {"1970", "1993", "1995", "1998", "2003"}
WP_NAV_MEDIUM_YANDEX = {"a97d888a-5402-4044-bb08-35bcc66f9ec7:17"}

CRYLOV_DIRECT_EASY_UIDS = ("crylov:v5t17", "crylov:v16t17")
CRYLOV_DIRECT_MEDIUM_UIDS = ("crylov:v11t17",)

TG_EASY_UIDS = ("tg:ege:934", "tg:ege:568")
TG_MEDIUM_UIDS = ("tg:ege:583", "tg:ege:559", "tg:ege:515")

MATERIAL_PLAN = {
    387: ("Примеры решений заданий", "required", True, 0),
    388: ("Задания для подготовки", "required", False, 1),
    587: ("Инструкция по работе с файлами на ЕГЭ", "required", True, 2),
    588: ("Как правильно указать путь до файла. Как сделать так, чтобы файл и программа оказались в одной папке", "required", True, 3),
    589: ("Как образуются пары в задании 17. На примере футбольных команд.", "required", True, 4),
    590: ("Вспомогательные примеры для решения 17 заданий.", "required", True, 5),
    591: ("Решение заданий 17 с помощью Excel.", "recommended", True, 6),
    592: ("Вспомогательные примеры для решения 17 заданий.", "required", False, 7),
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
        checks["active total stable = 67"] = before_total == 67

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
                external_uid ILIKE 'ext:d4:polyakov:%%'
                OR external_uid ILIKE 'ext:d4:sdamgia:%%'
                OR external_uid ILIKE 'ext:calib:yandex:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"ext polyakov/sdamgia/yandex Средние -> diff=3: {cur.rowcount}")
        checks["ext medium = 6"] = cur.rowcount == 6

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_EASY_UIDS)),
        )
        print(f"ext Простые -> diff=2: {cur.rowcount}")
        checks["ext easy = 1"] = cur.rowcount == 1

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
            (COURSE_ID, list(WP_NAV_EASY_STIDS), list(WP_NAV_MEDIUM_YANDEX)),
        )
        print(f"wp_nav Сложные -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 19"] = cur.rowcount == 19

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
        print(f"wp_nav Средние -> diff=3: {cur.rowcount}")
        checks["wp_nav medium = 1"] = cur.rowcount == 1

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
            (COURSE_ID, list(CRYLOV_DIRECT_EASY_UIDS)),
        )
        print(f"Крылов direct простой -> diff=2: {cur.rowcount}")
        checks["crylov direct easy = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(CRYLOV_DIRECT_MEDIUM_UIDS)),
        )
        print(f"Крылов direct средний -> diff=3: {cur.rowcount}")
        checks["crylov direct medium = 1"] = cur.rowcount == 1

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG Уровень простой/легкий -> diff=2: {cur.rowcount}")
        checks["tg easy = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG Уровень средний -> diff=3: {cur.rowcount}")
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
        checks["diff<4 required = 48"] = cur.rowcount == 48

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 19"] = cur.rowcount == 19

        section("Переупорядочивание")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c145:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 7"] = len(vvod_ids) == 7

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c145:vvod:%%'
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        practice_ids = [row[0] for row in cur.fetchall()]
        checks["practice count = 60"] = len(practice_ids) == 60
        cur.execute("UPDATE tasks SET order_position=order_position+2000 WHERE course_id=%s", (COURSE_ID,))
        for pos, task_id in enumerate(vvod_ids + practice_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (pos, task_id))
        print(f"vvod={len(vvod_ids)}, practice={len(practice_ids)}")

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
            (2, "required", 35, 1, 37),
            (3, "required", 13, 3, 48),
            (4, "recommended", 19, 49, 67),
        ]

        cur.execute(
            """
            SELECT min(order_position), max(order_position), count(*)
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c145:vvod:%%'
            """,
            (COURSE_ID,),
        )
        checks["vvod positions 1-7"] = cur.fetchone() == (1, 7, 7)

        cur.execute(
            """
            SELECT difficulty_id, requirement_level, count(*), min(order_position), max(order_position)
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c145:vvod:%%'
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        practice_blocks = cur.fetchall()
        for row in practice_blocks:
            print(" practice", row)
        checks["practice blocks"] = practice_blocks == [
            (2, "required", 30, 8, 37),
            (3, "required", 11, 38, 48),
            (4, "recommended", 19, 49, 67),
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
            sum(1 for r in materials if r[3]) == 6 and
            sum(1 for r in materials if not r[3]) == 2
        )
        checks["materials mixed req"] = (
            sum(1 for r in materials if r[3] and r[2] == "required") == 5 and
            sum(1 for r in materials if r[3] and r[2] == "recommended") == 1
        )
        checks["practice/duplicate materials inactive"] = all(
            any(r[0] == mid and not r[3] for r in materials)
            for mid in (388, 592)
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
