# -*- coding: utf-8 -*-
"""Курс 140 «Задание 1 ЕГЭ. Информационные модели» — полная нормализация.

Операции (в одной транзакции):
 1. Переупорядочивание заданий pos 11-89:
      было:  Средняя(11-22) → Лёгкая(23-42) → Средняя(43-89)
      стало: Лёгкая(11-30) → Средняя(31-89)
 2. Материалы: requirement_level по навигатору + деактивация дубликата.
 3. Задания: requirement_level по группам.
 4. Переметка ТГ-канал + wp-навигатор: difficulty_id 3→4 (Средняя→Сложная).

Источник правды: https://victor-komlev.ru/navigator-po-zadaniyu-1-ege/
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 140

DIFF_EASY   = 2   # Легко
DIFF_NORMAL = 3   # Средняя
DIFF_HARD   = 4   # Сложная

REQ_REQUIRED    = 'required'    # ☝️
REQ_RECOMMENDED = 'recommended' # без значка (опционально)
REQ_SKIPPABLE   = 'skippable'   # 🔽

# ── Материалы ─────────────────────────────────────────────────────────────────
# Навигатор:
#   358 Граф                                    ☝️  required   (уже верно)
#   359 Типы заданий                                recommended
#   360 Поиск оптимального маршрута             🔽  skippable
#   361 Однозначное соотнесение таблицы и графа ☝️  required   (уже верно)
#   362 Неоднозначное соотнесение               ☝️  required   (уже верно)
#   540 Видео: Решение задания 1 (вариант 1)        recommended
#   541 Видео: Решение задания 1 (вариант 2)        recommended
#   542 Видео: Решение задания 1 (вариант 3)    ☝️  required
#   543 ДУБЛИКАТ вариант 3 — деактивировать
#   365 YouTube (старое) — skippable

MATERIAL_REQ_UPDATES = [
    (359, REQ_RECOMMENDED, None),    # Типы заданий — без значка
    (360, REQ_SKIPPABLE,   None),    # Поиск маршрута — устаревшее 🔽
    (540, REQ_RECOMMENDED, None),    # Видео вариант 1 — без значка
    (541, REQ_RECOMMENDED, None),    # Видео вариант 2 — без значка
    (542, REQ_REQUIRED,    None),    # Видео вариант 3 ☝️ (уже required, для явности)
    (543, REQ_RECOMMENDED, False),   # Дубликат — деактивировать
    (365, REQ_SKIPPABLE,   None),    # YouTube — старое 🔽
]


# ── helpers ───────────────────────────────────────────────────────────────────

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


# ── main ──────────────────────────────────────────────────────────────────────

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
            SELECT difficulty_id, d.name_ru,
                   count(*) as cnt,
                   min(t.order_position) as min_pos,
                   max(t.order_position) as max_pos
            FROM tasks t
            JOIN difficulties d ON d.id = t.difficulty_id
            WHERE t.course_id = %s AND t.order_position BETWEEN 11 AND 89
            GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
        """, (COURSE_ID,))
        for row in cur.fetchall():
            print(f"  difficulty={row[0]}({row[1]}): cnt={row[2]}, pos {row[3]}-{row[4]}")

        # ── ШАГ 1: Переупорядочивание заданий pos 11-89 ──────────────────────
        section("ШАГ 1: Переупорядочивание")

        # 1a. Сдвинуть весь блок 11-89 во временный диапазон 1011-1089
        cur.execute("""
            UPDATE tasks SET order_position = order_position + 1000
            WHERE course_id = %s AND order_position BETWEEN 11 AND 89
        """, (COURSE_ID,))
        shifted = cur.rowcount
        print(f"  сдвинуто в temp space (+1000): {shifted} заданий")

        # 1b. Лёгкие (были 23-42, теперь 1023-1042) → 11-30  (сдвиг -1012)
        cur.execute("""
            UPDATE tasks SET order_position = order_position - 1012
            WHERE course_id = %s
              AND order_position BETWEEN 1023 AND 1042
              AND difficulty_id = %s
        """, (COURSE_ID, DIFF_EASY))
        easy_moved = cur.rowcount
        print(f"  Лёгкие → pos 11-30: {easy_moved} заданий")

        # 1c. Средние, которые были на 11-22 (теперь 1011-1022) → 31-42  (сдвиг -980)
        cur.execute("""
            UPDATE tasks SET order_position = order_position - 980
            WHERE course_id = %s AND order_position BETWEEN 1011 AND 1022
        """, (COURSE_ID,))
        norm_moved = cur.rowcount
        print(f"  Средние 11-22 → pos 31-42: {norm_moved} заданий")

        # 1d. Средние, которые были на 43-89 (теперь 1043-1089) → 43-89  (сдвиг -1000)
        cur.execute("""
            UPDATE tasks SET order_position = order_position - 1000
            WHERE course_id = %s AND order_position BETWEEN 1043 AND 1089
        """, (COURSE_ID,))
        tail_moved = cur.rowcount
        print(f"  Средние 43-89 → остаются 43-89: {tail_moved} заданий")

        checks["reorder: сдвинуто 79 заданий"] = shifted == 79
        checks["reorder: Лёгких перемещено 20"] = easy_moved == 20
        checks["reorder: Средних (11-22) перемещено 12"] = norm_moved == 12
        checks["reorder: Средних (43-89) восстановлено 47"] = tail_moved == 47

        # Проверка позиций после переупорядочивания
        cur.execute("""
            SELECT difficulty_id, count(*) as cnt,
                   min(order_position) as min_pos, max(order_position) as max_pos
            FROM tasks WHERE course_id = %s AND order_position BETWEEN 11 AND 89
            GROUP BY difficulty_id ORDER BY difficulty_id
        """, (COURSE_ID,))
        rows = cur.fetchall()
        print("  После переупорядочивания:")
        for r in rows:
            print(f"    difficulty={r[0]}: cnt={r[1]}, pos {r[2]}-{r[3]}")

        easy_row  = next((r for r in rows if r[0] == DIFF_EASY),  None)
        norm_row  = next((r for r in rows if r[0] == DIFF_NORMAL), None)

        checks["reorder: Лёгкие на pos 11-30"] = (
            easy_row is not None and easy_row[2] == 11 and easy_row[3] == 30
        )
        checks["reorder: Средние начинаются с 31"] = (
            norm_row is not None and norm_row[2] == 31
        )

        # ── ШАГ 2: Обязательность материалов ─────────────────────────────────
        section("ШАГ 2: Обязательность материалов")
        mat_updates = 0
        mat_deact   = 0
        for mid, req, is_active in MATERIAL_REQ_UPDATES:
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
                mat_updates += cur.rowcount
                print(f"  mat {mid}: req={req} — {cur.rowcount} строк")

        checks["materials: обновлено req_level"] = mat_updates == 6
        checks["materials: деактивирован дубликат (id=543)"] = mat_deact == 1

        # ── ШАГ 3: Обязательность заданий ────────────────────────────────────
        section("ШАГ 3: Обязательность заданий")

        # 3a. Всё вводные (tsk109) — required (уже так, явно для надёжности)
        cur.execute("""
            UPDATE tasks SET requirement_level = %s
            WHERE course_id = %s AND external_uid ILIKE 'lms:tsk109:%%'
        """, (REQ_REQUIRED, COURSE_ID))
        print(f"  вводные tsk109 → required: {cur.rowcount}")

        # 3b. Лёгкие (Крылов PDF) — required (Простые ☝️)
        cur.execute("""
            UPDATE tasks SET requirement_level = %s
            WHERE course_id = %s AND difficulty_id = %s
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
        """, (REQ_REQUIRED, COURSE_ID, DIFF_EASY))
        print(f"  Лёгкие (Крылов) → required: {cur.rowcount}")

        # 3c. Средние (polyakov/kompege/sdamgia/crylov) — required (Средние ☝️)
        #     Но НЕ ТГ-канал и НЕ wp_nav — они скоро станут Сложными
        cur.execute("""
            UPDATE tasks SET requirement_level = %s
            WHERE course_id = %s AND difficulty_id = %s
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
              AND external_uid NOT ILIKE 'tg:ege:%%'
              AND external_uid NOT ILIKE 'wp_nav:%%'
        """, (REQ_REQUIRED, COURSE_ID, DIFF_NORMAL))
        print(f"  Средние (polyakov/kompege/sdamgia/crylov) → required: {cur.rowcount}")

        # 3d. Будущие Сложные (ТГ + wp_nav) — recommended (опциональные)
        cur.execute("""
            UPDATE tasks SET requirement_level = %s
            WHERE course_id = %s
              AND (external_uid ILIKE 'tg:ege:%%' OR external_uid ILIKE 'wp_nav:%%')
        """, (REQ_RECOMMENDED, COURSE_ID))
        future_hard_req = cur.rowcount
        print(f"  Будущие Сложные (ТГ+wp_nav) → recommended: {future_hard_req}")

        checks["tasks: будущих Сложных 46"] = future_hard_req == 46

        # ── ШАГ 4: Переметка ТГ + wp_nav → difficulty_id=4 (Сложная) ─────────
        section("ШАГ 4: Переметка сложности")

        cur.execute("""
            UPDATE tasks SET difficulty_id = %s
            WHERE course_id = %s
              AND (external_uid ILIKE 'tg:ege:%%' OR external_uid ILIKE 'wp_nav:%%')
        """, (DIFF_HARD, COURSE_ID))
        relabeled = cur.rowcount
        print(f"  difficulty 3→4 (Сложная): {relabeled} заданий")

        checks["difficulty: переметено 46 заданий"] = relabeled == 46

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) as cnt,
                   min(t.order_position) as min_pos,
                   max(t.order_position) as max_pos
            FROM tasks t
            JOIN difficulties d ON d.id = t.difficulty_id
            WHERE t.course_id = %s AND t.order_position BETWEEN 1 AND 89
            GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
            ORDER BY t.difficulty_id, t.requirement_level
        """, (COURSE_ID,))
        for row in cur.fetchall():
            print(f"  diff={row[0]}({row[1]}) req={row[2]}: cnt={row[3]}, pos {row[4]}-{row[5]}")

        cur.execute("""
            SELECT id, title, order_position, is_active, requirement_level
            FROM materials WHERE course_id = %s ORDER BY order_position
        """, (COURSE_ID,))
        print("\n  Материалы:")
        for row in cur.fetchall():
            print(f"  id={row[0]} pos={row[2]} active={row[3]} req={row[4]}  {row[1][:50]}")

        # Проверка на дубликаты позиций заданий
        cur.execute("""
            SELECT order_position, count(*) as cnt
            FROM tasks WHERE course_id = %s
            GROUP BY order_position HAVING count(*) > 1
        """, (COURSE_ID,))
        dups = cur.fetchall()
        checks["нет дублей order_position"] = len(dups) == 0
        if dups:
            print(f"\n  ВНИМАНИЕ: дубли позиций: {dups}")

        # ── Итог проверок ─────────────────────────────────────────────────────
        section("Проверки")
        all_ok = True
        for name, ok in checks.items():
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {name}")
            if not ok:
                all_ok = False

        if all_ok and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
        elif all_ok:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN пройден успешно. Запусти с --apply для применения.")
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
