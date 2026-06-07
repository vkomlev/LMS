# -*- coding: utf-8 -*-
"""Курс 155: погасить материалы-практики, покрытые вводными заданиями.

Контекст:
- в навигаторе задания 4 есть материалы "Вопросы по разделу 1-5",
  "Мини практика 6-10", "Мини практика после заданий";
- их содержание пересекается с вводными заданиями lms:tsk109:c155:01-10;
- по правилу tsk-112 такие практики не восстанавливаем как материалы,
  если они уже преобразованы в задания/контрольные вопросы.
"""
import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 155
MATERIAL_IDS = (418, 419, 807)


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


def show(cur, title: str) -> None:
    print(f"\n{title}")
    cur.execute(
        """
        SELECT id, order_position, is_active, requirement_level, external_uid, title
        FROM materials
        WHERE course_id=%s AND id = ANY(%s)
        ORDER BY id
        """,
        (COURSE_ID, list(MATERIAL_IDS)),
    )
    for row in cur.fetchall():
        print(" ", row)


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")

        show(cur, "Снимок ДО")

        cur.execute(
            """
            SELECT count(*)
            FROM tasks
            WHERE course_id=%s
              AND external_uid ILIKE 'lms:tsk109:c155:%%'
              AND is_active=true
            """,
            (COURSE_ID,),
        )
        intro_count = cur.fetchone()[0]
        print(f"\nВводные задания lms:tsk109:c155:* active: {intro_count}")
        checks["вводные задания c155 = 10"] = intro_count == 10

        cur.execute(
            """
            UPDATE materials
            SET is_active=false,
                order_position=order_position + 100
            WHERE course_id=%s AND id = ANY(%s) AND is_active=true
            """,
            (COURSE_ID, list(MATERIAL_IDS)),
        )
        print(f"Погашено материалов: {cur.rowcount}")
        checks["погашено 3 материала"] = cur.rowcount == 3

        cur.execute(
            """
            SELECT id FROM materials
            WHERE course_id=%s AND is_active=true
            ORDER BY order_position, id
            """,
            (COURSE_ID,),
        )
        active_ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            "UPDATE materials SET order_position=order_position+1000 WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        for pos, mat_id in enumerate(active_ids):
            cur.execute("UPDATE materials SET order_position=%s WHERE id=%s", (pos, mat_id))

        cur.execute(
            """
            SELECT order_position, count(*)
            FROM materials
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*) > 1
            """,
            (COURSE_ID,),
        )
        checks["нет дублей active order_position"] = len(cur.fetchall()) == 0

        show(cur, "Снимок ПОСЛЕ")

        print("\nПроверки")
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
