# -*- coding: utf-8 -*-
"""Курс 139 (Задание 13, сети и адресация) — нормализация.

Источники:
- nav_parser на https://victor-komlev.ru/navigator-po-zadaniyu-13-ege/
- явные TG-маркеры в stem
- правило чек-листа: Крылов PDF/ext -> Легко

Особенности:
- уровни берутся из текстовых подзаголовков "Уровень простой/Средний/Сложный";
- existing ext:d4/ext:calib в LMS находятся в среднем разделе или дублируются
  в среднем и сложном, поэтому остаются diff=3; hard-only задачи отсутствуют
  в LMS и фиксируются в реестре missing;
- вводные lms:c139:vvod:01-27 уже созданы и покрывают вопросы/мини-практику;
- tg:ege:338 без маркера уровня в stem: оставляем текущий diff=3, не угадываем;
- material id=564 — лишний дубль видео id=549;
- material "Мини-задания" из навигатора не создаём: покрыто lms:c139:vvod:19-27.
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 139

TG_EASY_UIDS = ("tg:ege:917", "tg:ege:890", "tg:ege:566", "tg:ege:555")
TG_MEDIUM_UIDS = (
    "tg:ege:861", "tg:ege:860", "tg:ege:854", "tg:ege:633",
    "tg:ege:605", "tg:ege:601", "tg:ege:599", "tg:ege:462", "tg:ege:338",
)
TG_HARD_UIDS = ("tg:ege:600", "tg:ege:596")

CRYLOV_DIRECT_EASY_UIDS = ("crylov:v5t13",)
CRYLOV_DIRECT_MEDIUM_UIDS = ("crylov:v11t13", "crylov:v16t13")

MATERIAL_PLAN = {
    353: ("URL адрес", "skippable", True, 0),
    354: ("IP адрес", "required", True, 1),
    355: ("Подсеть, адрес подсети и маска подсети", "required", True, 2),
    549: ("Решения заданий 13 (IP адреса)", "recommended", True, 3),
    550: ("Задания 13. Восстановление URL адресов", "skippable", True, 4),
    551: ("Задания 13. Понятие URL адреса. Решение заданий на определение URL", "skippable", True, 5),
    552: ("Что такое IP адрес. Как он может быть представлен.", "recommended", True, 6),
    553: ("Термины и теория задания 13. IP адрес", "required", True, 7),
    554: ("Задания 13. Понятие IP адреса. Представление IP адреса. Решение заданий на восстановление IP адреса", "required", True, 8),
    555: ("Термины и теория задания 13. Сеть и ее свойства", "required", True, 9),
    556: ("Термины и теория задания 13. Адрес сети и как его вычислить. Побитовая конъюнкция", "required", True, 10),
    557: ("Термины и теория задания 13. Ответы на вопросы теории и основные термины. Бродкаст, сетевой адрес, преффикс.", "required", True, 11),
    558: ("Теория сетей и адресации для заданий 13", "required", True, 12),
    559: ("Компьютерная сеть", "recommended", True, 13),
    560: ("Задание 13. Понятие сети, маски, адреса сети. Решение заданий на определение адреса сети (без программы)", "required", True, 14),
    561: ("Решение заданий 13 (IP адреса) на определение порядкового номера устройства и определение адреса сети с помощью модуля ipaddress", "required", True, 15),
    562: ("Использование IPv4Network для работы с сетями в задании 13.", "required", True, 16),
    563: ("Задания на нахождение маски сети", "required", True, 17),
    356: ("Вопросы", "required", False, 18),
    357: ("Решаем на бумаге", "recommended", False, 19),
    564: ("Решения заданий 13 (IP адреса)", "required", False, 20),
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
        checks["active total stable = 98"] = before_total == 98

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
        print(f"ext:d4/ext:calib Средние или дубли Средние/Сложные -> diff=3: {cur.rowcount}")
        checks["ext medium = 33"] = cur.rowcount == 33

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
        print(f"Крылов direct Уровень простой -> diff=2: {cur.rowcount}")
        checks["crylov direct easy = 1"] = cur.rowcount == 1

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(CRYLOV_DIRECT_MEDIUM_UIDS)),
        )
        print(f"Крылов direct Уровень средний -> diff=3: {cur.rowcount}")
        checks["crylov direct medium = 2"] = cur.rowcount == 2

        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"TG Уровень простой/легкий -> diff=2: {cur.rowcount}")
        checks["tg easy = 4"] = cur.rowcount == 4

        cur.execute(
            "UPDATE tasks SET difficulty_id=3 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_MEDIUM_UIDS)),
        )
        print(f"TG Уровень/Сложность средняя + tg:338 текущий средний -> diff=3: {cur.rowcount}")
        checks["tg medium = 9"] = cur.rowcount == 9

        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_HARD_UIDS)),
        )
        print(f"TG Уровень сложный -> diff=4: {cur.rowcount}")
        checks["tg hard = 2"] = cur.rowcount == 2

        section("Requirement levels")
        cur.execute(
            """
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND is_active=true AND difficulty_id < 4
            """,
            (COURSE_ID,),
        )
        print(f"diff<4 active tasks -> required: {cur.rowcount}")
        checks["diff<4 required = 96"] = cur.rowcount == 96

        cur.execute(
            """
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND is_active=true AND difficulty_id = 4
            """,
            (COURSE_ID,),
        )
        print(f"diff=4 active tasks -> recommended: {cur.rowcount}")
        checks["diff=4 recommended = 2"] = cur.rowcount == 2

        section("Переупорядочивание")
        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c139:vvod:%%'
            ORDER BY external_uid ASC
            """,
            (COURSE_ID,),
        )
        vvod_ids = [row[0] for row in cur.fetchall()]
        checks["vvod count = 27"] = len(vvod_ids) == 27

        cur.execute(
            """
            SELECT id
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c139:vvod:%%'
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
            (1, "required", 5, 1, 6),
            (2, "required", 43, 4, 52),
            (3, "required", 48, 11, 96),
            (4, "recommended", 2, 97, 98),
        ]

        cur.execute(
            """
            SELECT min(order_position), max(order_position), count(*)
            FROM tasks
            WHERE course_id=%s AND is_active=true AND external_uid ILIKE 'lms:c139:vvod:%%'
            """,
            (COURSE_ID,),
        )
        checks["vvod positions 1-27"] = cur.fetchone() == (1, 27, 27)

        cur.execute(
            """
            SELECT difficulty_id, requirement_level, count(*), min(order_position), max(order_position)
            FROM tasks
            WHERE course_id=%s
              AND is_active=true
              AND external_uid NOT ILIKE 'lms:c139:vvod:%%'
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
            (2, "required", 25, 28, 52),
            (3, "required", 44, 53, 96),
            (4, "recommended", 2, 97, 98),
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
            sum(1 for r in materials if r[3]) == 18 and
            sum(1 for r in materials if not r[3]) == 3
        )
        checks["materials mixed req"] = (
            sum(1 for r in materials if r[3] and r[2] == "required") == 12 and
            sum(1 for r in materials if r[3] and r[2] == "recommended") == 3 and
            sum(1 for r in materials if r[3] and r[2] == "skippable") == 3
        )
        checks["practice materials inactive"] = all(
            any(r[0] == mid and not r[3] for r in materials)
            for mid in (356, 357, 564)
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
