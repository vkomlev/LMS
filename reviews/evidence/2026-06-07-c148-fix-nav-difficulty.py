# -*- coding: utf-8 -*-
"""Курс 148 — патч difficulty по навигатору (второй проход).

Проблема: при импорте все задачи получили difficulty=3 (Средняя) по умолчанию.
Навигатор https://victor-komlev.ru/navigator-po-zadaniyu-2-ege/ разделяет:
  Простые  → difficulty=2 (Легко)
  Средние  → difficulty=3 (Средняя) — уже верно
  Сложные  → difficulty=4 (Сложная)

Исправления:
  3 задачи  → Легко    (kompege:1, kompege:72, sdamgia:38936)
  24 задачи → Сложная  (polyakov:7239 + 23 wp_nav:2:*)

Итог после патча:
  pos 11-38: Легко    (28 шт.: 20 Крылов + 5 ТГ + 3 из навигатора)
  pos 39-44: Средняя  (6 шт.:  4 sdamgia + 2 Крылов)
  pos 45-69: Сложная  (25 шт.: 24 из навигатора + 1 ТГ)

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 148
DIFF_EASY = 2
DIFF_HARD = 4
REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'

# Простые из навигатора → Легко
NAV_EASY = [
    'ext:d4:kompege:20260602:1',
    'ext:d4:kompege:20260602:72',
    'ext:d4:sdamgia:20260602:38936',
]

# Сложные из навигатора → Сложная
NAV_HARD_EXPLICIT = [
    'ext:d4:polyakov:20260602:7239',
]
# + все wp_nav:2:* (импортированы со страниц сайта с Сложными)
NAV_HARD_WPNAV_PREFIX = 'wp_nav:2:'


def load_dsn() -> str:
    dsn = os.environ.get("LMS_DB_DSN")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    raise RuntimeError("DATABASE_URL не найден в .env")


def section(title: str) -> None:
    print(f"\n── {title} {'─' * (55 - len(title))}")


def main() -> None:
    apply = "--apply" in sys.argv

    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        # ── Снимок ДО ────────────────────────────────────────────────────────
        section("Снимок ДО")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru,
                   count(*) FILTER (WHERE t.is_active) as active,
                   min(t.order_position) FILTER (WHERE t.is_active),
                   max(t.order_position) FILTER (WHERE t.is_active)
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}): active={r[2]}, pos {r[3]}-{r[4]}")

        # ── ШАГ 1: Простые из навигатора → Легко ─────────────────────────────
        section("ШАГ 1: Простые → difficulty=2 (Легко)")
        easy_fixed = 0
        for uid in NAV_EASY:
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s, requirement_level=%s
                WHERE external_uid=%s AND course_id=%s
            """, (DIFF_EASY, REQ_REQUIRED, uid, COURSE_ID))
            easy_fixed += cur.rowcount
            print(f"  {uid}: {cur.rowcount} строк")
        checks["Легко: исправлено 3 задачи из навигатора"] = easy_fixed == 3

        # ── ШАГ 2: Сложные из навигатора → Сложная ───────────────────────────
        section("ШАГ 2: Сложные → difficulty=4 (Сложная)")
        hard_fixed = 0

        # явные (polyakov)
        for uid in NAV_HARD_EXPLICIT:
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s, requirement_level=%s
                WHERE external_uid=%s AND course_id=%s
            """, (DIFF_HARD, REQ_RECOMMENDED, uid, COURSE_ID))
            hard_fixed += cur.rowcount
            print(f"  {uid}: {cur.rowcount} строк")

        # все wp_nav:2:*
        cur.execute("""
            UPDATE tasks SET difficulty_id=%s, requirement_level=%s
            WHERE course_id=%s AND external_uid LIKE %s AND is_active=true
        """, (DIFF_HARD, REQ_RECOMMENDED, COURSE_ID, NAV_HARD_WPNAV_PREFIX + '%'))
        wp_fixed = cur.rowcount
        hard_fixed += wp_fixed
        print(f"  wp_nav:2:* ({wp_fixed} шт.): обновлено")

        checks["Сложных: исправлено 24 задачи (1 polyakov + 23 wp_nav)"] = hard_fixed == 24

        # ── ШАГ 3: Переупорядочивание pos 11+ ────────────────────────────────
        section("ШАГ 3: Переупорядочивание")
        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND order_position>10 AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC
        """, (COURSE_ID,))
        ordered_ids = [r[0] for r in cur.fetchall()]
        total = len(ordered_ids)
        print(f"  заданий (is_active=true, pos>10): {total}")

        cur.execute("""
            UPDATE tasks SET order_position = order_position + 2000
            WHERE course_id=%s AND order_position>10
        """, (COURSE_ID,))
        for new_pos, task_id in enumerate(ordered_ids, start=11):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s",
                        (new_pos, task_id))
        print(f"  назначено новых позиций: {total}")

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ")
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

        cur.execute("""
            SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true
        """, (COURSE_ID,))
        total_active = cur.fetchone()[0]
        print(f"  Итого активных: {total_active}")

        # проверка дублей
        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        dups = cur.fetchall()
        checks["нет дублей order_position"] = len(dups) == 0
        checks["итого активных = 69"] = total_active == 69

        # Распределение сложных для ручной проверки
        section("Сложные (выборка для проверки)")
        cur.execute("""
            SELECT external_uid, order_position
            FROM tasks
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
            ORDER BY order_position
        """, (COURSE_ID,))
        hard_rows = cur.fetchall()
        for r in hard_rows:
            print(f"  pos={r[1]}  {r[0]}")
        checks["Сложных активных = 25"] = len(hard_rows) == 25

        # ── Итог ─────────────────────────────────────────────────────────────
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
