# -*- coding: utf-8 -*-
"""Курс 162 (Задание 11, вычисление объёма информации) — нормализация.

Источники:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-11-ege/
- явные TG-маркеры в stem
- правило чек-листа: Крылов PDF -> Легко

Особенности:
- вводные lms:c162:vvod:01-20 уже созданы и покрывают "Вопросы"/"Мини-задания";
- вводные сейчас стоят в конце курса, переносим их на позиции 1-20 без изменения
  содержимого и внутреннего порядка;
- kompege:7032, kompege:1750 и sdamgia:73837 встречаются и в средних, и в
  сложных разделах навигатора, поэтому сохраняют diff=3;
- material id=546 — дубль видео id=545.

Запуск:
  python reviews/evidence/2026-06-08-c162-normalization.py
  python reviews/evidence/2026-06-08-c162-normalization.py --apply
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 162

WP_NAV_EASY_STIDS = {"10", "55", "123", "124", "125"}

WP_NAV_HARD_STIDS = {
    "17552", "6264", "5876", "5061", "4468", "4323", "2119", "1342",
    "825", "623", "576", "423", "18819", "55628", "70538", "7856", "7855",
    "f2ae72ec-dd45-47c0-996a-69d1b524b2e9",
    "7973eeb2-91dd-4de5-ae6f-9c141ca5fa47",
    "658ead8e-8e85-4ad1-a74d-075f3e9f8bf0",
    "a35ac12d-56d0-45d9-87ef-a1d81dbfd6b1",
    "dc1b1d86-189a-40e6-814f-dce8deecb664",
}

EXT_D4_HARD_UIDS = (
    "ext:d4:polyakov:20260602:7857",
    "ext:d4:kompege:20260602:5433",
    "ext:calib:polyakov:20260525:7857",
)

TG_EASY_UIDS = ("tg:ege:573", "tg:ege:503")
TG_MEDIUM_UIDS = ("tg:ege:946", "tg:ege:916", "tg:ege:856")
TG_HARD_UIDS = ("tg:ege:645",)

MATERIAL_PLAN = {
    369: ("Теоретический блок", "required", True, 0),
    370: ("Разбор типовых заданий", "required", True, 1),
    544: ("Решение заданий 11", "recommended", True, 2),
    545: ("Решение заданий 11. Вариант 2", "required", True, 3),
    371: ("Вопросы", "required", False, 4),
    372: ("Мини-задания", "required", False, 5),
    546: ("Решение заданий 11. Вариант 2", "required", False, 6),
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
                   min(order_position) FILTER (WHERE is_active) AS pos_min,
                   max(order_position) FILTER (WHERE is_active) AS pos_max
            FROM tasks
            WHERE course_id=%s
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        for row in cur.fetchall():
            print(f"diff={row[0]} req={row[1]} active={row[2]} pos={row[3]}-{row[4]}")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        before_total = cur.fetchone()[0]
        print(f"Активных задач ДО: {before_total}")

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
            print(f"id={mat_id}: title={title!r}, req={req}, active={active}, pos={pos}: {cur.rowcount}")
            checks[f"material {mat_id} updated"] = cur.rowcount == 1

        section("Difficulty")
        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_kind' = 'kompege'
              AND task_content->>'source_task_id' = ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_EASY_STIDS)),
        )
        print(f"wp_nav Простые -> diff=2: {cur.rowcount}")
        checks["wp_nav easy = 5"] = cur.rowcount == 5

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'pdf:d4:pdf:crylov:%%'
                OR external_uid ILIKE 'ext:pdf:d4:pdf:crylov:%%'
                OR external_uid ILIKE 'crylov:%%'
              )
            """,
            (COURSE_ID,),
        )
        print(f"Крылов PDF/ext -> diff=2: {cur.rowcount}")
        checks["crylov = 21"] = cur.rowcount == 21

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG Уровень легкий -> diff=2: {cur.rowcount}")
        checks["tg easy = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG Уровень средний -> diff=3: {cur.rowcount}")
        checks["tg medium = 3"] = cur.rowcount == 3

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG Уровень сложный -> diff=4: {cur.rowcount}")
        checks["tg hard = 1"] = cur.rowcount == 1

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(EXT_D4_HARD_UIDS)),
        )
        print(f"ext:d4/calib Сложные -> diff=4: {cur.rowcount}")
        checks["ext hard = 3"] = cur.rowcount == 3

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_task_id' = ANY(%s)
            """,
            (COURSE_ID, list(WP_NAV_HARD_STIDS)),
        )
        print(f"wp_nav Сложные -> diff=4: {cur.rowcount}")
        checks["wp_nav hard = 22"] = cur.rowcount == 22

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"diff<4 active tasks -> required: {cur.rowcount}")
        checks["diff<4 required = 65"] = cur.rowcount == 65

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 26"] = cur.rowcount == 26

        section("Переупорядочивание")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c162:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 20"] = len(vvod_ids) == 20

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c162:vvod:%%'
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
                   min(order_position) FILTER (WHERE is_active) AS pos_min,
                   max(order_position) FILTER (WHERE is_active) AS pos_max
            FROM tasks
            WHERE course_id=%s
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        after_rows = cur.fetchall()
        for row in after_rows:
            print(f"diff={row[0]} req={row[1]} active={row[2]} pos={row[3]}-{row[4]}")
        checks["final diff blocks"] = after_rows == [
            (1, "required", 5, 1, 15),
            (2, "required", 40, 3, 48),
            (3, "required", 20, 18, 65),
            (4, "recommended", 26, 66, 91),
        ]

        cur.execute(
            """
            SELECT min(order_position), max(order_position), count(*)
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c162:vvod:%%'
            """,
            (COURSE_ID,),
        )
        checks["vvod positions 1-20"] = cur.fetchone() == (1, 20, 20)

        cur.execute(
            """
            SELECT difficulty_id, requirement_level, count(*), min(order_position), max(order_position)
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c162:vvod:%%'
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
            GROUP BY difficulty_id, requirement_level
            ORDER BY difficulty_id, requirement_level
            """,
            (COURSE_ID,),
        )
        practice_blocks = cur.fetchall()
        for row in practice_blocks:
            print(f"practice diff={row[0]} req={row[1]} active={row[2]} pos={row[3]}-{row[4]}")
        checks["practice blocks"] = practice_blocks == [
            (2, "required", 28, 21, 48),
            (3, "required", 17, 49, 65),
            (4, "recommended", 26, 66, 91),
        ]

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
            SELECT id, title, requirement_level, is_active, order_position
            FROM materials
            WHERE course_id=%s
            ORDER BY order_position, id
            """,
            (COURSE_ID,),
        )
        materials = cur.fetchall()
        print("\nМатериалы ПОСЛЕ:")
        for row in materials:
            print(" ", row)
        checks["materials active/inactive"] = (
            sum(1 for r in materials if r[3]) == 4 and
            sum(1 for r in materials if not r[3]) == 3
        )
        checks["material 544 recommended"] = any(r[0] == 544 and r[2] == "recommended" and r[3] for r in materials)
        checks["material practice inactive"] = all(
            any(r[0] == mid and not r[3] for r in materials)
            for mid in (371, 372, 546)
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
