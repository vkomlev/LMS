# -*- coding: utf-8 -*-
"""Курс 140 (Задание 1) — нормализация req_level и порядка позиций.

Проблема: все задания и материалы стали required=required после ROLLBACK
из-за hardcoded-проверки итого активных в предыдущих скриптах.

Что делает скрипт:
  ШАГ 1: Материалы — req_level по иконкам навигатора (4 расхождения)
    id=359 → recommended (Типы заданий, нет иконки)
    id=360 → skippable  (Поиск маршрута устаревшее, 🔽)
    id=540 → recommended (Решение задания 1, нет иконки)
    id=541 → recommended (Решение задания 1 вариант 2, нет иконки)
  ШАГ 2: Задания req_level
    вводные (lms:tsk109:*) → required
    diff=1,2,3 non-vvod → required
    diff=4 Сложная → recommended
  ШАГ 3: Переупорядочивание
    вводные → pos 1-10 (сохраняем их текущий относительный порядок)
    non-vvod active → pos 11+ ORDER BY difficulty_id ASC, order_position ASC

Источник данных: nav_parser.py, запущенный на navigator-po-zadaniyu-1-ege
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 140

# Материалы: {id: (текущее, нужное)} — из вывода nav_parser
MAT_UPDATES = {
    359: 'recommended',   # Типы заданий (нет иконки в навигаторе)
    360: 'skippable',     # Поиск маршрута (устаревшее) (🔽)
    540: 'recommended',   # Решение задания 1 (нет иконки)
    541: 'recommended',   # Решение задания 1 (вариант 2) (нет иконки)
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

        # ── Снимок ДО ────────────────────────────────────────────────────────
        section("Снимок ДО — задания")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) FILTER (WHERE t.is_active) AS active
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
            ORDER BY t.difficulty_id, t.requirement_level
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}) req={r[2]}: active={r[3]}")

        section("Снимок ДО — позиции")
        cur.execute("""
            SELECT difficulty_id,
                   min(order_position) FILTER (WHERE is_active) AS mn,
                   max(order_position) FILTER (WHERE is_active) AS mx
            FROM tasks WHERE course_id=%s
            GROUP BY difficulty_id ORDER BY difficulty_id
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}: pos {r[1]}-{r[2]}")

        section("Снимок ДО — материалы")
        cur.execute("""
            SELECT id, requirement_level, is_active, external_uid
            FROM materials WHERE course_id=%s ORDER BY id
        """, (COURSE_ID,))
        for r in cur.fetchall():
            active_str = "active" if r[2] else "inactive"
            print(f"  id={r[0]} req={r[1]} ({active_str}) {r[3]}")

        # ── ШАГ 1: Материалы ─────────────────────────────────────────────────
        section("ШАГ 1: Материалы req_level")
        for mat_id, new_req in MAT_UPDATES.items():
            cur.execute(
                "UPDATE materials SET requirement_level=%s WHERE id=%s AND course_id=%s",
                (new_req, mat_id, COURSE_ID)
            )
            print(f"  id={mat_id} → {new_req}: {cur.rowcount} строк")
        checks["материалов обновлено = 4"] = sum(
            1 for mid in MAT_UPDATES
        ) == 4

        # Проверить что не все required
        cur.execute("""
            SELECT count(*) FROM materials
            WHERE course_id=%s AND is_active=true AND requirement_level='required'
        """, (COURSE_ID,))
        mat_req = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM materials WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        mat_total = cur.fetchone()[0]
        checks["материалы: не все required"] = mat_req < mat_total

        # ── ШАГ 2: req_level заданий ─────────────────────────────────────────
        section("ШАГ 2: req_level заданий")

        cur.execute("""
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%'
        """, (COURSE_ID,))
        print(f"  вводные → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND difficulty_id < 4
              AND external_uid NOT ILIKE 'lms:tsk109:%%' AND is_active=true
        """, (COURSE_ID,))
        print(f"  diff<4 non-vvod active → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
        """, (COURSE_ID,))
        hard_cnt = cur.rowcount
        print(f"  diff=4 Сложная → recommended: {hard_cnt}")

        # ── ШАГ 3: Переупорядочивание ────────────────────────────────────────
        section("ШАГ 3: Переупорядочивание")

        # Вводные — сохраняем текущий относительный порядок
        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%' AND is_active=true
            ORDER BY order_position ASC
        """, (COURSE_ID,))
        vvod_ids = [r[0] for r in cur.fetchall()]
        print(f"  вводных: {len(vvod_ids)}")

        # Non-вводные активные — сортируем по difficulty + текущей pos
        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND external_uid NOT ILIKE 'lms:tsk109:%%' AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC
        """, (COURSE_ID,))
        non_vvod_ids = [r[0] for r in cur.fetchall()]
        print(f"  non-vvod active: {len(non_vvod_ids)}")

        # Сдвигаем все активные в temp-пространство (+2000)
        cur.execute("""
            UPDATE tasks SET order_position = order_position + 2000
            WHERE course_id=%s AND is_active=true
        """, (COURSE_ID,))

        # Вводные → 1-10
        for new_pos, task_id in enumerate(vvod_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

        # Non-вводные active → 11+
        for new_pos, task_id in enumerate(non_vvod_ids, start=11):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

        print(f"  позиции: вводные 1-{len(vvod_ids)}, "
              f"non-vvod {len(vvod_ids)+1}-{len(vvod_ids)+len(non_vvod_ids)}")

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) FILTER (WHERE t.is_active) AS active,
                   min(t.order_position) FILTER (WHERE t.is_active) AS mn,
                   max(t.order_position) FILTER (WHERE t.is_active) AS mx
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
        total = cur.fetchone()[0]
        print(f"  Итого активных: {total}")

        # ── Проверки ──────────────────────────────────────────────────────────
        section("Проверки")

        # Все Сложные → recommended
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
        """, (COURSE_ID,))
        hard_total = cur.fetchone()[0]
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
              AND requirement_level='recommended'
        """, (COURSE_ID,))
        hard_rec = cur.fetchone()[0]
        checks["все Сложные active = recommended"] = hard_rec == hard_total

        # Все diff<4 active → required
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND difficulty_id < 4 AND is_active=true
        """, (COURSE_ID,))
        easy_total = cur.fetchone()[0]
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND difficulty_id < 4 AND is_active=true
              AND requirement_level='required'
        """, (COURSE_ID,))
        easy_req = cur.fetchone()[0]
        checks["все Легко/Средняя/Теория = required"] = easy_req == easy_total

        # Вводные ≤ pos 10
        cur.execute("""
            SELECT max(order_position) FROM tasks
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%' AND is_active=true
        """, (COURSE_ID,))
        vvod_max = cur.fetchone()[0]
        checks[f"вводные ≤ pos 10 (факт max={vvod_max})"] = vvod_max <= 10

        # Нет дублей позиций
        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        dups = cur.fetchall()
        checks["нет дублей order_position"] = len(dups) == 0

        # Блоки difficulty не пересекаются
        cur.execute("""
            SELECT difficulty_id,
                   min(order_position) AS mn, max(order_position) AS mx
            FROM tasks
            WHERE course_id=%s AND is_active=true
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
            GROUP BY difficulty_id ORDER BY difficulty_id
        """, (COURSE_ID,))
        blocks = cur.fetchall()
        if len(blocks) >= 2:
            contiguous = all(blocks[i][2] < blocks[i+1][1] for i in range(len(blocks)-1))
        else:
            contiguous = True
        checks["блоки difficulty не пересекаются"] = contiguous

        # Материалы не все required
        checks["материалы: не все required"] = mat_req < mat_total

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
