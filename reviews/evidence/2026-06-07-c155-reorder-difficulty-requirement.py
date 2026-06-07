# -*- coding: utf-8 -*-
"""Курс 155 «Задание 4 ЕГЭ. Неравномерное кодирование и условие Фано» — нормализация.

Особенность: все задания из навигатора (Простые/Средние/Сложные kompege/sdamgia)
отсутствуют в LMS. Из стандартных источников есть только crylov + tg + wp_nav.
Задачи с навигатора зафиксированы в реестре nav-missing-tasks.md (26 строк).

Операции:
 1. Материалы: id=639/640 → recommended; id=641 деактивировать (дубликат id=640).
 2. crylov (20) → Легко
 3. ТГ-задания:
      tg:ege:704 → Легко (stem: «Уровень легкий»)
      tg:ege:527 → Легко (разбор kompege:114 из раздела «Простые» навигатора)
 4. wp_nav (24) → Сложная
 5. requirement_level: Легко → required, Сложная → recommended
 6. Переупорядочивание pos 11+: Легко(22) → Сложная(24)

Итого активных: 56 (без изменений — деактивируется только дубликат материала).
Источник: https://victor-komlev.ru/zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano/
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 155
DIFF_EASY = 2
DIFF_HARD = 4
REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'

MATERIAL_UPDATES = [
    (639, REQ_RECOMMENDED, None),   # Решение задания 4 — нет в навигаторе как ☝️
    (640, REQ_RECOMMENDED, None),   # Решение задания 4 (вариант 2)
    (641, REQ_RECOMMENDED, False),  # ДУБЛИКАТ id=640 (тот же VK URL) — деактивировать
]

TG_DIFFICULTY = {
    'tg:ege:704': (DIFF_EASY, 'stem: «Уровень легкий»'),
    'tg:ege:527': (DIFF_EASY, 'разбор kompege:114 (Простые навигатора)'),
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
                   count(*) FILTER (WHERE t.is_active) AS active,
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
                    "WHERE id=%s AND course_id=%s", (req, mid, COURSE_ID))
                mat_deact += cur.rowcount
                print(f"  mat {mid}: is_active=false — {cur.rowcount} строк")
            else:
                cur.execute(
                    "UPDATE materials SET requirement_level=%s "
                    "WHERE id=%s AND course_id=%s", (req, mid, COURSE_ID))
                mat_upd += cur.rowcount
                print(f"  mat {mid}: req={req} — {cur.rowcount} строк")

        checks["materials: обновлено 2 req_level"] = mat_upd == 2
        checks["materials: деактивирован дубликат id=641"] = mat_deact == 1

        # ── ШАГ 2: crylov → Легко ────────────────────────────────────────────
        section("ШАГ 2: crylov → Легко")
        cur.execute("""
            UPDATE tasks SET difficulty_id=%s
            WHERE course_id=%s AND is_active=true
              AND external_uid ILIKE '%%crylov%%'
              AND external_uid NOT ILIKE 'lms:tsk109:%%'
        """, (DIFF_EASY, COURSE_ID))
        crylov_fixed = cur.rowcount
        print(f"  crylov → Легко: {crylov_fixed} строк")
        checks["crylov: исправлено 20"] = crylov_fixed == 20

        # ── ШАГ 3: ТГ-задания ────────────────────────────────────────────────
        section("ШАГ 3: ТГ-задания → difficulty")
        tg_fixed = 0
        for uid, (diff_id, reason) in TG_DIFFICULTY.items():
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE external_uid=%s AND course_id=%s
            """, (diff_id, uid, COURSE_ID))
            tg_fixed += cur.rowcount
            print(f"  {uid} → diff={diff_id} ({reason}): {cur.rowcount} строк")
        checks["tg: исправлено 2"] = tg_fixed == 2

        # ── ШАГ 4: wp_nav → Сложная ──────────────────────────────────────────
        section("ШАГ 4: wp_nav → Сложная")
        cur.execute("""
            UPDATE tasks SET difficulty_id=%s
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%' AND is_active=true
        """, (DIFF_HARD, COURSE_ID))
        wp_fixed = cur.rowcount
        print(f"  wp_nav → Сложная: {wp_fixed} строк")
        checks["wp_nav: исправлено 24"] = wp_fixed == 24

        # ── ШАГ 5: requirement_level заданий ─────────────────────────────────
        section("ШАГ 5: requirement_level")
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'lms:tsk109:%%'
        """, (REQ_REQUIRED, COURSE_ID))
        print(f"  вводные → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s
              AND external_uid NOT ILIKE 'lms:tsk109:%%' AND is_active=true
        """, (REQ_REQUIRED, COURSE_ID, DIFF_EASY))
        print(f"  Легко (non-vvod) → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND difficulty_id=%s AND is_active=true
        """, (REQ_RECOMMENDED, COURSE_ID, DIFF_HARD))
        hard_cnt = cur.rowcount
        print(f"  Сложная → recommended: {hard_cnt}")
        checks["Сложных active = 24"] = hard_cnt == 24

        # ── ШАГ 6: Переупорядочивание ────────────────────────────────────────
        section("ШАГ 6: Переупорядочивание")
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
