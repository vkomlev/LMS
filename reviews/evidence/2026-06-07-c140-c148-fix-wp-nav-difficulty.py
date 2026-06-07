# -*- coding: utf-8 -*-
"""Курсы 140 и 148 — патч difficulty wp_nav заданий из раздела «Простые» навигатора.

Проблема: blanket-правило wp_nav→Сложная назначило diff=4 заданиям из «Простые».

Курс 140 (Задание 1):
  kompege:25 (wp_nav:1:f0871882) → diff=2 (Легко)

Курс 148 (Задание 2):
  kompege:70 (wp_nav:2:293e8d08) → diff=2 (Легко)
  kompege:73 (wp_nav:2:00b4a5b9) → diff=2 (Легко)

После патча — повторное переупорядочивание (Легко-задания переезжают в свой блок).

Ожидаемые итоги:
  Курс 140: active=87; было Легко(11-34), становится Легко(11-35) +1
  Курс 148: active=69; было Легко(7-38), становится Легко(7-40) +2

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DIFF_EASY = 2
DIFF_MEDIUM = 3
DIFF_HARD = 4
REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'

# (course_id, source_task_id_str)
FIXES = [
    (140, '25'),
    (148, '70'),
    (148, '73'),
]

EXPECTED_TOTALS = {140: 87, 148: 69}


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
    print(f"\n── {title} {'─' * (55 - len(title))}")


def process_course(cur, course_id: int, fixes: list[str], apply: bool, checks: dict) -> None:
    section(f"Курс {course_id}")

    # Снимок ДО
    cur.execute("""
        SELECT t.difficulty_id, d.name_ru, count(*) FILTER (WHERE t.is_active) AS active
        FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
        WHERE t.course_id=%s GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
    """, (course_id,))
    print("  ДО:")
    for r in cur.fetchall():
        print(f"    diff={r[0]}({r[1]}): active={r[2]}")

    # Патч
    fixed = 0
    for stid in fixes:
        cur.execute("""
            UPDATE tasks SET difficulty_id=%s, requirement_level=%s
            WHERE course_id=%s
              AND external_uid ILIKE 'wp_nav:%%'
              AND task_content->>'source_task_id' = %s
        """, (DIFF_EASY, REQ_REQUIRED, course_id, stid))
        n = cur.rowcount
        fixed += n
        print(f"  kompege:{stid} → diff=2 (Легко): {n} строк")

    checks[f"курс {course_id}: исправлено {len(fixes)} заданий"] = fixed == len(fixes)

    # Переупорядочивание
    cur.execute("""
        SELECT id FROM tasks
        WHERE course_id=%s AND order_position>10 AND is_active=true
        ORDER BY difficulty_id ASC, order_position ASC
    """, (course_id,))
    ordered_ids = [r[0] for r in cur.fetchall()]

    cur.execute("""
        UPDATE tasks SET order_position = order_position + 2000
        WHERE course_id=%s AND order_position>10
    """, (course_id,))
    for new_pos, task_id in enumerate(ordered_ids, start=11):
        cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

    # Снимок ПОСЛЕ
    cur.execute("""
        SELECT t.difficulty_id, d.name_ru, t.requirement_level,
               count(*) FILTER (WHERE t.is_active) AS active,
               min(t.order_position) FILTER (WHERE t.is_active),
               max(t.order_position) FILTER (WHERE t.is_active)
        FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
        WHERE t.course_id=%s
        GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
        ORDER BY t.difficulty_id, t.requirement_level
    """, (course_id,))
    print("  ПОСЛЕ:")
    for r in cur.fetchall():
        print(f"    diff={r[0]}({r[1]}) req={r[2]}: active={r[3]}, pos {r[4]}-{r[5]}")

    cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (course_id,))
    total = cur.fetchone()[0]
    expected = EXPECTED_TOTALS[course_id]
    checks[f"курс {course_id}: active={expected}"] = total == expected

    cur.execute("""
        SELECT order_position, count(*) FROM tasks
        WHERE course_id=%s AND is_active=true
        GROUP BY order_position HAVING count(*)>1
    """, (course_id,))
    checks[f"курс {course_id}: нет дублей order_position"] = len(cur.fetchall()) == 0


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        # Группируем по курсу
        from collections import defaultdict
        by_course: dict[int, list[str]] = defaultdict(list)
        for cid, stid in FIXES:
            by_course[cid].append(stid)

        for cid in sorted(by_course):
            process_course(cur, cid, by_course[cid], apply, checks)

        section("Проверки")
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
