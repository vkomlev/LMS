# -*- coding: utf-8 -*-
"""Курс 160 (Задание 9, электронные таблицы) — нормализация.

Источник классификации:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-9-ege/
- явные TG-маркеры в stem
- правило чек-листа: Крылов PDF без явного среднего маркера -> Легко

Особенности:
- вводных lms:c160:vvod:* нет;
- в навигаторе: Простые=4, Средние=29, Сложные=0;
- материалы: 9 позиций навигатора, id=662 без ☝ -> recommended;
- id=446 "Задания для закрепления" — раздел/контейнер, не материал навигатора -> inactive;
- id=664 "Задание 9" — лишнее видео не из навигатора, URL от курса 8 -> inactive;
- crylov:v11t9 содержит "Уровень средний" -> оставить diff=3.

Запуск:
  python reviews/evidence/2026-06-07-c160-normalization.py
  python reviews/evidence/2026-06-07-c160-normalization.py --apply
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 160

WP_NAV_EASY_STIDS = {"1962", "2041"}
KOMPEGE_EASY_UIDS = (
    "ext:d4:kompege:20260602:2049",
    "ext:d4:kompege:20260602:2100",
)
TG_EASY_UIDS = ("tg:ege:785", "tg:ege:553", "tg:ege:498")
MATERIAL_RECOMMENDED_IDS = (662,)
MATERIAL_DEACTIVATE_IDS = (446, 664)


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
        cur.execute(
            """
            UPDATE materials
            SET requirement_level='recommended'
            WHERE course_id=%s AND id=ANY(%s)
            """,
            (COURSE_ID, list(MATERIAL_RECOMMENDED_IDS)),
        )
        print(f"id={MATERIAL_RECOMMENDED_IDS} -> recommended: {cur.rowcount}")
        checks["recommended material updated"] = cur.rowcount == len(MATERIAL_RECOMMENDED_IDS)

        cur.execute(
            """
            UPDATE materials
            SET is_active=false
            WHERE course_id=%s AND id=ANY(%s)
            """,
            (COURSE_ID, list(MATERIAL_DEACTIVATE_IDS)),
        )
        print(f"id={MATERIAL_DEACTIVATE_IDS} -> inactive: {cur.rowcount}")
        checks["extra materials deactivated"] = cur.rowcount == len(MATERIAL_DEACTIVATE_IDS)

        section("Difficulty")
        for stid in sorted(WP_NAV_EASY_STIDS, key=int):
            cur.execute(
                """
                UPDATE tasks SET difficulty_id=2
                WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
                  AND task_content->>'source_kind' = 'kompege'
                  AND task_content->>'source_task_id' = %s
                """,
                (COURSE_ID, stid),
            )
            print(f"wp_nav kompege:{stid} -> diff=2: {cur.rowcount}")
            checks[f"wp_nav kompege:{stid} updated"] = cur.rowcount == 1

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true
            """,
            (COURSE_ID, list(KOMPEGE_EASY_UIDS)),
        )
        print(f"kompege direct Простые -> diff=2: {cur.rowcount}")
        checks["kompege direct easy updated"] = cur.rowcount == len(KOMPEGE_EASY_UIDS)

        cur.execute(
            """
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true
            """,
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG Легкие -> diff=2: {cur.rowcount}")
        checks["tg easy updated"] = cur.rowcount == len(TG_EASY_UIDS)

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
        print(f"Крылов PDF new+ext -> diff=2: {cur.rowcount}")
        checks["crylov pdf updated"] = cur.rowcount == 20

        section("Requirement levels")
        cur.execute(
            "UPDATE tasks SET requirement_level='required' WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        print(f"active tasks -> required: {cur.rowcount}")
        checks["active tasks required"] = cur.rowcount == before_total

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
        for pos, task_id in enumerate(ordered_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (pos, task_id))
        print(f"Активные задачи -> позиции 1-{len(ordered_ids)}")
        checks["active task count unchanged"] = len(ordered_ids) == before_total

        section("Деактивированные материалы в конец")
        cur.execute(
            """
            SELECT id
            FROM materials
            WHERE course_id=%s AND is_active=false
            ORDER BY order_position, id
            """,
            (COURSE_ID,),
        )
        inactive_material_ids = [row[0] for row in cur.fetchall()]
        cur.execute(
            "SELECT coalesce(max(order_position), 0) FROM materials WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        start_pos = cur.fetchone()[0] + 1
        for offset, material_id in enumerate(inactive_material_ids):
            cur.execute(
                "UPDATE materials SET order_position=%s WHERE id=%s",
                (start_pos + offset, material_id),
            )
        print(f"inactive materials -> с позиции {start_pos}: {inactive_material_ids}")

        section("Проверки")
        cur.execute(
            """
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND is_active=true AND difficulty_id=2 AND requirement_level='required'
            """,
            (COURSE_ID,),
        )
        checks["diff=2 required count = 27"] = cur.fetchone()[0] == 27

        cur.execute(
            """
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND is_active=true AND difficulty_id=3 AND requirement_level='required'
            """,
            (COURSE_ID,),
        )
        checks["diff=3 required count = 77"] = cur.fetchone()[0] == 77

        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true AND difficulty_id=4",
            (COURSE_ID,),
        )
        checks["diff=4 active count = 0"] = cur.fetchone()[0] == 0

        cur.execute(
            """
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND external_uid='crylov:v11t9'
              AND difficulty_id=3 AND requirement_level='required'
            """,
            (COURSE_ID,),
        )
        checks["crylov:v11t9 remains medium"] = cur.fetchone()[0] == 1

        cur.execute(
            """
            SELECT order_position, count(*)
            FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*) > 1
            """,
            (COURSE_ID,),
        )
        checks["нет дублей order_position"] = len(cur.fetchall()) == 0

        cur.execute(
            """
            SELECT difficulty_id, min(order_position), max(order_position)
            FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY difficulty_id ORDER BY difficulty_id
            """,
            (COURSE_ID,),
        )
        blocks = cur.fetchall()
        print("Блоки difficulty:", blocks)
        checks["блоки diff не пересекаются"] = all(
            blocks[i][2] < blocks[i + 1][1] for i in range(len(blocks) - 1)
        )

        cur.execute(
            """
            SELECT requirement_level, count(*)
            FROM materials
            WHERE course_id=%s AND is_active=true
            GROUP BY requirement_level ORDER BY requirement_level
            """,
            (COURSE_ID,),
        )
        material_levels = cur.fetchall()
        print("Материалы active по req:", material_levels)
        checks["active materials required/recommended = 8/1"] = material_levels == [
            ("recommended", 1),
            ("required", 8),
        ]

        cur.execute(
            """
            SELECT id, is_active
            FROM materials
            WHERE course_id=%s AND id=ANY(%s)
            ORDER BY id
            """,
            (COURSE_ID, list(MATERIAL_DEACTIVATE_IDS)),
        )
        checks["extra materials inactive"] = all(row[1] is False for row in cur.fetchall())

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
            print("\nРЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
            sys.exit(1)
    except Exception as exc:
        conn.rollback()
        print(f"\nОШИБКА: {exc!r}. ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
