# -*- coding: utf-8 -*-
"""Курс 159: довести requirement_level материалов по скрину навигатора.

Источник: скрин навигатора задания 8 от оператора, 2026-06-07.

Правила по скрину:
- ☝ Теоретический блок -> required
- ☝ Разбор типовых заданий -> required
- Что повторить по Python без ☝/🔽 -> recommended
- ☝ Решение заданий 8 -> required
- 5 способов создания списков без ☝/🔽 -> recommended
- ☝ Решение заданий 8 с помощью itertools -> required
- duplicate video id=656 с тем же URL, что id=655 -> is_active=false

Запуск:
  python reviews/evidence/2026-06-07-c159-materials-icons.py
  python reviews/evidence/2026-06-07-c159-materials-icons.py --apply
"""
from __future__ import annotations

import io
import os
import re
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 159

EXPECTED = {
    439: ("Теория и основные понятия", "required", True),
    440: ("Что повторить", "recommended", True),
    441: ("Разбор типовых заданий", "required", True),
    653: ("Решение заданий 8", "required", True),
    654: ("5 способов создания списков", "recommended", True),
    655: ("Решение заданий 8 с помощью модуля itertools Python", "required", True),
    656: ("Решение заданий 8 с помощью модуля itertools Python", "required", False),
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
        section("Снимок ДО")
        cur.execute(
            """
            SELECT id, title, type::text, order_position, is_active, requirement_level,
                   content #>> '{sources,0,url}' AS source_url
            FROM materials
            WHERE course_id=%s
            ORDER BY order_position, id
            """,
            (COURSE_ID,),
        )
        before_rows = cur.fetchall()
        for row in before_rows:
            print(
                f"id={row[0]:<4} pos={row[3]:<2} active={row[4]!s:<5} "
                f"req={row[5]:<11} type={row[2]:<5} title={row[1]}"
            )

        section("Аудит целевого набора")
        ids = tuple(EXPECTED)
        cur.execute(
            """
            SELECT id, title, is_active, requirement_level
            FROM materials
            WHERE course_id=%s AND id = ANY(%s)
            ORDER BY id
            """,
            (COURSE_ID, list(ids)),
        )
        current = {row[0]: row for row in cur.fetchall()}
        checks["найдены все 7 целевых материалов"] = set(current) == set(EXPECTED)
        for mid, (title, _req, _active) in EXPECTED.items():
            row = current.get(mid)
            ok_title = row is not None and row[1] == title
            checks[f"id={mid} title matches"] = ok_title

        section("Применение")
        for mid, (_title, req, active) in EXPECTED.items():
            cur.execute(
                """
                UPDATE materials
                SET requirement_level=%s, is_active=%s
                WHERE course_id=%s AND id=%s
                """,
                (req, active, COURSE_ID, mid),
            )
            print(f"id={mid} -> req={req}, active={active}: {cur.rowcount} строк")
            checks[f"id={mid} updated one row"] = cur.rowcount == 1

        section("Проверки ПОСЛЕ")
        cur.execute(
            """
            SELECT id, title, is_active, requirement_level
            FROM materials
            WHERE course_id=%s AND id = ANY(%s)
            ORDER BY id
            """,
            (COURSE_ID, list(ids)),
        )
        after = {row[0]: row for row in cur.fetchall()}
        for mid, (_title, req, active) in EXPECTED.items():
            row = after.get(mid)
            checks[f"id={mid} req/active expected"] = (
                row is not None and row[2] is active and row[3] == req
            )

        cur.execute(
            """
            SELECT content #>> '{sources,0,url}', count(*)
            FROM materials
            WHERE course_id=%s AND is_active=true AND type::text='video'
            GROUP BY content #>> '{sources,0,url}'
            HAVING count(*) > 1
            """,
            (COURSE_ID,),
        )
        video_dups = cur.fetchall()
        checks["нет активных дублей video URL"] = len(video_dups) == 0
        if video_dups:
            print("Дубли video URL:", video_dups)

        cur.execute(
            """
            SELECT requirement_level, count(*)
            FROM materials
            WHERE course_id=%s AND is_active=true
            GROUP BY requirement_level
            ORDER BY requirement_level
            """,
            (COURSE_ID,),
        )
        levels = cur.fetchall()
        print("Активные материалы по requirement_level:", levels)
        checks["есть required и recommended"] = {row[0] for row in levels} == {
            "recommended",
            "required",
        }

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
