# -*- coding: utf-8 -*-
"""Курс 142 (Задание 14, позиционные системы счисления) — нормализация.

Источники:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-14-ege/
- явные TG-маркеры в stem
- правило чек-листа: Крылов PDF/ext -> Легко

Особенности:
- вводные lms:c142:vvod:01-11 покрывают "Контрольные вопросы и мини-задания";
- yandex:c01534c6-0b3e-4da7-9d99-6c8d759babaf:14 отсутствует в LMS;
- tg:ege:741 не содержит маркер уровня, но связан с kompege:23752 из раздела
  "Простые" навигатора, поэтому получает diff=2;
- material id=381 — контейнер ссылок на задачи, id=577 — дубль видео id=569.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 142

WP_NAV_EASY_STIDS = {"244", "247"}
EXT_EASY_UIDS = (
    "ext:calib:kompege:20260525:13",
    "ext:d4:kompege:20260602:13",
    "ext:d4:kompege:20260602:242",
    "ext:d4:kompege:20260602:23752",
)
CRYLOV_DIRECT_EASY_UIDS = ("crylov:v5t14", "crylov:v11t14")
CRYLOV_DIRECT_MEDIUM_UIDS = ("crylov:v16t14",)
TG_EASY_UIDS = (
    "tg:ege:892", "tg:ege:741", "tg:ege:538", "tg:ege:537", "tg:ege:534",
    "tg:ege:511", "tg:ege:509", "tg:ege:495", "tg:ege:487", "tg:ege:461",
)
TG_MEDIUM_UIDS = ("tg:ege:585", "tg:ege:542", "tg:ege:531")
TG_HARD_UIDS = ("tg:ege:838",)

MATERIAL_PLAN = {
    378: ("Теория", "required", True, 0),
    379: ("Разбор типовых заданий", "required", True, 1),
    565: ("Представление чисел в различных системах счисления.", "required", True, 2),
    566: ("Перевод чисел в различные системы счисления. Устный способ", "required", True, 3),
    567: ("Разбор некоторых заданий 5 и 14", "recommended", True, 4),
    568: ("Теория для решения заданий 14", "required", True, 5),
    569: ("Решение заданий 14.", "recommended", True, 6),
    570: ("Некоторые тонкости решений заданий 14.", "recommended", True, 7),
    571: ("Как перевести число из произвольной системы счисления в десятичную без функции int().", "recommended", True, 8),
    572: ("Разбор алгоритма перевода числа из произвольной системы счисления в десятичную", "recommended", True, 9),
    573: ("Алгоритм перевода чисел из десятичной системы счисления в произвольную.", "recommended", True, 10),
    574: ("Помощь для решения заданий 14. Пишем функцию для перевода числа из десятичной в произвольную систему счисления.", "required", True, 11),
    575: ("Переводим число из произвольной системы в десятичную. Когда int не помогает.", "required", True, 12),
    576: ("Универсальный код для решения 98% заданий 14.", "required", True, 13),
    380: ("Вопросы", "required", False, 14),
    381: ("Задания для подготовки", "required", False, 15),
    577: ("Решение заданий 14.", "required", False, 16),
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
        checks["active total stable = 161"] = before_total == 161

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
              AND (external_uid ILIKE 'ext:d4:%%' OR external_uid ILIKE 'ext:calib:%%')
            """,
            (COURSE_ID,),
        )
        print(f"ext:d4/ext:calib базово -> diff=3: {cur.rowcount}")
        checks["ext baseline medium = 39"] = cur.rowcount == 39

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_EASY_UIDS)),
        )
        print(f"ext Простые -> diff=2: {cur.rowcount}")
        checks["ext easy = 4"] = cur.rowcount == 4

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
        checks["wp_nav easy = 2"] = cur.rowcount == 2

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
        checks["wp_nav hard = 72"] = cur.rowcount == 72

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
        print(f"TG легкие/простые -> diff=2: {cur.rowcount}")
        checks["tg easy = 10"] = cur.rowcount == 10

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG средние -> diff=3: {cur.rowcount}")
        checks["tg medium = 3"] = cur.rowcount == 3

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG сложные -> diff=4: {cur.rowcount}")
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
        checks["diff<4 required = 88"] = cur.rowcount == 88

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 73"] = cur.rowcount == 73

        section("Переупорядочивание")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c142:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 11"] = len(vvod_ids) == 11

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c142:vvod:%%'
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
            ORDER BY difficulty_id ASC, order_position ASC, id ASC
            """,
            (COURSE_ID,),
        )
        practice_ids = [row[0] for row in cur.fetchall()]
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
            (1, "required", 4, 1, 7),
            (2, "required", 44, 4, 49),
            (3, "required", 40, 11, 88),
            (4, "recommended", 73, 89, 161),
        ]

        cur.execute(
            """
            SELECT min(order_position), max(order_position), count(*)
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c142:vvod:%%'
            """,
            (COURSE_ID,),
        )
        checks["vvod positions 1-11"] = cur.fetchone() == (1, 11, 11)

        cur.execute(
            """
            SELECT difficulty_id, requirement_level, count(*), min(order_position), max(order_position)
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c142:vvod:%%'
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        practice_blocks = cur.fetchall()
        for row in practice_blocks:
            print(" practice", row)
        checks["practice blocks"] = practice_blocks == [
            (2, "required", 38, 12, 49),
            (3, "required", 39, 50, 88),
            (4, "recommended", 73, 89, 161),
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
            sum(1 for r in materials if r[3]) == 14 and
            sum(1 for r in materials if not r[3]) == 3
        )
        checks["materials mixed req"] = (
            sum(1 for r in materials if r[3] and r[2] == "required") == 8 and
            sum(1 for r in materials if r[3] and r[2] == "recommended") == 6
        )
        checks["practice/duplicate materials inactive"] = all(
            any(r[0] == mid and not r[3] for r in materials)
            for mid in (380, 381, 577)
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
