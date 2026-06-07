# -*- coding: utf-8 -*-
"""Курс 138 «Задание 3 ЕГЭ. Базы данных в Excel» — полная нормализация.

Операции:
 1. Материалы: requirement_level по навигатору + деактивация дубликата id=638.
 2. Difficulty по источникам и тексту stem:
      crylov (22)  → Легко
      Простые nav  → Легко  (kompege:1956, 2052, 2054, 2055; sdamgia:37492)
      Сложная nav  → Сложная (kompege:2112)
      ТГ по stem:  941, 848, 845, 598, 528, 524 → Легко;
                   711, 702, 670 → Средняя (уже верно);
                   569 → Сложная
      wp_nav (41)  → Сложная
 3. Деактивация нерелевантных ТГ-заданий:
      tg:ege:723 — регулярные выражения (не задание 3), tg:ege:16 — плейсхолдер
 4. requirement_level заданий по навигатору:
      Простые ☝️ → required, Средние ☝️ → required, Сложные → recommended
 5. Переупорядочивание pos 11+: Легко(33) → Средняя(15) → Сложная(43)

Итого активных заданий: 101 (103 − 2 деактивированных).
Источник: https://victor-komlev.ru/ege-po-informatike-zadanie-3-bazy-dannyh-v-excel/
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 138

DIFF_EASY   = 2
DIFF_NORMAL = 3
DIFF_HARD   = 4

REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'
REQ_SKIPPABLE   = 'skippable'

# ── Материалы ─────────────────────────────────────────────────────────────────
# (id, req_level, is_active или None)
# Навигатор: ☝️=required, без значка=recommended, 🔽=skippable
MATERIAL_UPDATES = [
    # Текстовые — все 4 ☝️, уже required, явно для надёжности
    (318, REQ_REQUIRED,    None),   # ☝️ Что нужно знать и уметь
    (319, REQ_REQUIRED,    None),   # ☝️ Что проверяет задание №3
    (320, REQ_REQUIRED,    None),   # ☝️ Простое задание: ВПР
    (321, REQ_REQUIRED,    None),   # ☝️ Сложное задание: ВПР + сводные
    (322, REQ_RECOMMENDED, None),   # без значка — Частые ошибки
    (324, REQ_SKIPPABLE,   None),   # не в навигаторе — Задания для тренировки
    # Видео
    (634, REQ_RECOMMENDED, None),   # без значка — Решение задания 3
    (635, REQ_RECOMMENDED, None),   # без значка — Поляков+сводные
    (636, REQ_REQUIRED,    None),   # ☝️ ВПР+сводные (вариант 2)
    (637, REQ_REQUIRED,    None),   # ☝️ Использование ВПР
    (638, REQ_RECOMMENDED, False),  # ДУБЛИКАТ id=636 — деактивировать
]
# Ожидаем: 10 req_level обновлений + 1 деактивация

# ── ТГ-задания: difficulty по stem ───────────────────────────────────────────
TG_DIFFICULTY = {
    'tg:ege:941': (DIFF_EASY,   'stem: «Уровень простой»'),
    'tg:ege:848': (DIFF_EASY,   'stem: «Уровень простой»'),
    'tg:ege:845': (DIFF_EASY,   'stem: «Уровень простой»'),
    'tg:ege:598': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:528': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:524': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:711': (DIFF_NORMAL, 'stem: «Уровень средний» — уже верно'),
    'tg:ege:702': (DIFF_NORMAL, 'stem: «Уровень средний» — уже верно'),
    'tg:ege:670': (DIFF_NORMAL, 'stem: «Уровень средний» — уже верно'),
    'tg:ege:569': (DIFF_HARD,   'stem: «Уровень сложный»'),
}
TG_DEACTIVATE = ['tg:ege:723', 'tg:ege:16']  # не задание 3 и плейсхолдер

# ── Простые из навигатора → Легко ─────────────────────────────────────────────
NAV_EASY = [
    'ext:d4:kompege:20260602:1956',
    'ext:d4:kompege:20260602:2052',
    'ext:d4:kompege:20260602:2054',
    'ext:d4:kompege:20260602:2055',
    'ext:d4:sdamgia:20260602:37492',
]

# ── Сложная из навигатора → Сложная ───────────────────────────────────────────
NAV_HARD = [
    'ext:d4:kompege:20260602:2112',
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

        # ── ШАГ 1: Материалы ─────────────────────────────────────────────────
        section("ШАГ 1: Материалы")
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

        checks["materials: обновлено 10 req_level"] = mat_upd == 10
        checks["materials: деактивирован дубликат id=638"] = mat_deact == 1

        # ── ШАГ 2: crylov → Легко ────────────────────────────────────────────
        section("ШАГ 2: crylov → Легко")
        cur.execute("""
            UPDATE tasks SET difficulty_id=%s
            WHERE course_id=%s AND is_active=true
              AND (external_uid ILIKE '%%crylov%%')
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
        """, (DIFF_EASY, COURSE_ID))
        crylov_fixed = cur.rowcount
        print(f"  crylov → Легко: {crylov_fixed} строк")
        checks["crylov: исправлено 22 задачи → Легко"] = crylov_fixed == 22

        # ── ШАГ 3: Простые навигатора → Легко ────────────────────────────────
        section("ШАГ 3: Простые навигатора → Легко")
        nav_easy_fixed = 0
        for uid in NAV_EASY:
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE external_uid=%s AND course_id=%s
            """, (DIFF_EASY, uid, COURSE_ID))
            nav_easy_fixed += cur.rowcount
            print(f"  {uid}: {cur.rowcount} строк")
        checks["nav Простые: исправлено 5"] = nav_easy_fixed == 5

        # ── ШАГ 4: Сложная навигатора → Сложная ──────────────────────────────
        section("ШАГ 4: Сложная навигатора → Сложная")
        nav_hard_fixed = 0
        for uid in NAV_HARD:
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE external_uid=%s AND course_id=%s
            """, (DIFF_HARD, uid, COURSE_ID))
            nav_hard_fixed += cur.rowcount
            print(f"  {uid}: {cur.rowcount} строк")
        checks["nav Сложные: исправлено 1"] = nav_hard_fixed == 1

        # ── ШАГ 5: ТГ-задания: difficulty по stem ────────────────────────────
        section("ШАГ 5: ТГ-задания — difficulty")
        tg_fixed = 0
        for uid, (diff_id, reason) in TG_DIFFICULTY.items():
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE external_uid=%s AND course_id=%s
            """, (diff_id, uid, COURSE_ID))
            tg_fixed += cur.rowcount
            print(f"  {uid} → diff={diff_id} ({reason}): {cur.rowcount} строк")
        checks["tg: обновлено 10 difficulty"] = tg_fixed == 10

        # ── ШАГ 6: Деактивация нерелевантных ТГ-заданий ──────────────────────
        section("ШАГ 6: Деактивация нерелевантных ТГ-заданий")
        tg_deact = 0
        for uid in TG_DEACTIVATE:
            cur.execute("""
                UPDATE tasks SET is_active=false
                WHERE external_uid=%s AND course_id=%s
            """, (uid, COURSE_ID))
            tg_deact += cur.rowcount
            print(f"  {uid}: {cur.rowcount} строк")
        checks["деактивировано 2 нерелевантных ТГ-задания"] = tg_deact == 2

        # ── ШАГ 7: wp_nav → Сложная ──────────────────────────────────────────
        section("ШАГ 7: wp_nav → Сложная")
        cur.execute("""
            UPDATE tasks SET difficulty_id=%s
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%' AND is_active=true
        """, (DIFF_HARD, COURSE_ID))
        wp_fixed = cur.rowcount
        print(f"  wp_nav → Сложная: {wp_fixed} строк")
        checks["wp_nav: исправлено 41"] = wp_fixed == 41

        # ── ШАГ 8: requirement_level заданий ─────────────────────────────────
        section("ШАГ 8: requirement_level заданий")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%'
        """, (REQ_REQUIRED, COURSE_ID))
        print(f"  вводные tsk109 → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
              AND is_active=true
        """, (REQ_REQUIRED, COURSE_ID, DIFF_EASY))
        print(f"  Легко (non-vvod) → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
              AND is_active=true
        """, (REQ_REQUIRED, COURSE_ID, DIFF_NORMAL))
        print(f"  Средняя (non-vvod) → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s AND is_active=true
        """, (REQ_RECOMMENDED, COURSE_ID, DIFF_HARD))
        hard_cnt = cur.rowcount
        print(f"  Сложная → recommended: {hard_cnt}")
        checks["Сложных active = 43"] = hard_cnt == 43

        # ── ШАГ 9: Переупорядочивание pos 11+ ────────────────────────────────
        section("ШАГ 9: Переупорядочивание")
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
        # Деактивированные — в конец
        cur.execute("""
            SELECT max(order_position) FROM tasks
            WHERE course_id=%s AND is_active=true
        """, (COURSE_ID,))
        max_pos = cur.fetchone()[0] or 0
        for i, uid in enumerate(TG_DEACTIVATE, start=1):
            cur.execute("""
                UPDATE tasks SET order_position=%s
                WHERE course_id=%s AND external_uid=%s
            """, (max_pos + i, COURSE_ID, uid))

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) FILTER (WHERE t.is_active) as active,
                   count(*) FILTER (WHERE NOT t.is_active) as inactive,
                   min(t.order_position) FILTER (WHERE t.is_active),
                   max(t.order_position) FILTER (WHERE t.is_active)
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
            ORDER BY t.difficulty_id, t.requirement_level
        """, (COURSE_ID,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}) req={r[2]}: active={r[3]}, inactive={r[4]}, pos {r[5]}-{r[6]}")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,))
        total_active = cur.fetchone()[0]
        print(f"  Итого активных: {total_active}")

        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        dups = cur.fetchall()
        checks["нет дублей order_position"] = len(dups) == 0
        checks["итого активных = 101"] = total_active == 101

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
