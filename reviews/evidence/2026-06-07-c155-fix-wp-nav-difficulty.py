# -*- coding: utf-8 -*-
"""Курс 155 — патч difficulty wp_nav заданий по разделам навигатора.

Источник правды: фрагмент навигатора, предоставленный оператором 2026-06-07.

Проблема: blanket-правило wp_nav→Сложная назначило неверную сложность заданиям
из разделов «Простые» и «Средние» навигатора.

Исправления:
  Простые (kompege:3, 48, 109, 112, 114) → diff=2 (Легко), req=required
  Средние (sdamgia:11341)                → diff=3 (Средняя), req=required
  Средние (sdamgia:75241, 76701, 76219)  → diff=3 (Средняя), req=required
    NB: эти 3 задания дублируются в навигаторе (Средние И Сложные);
        ТГ-разборов нет; назначаем Средняя как более консервативное значение.

Итог после патча:
  Легко:   10 вводных + 27 non-vvod = pos 1-32 (was 22)
  Средняя: 4 non-vvod sdamgia       = pos 33-36
  Сложная: 18 non-vvod              = pos 37-56 (was 24)
  Итого активных: 56 (без изменений).

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 155
DIFF_EASY   = 2
DIFF_MEDIUM = 3
DIFF_HARD   = 4
REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'

# source_task_id (строка) → (новый diff_id, причина)
FIX_MAP = {
    # Простые — должны быть Легко
    '3':   (DIFF_EASY,   'навигатор: Простой уровень'),
    '48':  (DIFF_EASY,   'навигатор: Простой уровень'),
    '109': (DIFF_EASY,   'навигатор: Простой уровень'),
    '112': (DIFF_EASY,   'навигатор: Простой уровень'),
    '114': (DIFF_EASY,   'навигатор: Простой уровень'),
    # Средние — должны быть Средняя
    '11341': (DIFF_MEDIUM, 'навигатор: Средний уровень (только)'),
    '75241': (DIFF_MEDIUM, 'навигатор: Средний И Сложный (дубль); ТГ нет → Средняя'),
    '76701': (DIFF_MEDIUM, 'навигатор: Средний И Сложный (дубль); ТГ нет → Средняя'),
    '76219': (DIFF_MEDIUM, 'навигатор: Средний И Сложный (дубль); ТГ нет → Средняя'),
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
    print(f"\n── {title} {'─' * (55 - len(title))}")


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        section("Снимок ДО")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru,
                   count(*) FILTER (WHERE t.is_active) AS active
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}): active={r[2]}")

        # ── ШАГ 1: Патч difficulty wp_nav по source_task_id ──────────────────
        section("ШАГ 1: Патч wp_nav difficulty")
        fixed_easy = fixed_medium = 0
        for stid, (diff_id, reason) in FIX_MAP.items():
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE course_id=%s
                  AND external_uid ILIKE 'wp_nav:%%'
                  AND task_content->>'source_task_id' = %s
            """, (diff_id, COURSE_ID, stid))
            n = cur.rowcount
            if diff_id == DIFF_EASY:
                fixed_easy += n
            else:
                fixed_medium += n
            print(f"  source_task_id={stid:<6} → diff={diff_id}: {n} строк  ({reason})")

        checks["Легко: исправлено 5 kompege"] = fixed_easy == 5
        checks["Средняя: исправлена 4 sdamgia"] = fixed_medium == 4

        # ── ШАГ 2: requirement_level заданий ─────────────────────────────────
        section("ШАГ 2: requirement_level")
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%'
        """, (REQ_REQUIRED, COURSE_ID))
        print(f"  вводные → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id IN (%s,%s)
              AND external_uid NOT ILIKE 'lms:tsk109:%%' AND is_active=true
        """, (REQ_REQUIRED, COURSE_ID, DIFF_EASY, DIFF_MEDIUM))
        print(f"  Легко+Средняя (non-vvod) → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s AND is_active=true
        """, (REQ_RECOMMENDED, COURSE_ID, DIFF_HARD))
        hard_cnt = cur.rowcount
        print(f"  Сложная → recommended: {hard_cnt}")
        checks["Сложных active = 15"] = hard_cnt == 15

        # ── ШАГ 3: Переупорядочивание ────────────────────────────────────────
        section("ШАГ 3: Переупорядочивание")
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

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) FILTER (WHERE t.is_active) AS active,
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
        print(f"  Итого активных: {total}")

        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        checks["нет дублей order_position"] = len(cur.fetchall()) == 0
        checks["итого активных = 56"] = total == 56

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
