# -*- coding: utf-8 -*-
"""Курс 163 (Задание 12, машина Тьюринга) — нормализация.

Источники:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-12-ege/
- явные TG-маркеры в stem
- правило чек-листа: Крылов PDF/direct -> Легко

Особенности:
- вводных заданий нет;
- в навигаторе нет сложного раздела, но TG-задача tg:ege:554 имеет явный
  маркер "Уровень сложный";
- ссылка "Разбор типовых заданий" в навигаторе ведёт на страницу задания 11,
  поэтому не создаём её как материал курса 12;
- material id=548 — дубль видео id=547.

Запуск:
  python reviews/evidence/2026-06-08-c163-normalization.py
  python reviews/evidence/2026-06-08-c163-normalization.py --apply
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 163

KOMPEGE_EASY_UIDS = (
    "ext:d4:kompege:20260602:23727",
    "ext:d4:kompege:20260602:23750",
    "ext:d4:kompege:20260602:23812",
    "ext:d4:kompege:20260602:23813",
)
KOMPEGE_MEDIUM_UIDS = ("ext:d4:kompege:20260602:23814",)

TG_EASY_UIDS = ("tg:ege:959", "tg:ege:475")
TG_MEDIUM_UIDS = ("tg:ege:947", "tg:ege:847", "tg:ege:530", "tg:ege:407")
TG_HARD_UIDS = ("tg:ege:554",)

MATERIAL_PLAN = {
    373: ("Теоретический блок", "required", True, 0),
    374: ("Порядок устного решения", "recommended", True, 1),
    375: ("Python класс для решения", "recommended", True, 2),
    547: ("Решение заданий 12", "required", True, 3),
    376: ("Обзор модели", "required", False, 4),
    377: ("Задания для подготовки", "required", False, 5),
    548: ("Решение заданий 12", "required", False, 6),
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
        checks["active total stable = 34"] = before_total == 34

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
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(KOMPEGE_EASY_UIDS)),
        )
        print(f"Kompege Простые -> diff=2: {cur.rowcount}")
        checks["kompege easy = 4"] = cur.rowcount == 4

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(KOMPEGE_MEDIUM_UIDS)),
        )
        print(f"Kompege Средние -> diff=3: {cur.rowcount}")
        checks["kompege medium = 1"] = cur.rowcount == 1

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
        print(f"Крылов PDF/direct -> diff=2: {cur.rowcount}")
        checks["crylov = 22"] = cur.rowcount == 22

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG Уровень легкий/простой -> diff=2: {cur.rowcount}")
        checks["tg easy = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG Уровень/Сложность средняя -> diff=3: {cur.rowcount}")
        checks["tg medium = 4"] = cur.rowcount == 4

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG Уровень сложный -> diff=4: {cur.rowcount}")
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
        checks["diff<4 required = 33"] = cur.rowcount == 33

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 1"] = cur.rowcount == 1

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
        ordered_ids = [row[0] for row in cur.fetchall()]
        cur.execute("UPDATE tasks SET order_position=order_position+2000 WHERE course_id=%s", (COURSE_ID,))
        for pos, task_id in enumerate(ordered_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (pos, task_id))

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
        checks["final blocks"] = after_rows == [
            (2, "required", 28, 1, 28),
            (3, "required", 5, 29, 33),
            (4, "recommended", 1, 34, 34),
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
        checks["materials recommended = 2"] = sum(1 for r in materials if r[3] and r[2] == "recommended") == 2
        checks["materials duplicates inactive"] = all(
            any(r[0] == mid and not r[3] for r in materials)
            for mid in (376, 377, 548)
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
