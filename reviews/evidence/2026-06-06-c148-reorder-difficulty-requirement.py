# -*- coding: utf-8 -*-
"""Курс 148 «Задание 2 ЕГЭ. Таблицы истинности» — полная нормализация.

Операции:
 1. Материалы: requirement_level по навигатору + деактивация дубликата id=608.
 2. ТГ-задания: difficulty по тексту stem + деактивация некорректного tg:ege:540.
 3. Переупорядочивание pos 11+:
      Легко(25) → pos 11-35, Средняя(33) → pos 36-68, Сложная(1) → pos 69.

Итого активных заданий: 69 (было 70, деактивировано некорректное).
Источник: https://victor-komlev.ru/navigator-po-zadaniyu-2-ege/
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 148

DIFF_EASY   = 2
DIFF_NORMAL = 3
DIFF_HARD   = 4

REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'
REQ_SKIPPABLE   = 'skippable'

# ── Материалы: (id, req_level, is_active или None) ───────────────────────────
MATERIAL_UPDATES = [
    (396, REQ_RECOMMENDED, None),   # Приоритет операций — без значка
    (399, REQ_SKIPPABLE,   None),   # Excel таблица — 🔽
    (400, REQ_SKIPPABLE,   None),   # Excel решение — 🔽
    (403, REQ_SKIPPABLE,   None),   # Реклама — нет в навигаторе
    (605, REQ_RECOMMENDED, None),   # Видео вариант 1 — без значка
    (606, REQ_RECOMMENDED, None),   # Видео вариант 2 — без значка
    (607, REQ_REQUIRED,    None),   # Видео вариант 3 ☝️ (уже required, для явности)
    (608, REQ_RECOMMENDED, False),  # Дубликат вариант 3 — деактивировать
]

# ── ТГ-задания: difficulty по stem ───────────────────────────────────────────
TG_DIFFICULTY = {
    'tg:ege:967': (DIFF_EASY,   'stem: «Уровень простой»'),
    'tg:ege:956': (DIFF_EASY,   'stem: «Уровень простой»'),
    'tg:ege:608': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:595': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:517': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:543': (DIFF_HARD,   'stem: «Уровень сложный»'),
}
TG_DEACTIVATE = 'tg:ege:540'  # «задание некорректно!»


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
            SELECT t.difficulty_id, d.name_ru, count(*) as cnt,
                   min(t.order_position) as min_pos, max(t.order_position) as max_pos
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}): cnt={r[2]}, pos {r[3]}-{r[4]}")

        # ── ШАГ 1: Материалы ─────────────────────────────────────────────────
        section("ШАГ 1: Обязательность материалов")
        mat_upd = mat_deact = 0
        for mid, req, is_active in MATERIAL_UPDATES:
            if is_active is False:
                cur.execute(
                    "UPDATE materials SET requirement_level=%s, is_active=false "
                    "WHERE id=%s AND course_id=%s",
                    (req, mid, COURSE_ID),
                )
                mat_deact += cur.rowcount
                print(f"  mat {mid}: is_active=false, req={req} — {cur.rowcount} строк")
            else:
                cur.execute(
                    "UPDATE materials SET requirement_level=%s WHERE id=%s AND course_id=%s",
                    (req, mid, COURSE_ID),
                )
                mat_upd += cur.rowcount
                print(f"  mat {mid}: req={req} — {cur.rowcount} строк")

        checks["materials: обновлено 7 req_level"] = mat_upd == 7
        checks["materials: деактивирован дубликат id=608"] = mat_deact == 1

        # ── ШАГ 2: ТГ-задания ────────────────────────────────────────────────
        section("ШАГ 2: Difficulty и деактивация ТГ-заданий")

        diff_fixed = 0
        for uid, (diff_id, reason) in TG_DIFFICULTY.items():
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE external_uid=%s AND course_id=%s
            """, (diff_id, uid, COURSE_ID))
            diff_fixed += cur.rowcount
            print(f"  {uid} → diff={diff_id} ({reason}): {cur.rowcount} строк")

        # Деактивировать некорректное задание
        cur.execute("""
            UPDATE tasks SET is_active=false
            WHERE external_uid=%s AND course_id=%s
        """, (TG_DEACTIVATE, COURSE_ID))
        deact = cur.rowcount
        print(f"  {TG_DEACTIVATE} → деактивировано: {deact} строк")

        checks["difficulty: исправлено у 6 ТГ-заданий"] = diff_fixed == 6
        checks["деактивировано некорректное tg:ege:540"] = deact == 1

        # ── ШАГ 3: requirement_level заданий ─────────────────────────────────
        section("ШАГ 3: requirement_level заданий")

        # Вводные — required
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%'
        """, (REQ_REQUIRED, COURSE_ID))
        print(f"  вводные tsk109 → required: {cur.rowcount}")

        # Легко (не вводные) → required (Простые ☝️)
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
              AND is_active=true
        """, (REQ_REQUIRED, COURSE_ID, DIFF_EASY))
        print(f"  Легко → required: {cur.rowcount}")

        # Средняя (не вводные) → required (Средние ☝️)
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
              AND is_active=true
        """, (REQ_REQUIRED, COURSE_ID, DIFF_NORMAL))
        print(f"  Средняя → required: {cur.rowcount}")

        # Сложная → recommended (опциональные)
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s AND is_active=true
        """, (REQ_RECOMMENDED, COURSE_ID, DIFF_HARD))
        hard_cnt = cur.rowcount
        print(f"  Сложная → recommended: {hard_cnt}")

        checks["tasks: Сложных 1"] = hard_cnt == 1

        # ── ШАГ 4: Переупорядочивание pos 11+ ────────────────────────────────
        section("ШАГ 4: Переупорядочивание")

        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND order_position>10 AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC
        """, (COURSE_ID,))
        ordered_ids = [r[0] for r in cur.fetchall()]
        total = len(ordered_ids)
        print(f"  заданий для упорядочивания (is_active=true, pos>10): {total}")

        cur.execute("""
            UPDATE tasks SET order_position = order_position + 2000
            WHERE course_id=%s AND order_position>10
        """, (COURSE_ID,))
        print(f"  сдвинуто в temp space: {cur.rowcount}")

        for new_pos, task_id in enumerate(ordered_ids, start=11):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s",
                        (new_pos, task_id))

        # Деактивированное задание — ставим в конец, чтобы не мешало
        cur.execute("""
            SELECT max(order_position) FROM tasks
            WHERE course_id=%s AND is_active=true
        """, (COURSE_ID,))
        max_active = cur.fetchone()[0] or 0
        cur.execute("""
            UPDATE tasks SET order_position=%s
            WHERE course_id=%s AND external_uid=%s
        """, (max_active + 1, COURSE_ID, TG_DEACTIVATE))
        print(f"  деактивированное задание → pos {max_active+1}")

        print(f"  назначено новых позиций: {total}")

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) FILTER (WHERE t.is_active) as active,
                   count(*) FILTER (WHERE NOT t.is_active) as inactive,
                   min(t.order_position) FILTER (WHERE t.is_active) as min_pos,
                   max(t.order_position) FILTER (WHERE t.is_active) as max_pos
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
            ORDER BY t.difficulty_id, t.requirement_level
        """, (COURSE_ID,))
        rows = cur.fetchall()
        for r in rows:
            print(f"  diff={r[0]}({r[1]}) req={r[2]}: active={r[3]}, inactive={r[4]}, pos {r[5]}-{r[6]}")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        total_active = cur.fetchone()[0]
        print(f"  Итого активных заданий: {total_active}")

        # Проверка дублей pos среди активных
        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        dups = cur.fetchall()
        checks["нет дублей order_position"] = len(dups) == 0
        checks["итого активных заданий = 69"] = total_active == 69

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
