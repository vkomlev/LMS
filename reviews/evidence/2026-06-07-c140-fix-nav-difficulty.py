# -*- coding: utf-8 -*-
"""Курс 140 — патч difficulty по навигатору.

Проблема: 4 задачи из раздела «Простые» навигатора
(kompege:26, 27, 28, 32) импортированы как difficulty=3 (Средняя).
Должны быть difficulty=2 (Легко).

Источник: https://victor-komlev.ru/zadanie-1-ege-po-informatike-informatsionnye-modeli/#prostoy-uroven
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 140
DIFF_EASY = 2
REQ_REQUIRED = 'required'

NAV_EASY = [
    'ext:d4:kompege:20260602:26',
    'ext:d4:kompege:20260602:27',
    'ext:d4:kompege:20260602:28',
    'ext:d4:kompege:20260602:32',
]


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


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        print("── Простые → difficulty=2 (Легко) ──")
        fixed = 0
        for uid in NAV_EASY:
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s, requirement_level=%s
                WHERE external_uid=%s AND course_id=%s
            """, (DIFF_EASY, REQ_REQUIRED, uid, COURSE_ID))
            fixed += cur.rowcount
            print(f"  {uid}: {cur.rowcount} строк")

        checks["исправлено 4 задачи → Легко"] = fixed == 4

        # Переупорядочивание: добавляем 4 Легко в блок 11-34
        print("\n── Переупорядочивание ──")
        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND order_position>10 AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC
        """, (COURSE_ID,))
        ordered_ids = [r[0] for r in cur.fetchall()]
        print(f"  заданий (pos>10, active): {len(ordered_ids)}")

        cur.execute("""
            UPDATE tasks SET order_position = order_position + 2000
            WHERE course_id=%s AND order_position>10
        """, (COURSE_ID,))
        for new_pos, task_id in enumerate(ordered_ids, start=11):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s",
                        (new_pos, task_id))

        # Снимок ПОСЛЕ
        print("\n── Снимок ПОСЛЕ ──")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) FILTER (WHERE t.is_active) as active,
                   min(t.order_position) FILTER (WHERE t.is_active),
                   max(t.order_position) FILTER (WHERE t.is_active)
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
            ORDER BY t.difficulty_id, t.requirement_level
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}) req={r[2]}: active={r[3]}, pos {r[4]}-{r[5]}")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        total = cur.fetchone()[0]
        checks["итого активных = 87"] = total == 87

        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        checks["нет дублей order_position"] = len(cur.fetchall()) == 0

        print("\n── Проверки ──")
        all_ok = True
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")
            if not ok:
                all_ok = False

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
