# -*- coding: utf-8 -*-
"""Восстановление заданий курса «Python для ЕГЭ» (курс-контейнер 88).

== Инцидент ==
Курс 88 «Python для ЕГЭ» — контейнер из 10 модулей (курсы 90, 103-111).
Все модули оказались пусты: 353 адаптированных задания (250 SA_COM + 103 SC,
автопроверка) были ПЕРЕМЕЩЕНЫ в архивный курс 561 «Архив: контент Виктора
Комлева (legacy)» — у них сменили course_id. Контент (task_content,
solution_rules, difficulty_id) при перемещении сохранился.

Точную дату/причину перемещения установить нельзя: в tasks нет timestamps,
audit_event содержит только login-события. Скрипты нормализации tsk-112
курс 561 и модули Python НЕ трогали (работали с курсами 138-159).

== Восстановление ==
Вернуть 353 Python-задания из 561 в родные модули:
  - UPDATE course_id (561 → целевой модуль)
  - восстановить order_position из content_hub.task (та же БД Learn, схема content_hub)
  - task_content / solution_rules / difficulty_id / is_active / requirement_level — НЕ трогаем

Остальные ~193 не-Python задания курса 561 не затрагиваются.

== Маппинг темы → курс (по совпадению названий модулей) ==
  chisla-v-python-i-operatsii-s-nimi            → 103 (Числа)            26
  funktsii-v-python-sozdanie-sobstvennyh-funktsij → 104 (Функции)        43
  ispolzovanie-mnozhestv-set-v-python           → 105 (Множества)        57
  kak-ustanovit-python                          → 90  (Установка)         1
  pervaya-programma-na-python-osnovnye-konstruktsii → 106 (Первая прог.) 21
  rabota-so-slovaryami-v-python                 → 107 (Словари)          51
  rabota-so-strokami-v-python                   → 108 (Строки)           42
  spiski-massivy-v-python                       → 109 (Списки)           31
  tsikly-v-python                               → 110 (Циклы)            40
  uslovnye-konstruktsii-v-python                → 111 (Условные)         41
  ИТОГО                                                                  353

Задача: инцидент-восстановление (связано с tsk-112 «оптимизация контента»)
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ARCHIVE_COURSE = 561

# Тема (часть external_uid после wp:task:komlev:) → целевой курс-модуль + ожидаемое кол-во
THEME_TO_COURSE = {
    'chisla-v-python-i-operatsii-s-nimi':              (103, 26),
    'funktsii-v-python-sozdanie-sobstvennyh-funktsij': (104, 43),
    'ispolzovanie-mnozhestv-set-v-python':             (105, 57),
    'kak-ustanovit-python':                            (90,   1),
    'pervaya-programma-na-python-osnovnye-konstruktsii': (106, 21),
    'rabota-so-slovaryami-v-python':                   (107, 51),
    'rabota-so-strokami-v-python':                     (108, 42),
    'spiski-massivy-v-python':                         (109, 31),
    'tsikly-v-python':                                 (110, 40),
    'uslovnye-konstruktsii-v-python':                  (111, 41),
}
EXPECTED_TOTAL = 353

# Все 10 целевых модулей (для проверки пустоты ДО)
TARGET_COURSES = sorted({c for c, _ in THEME_TO_COURSE.values()})


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
        section("Снимок ДО — модули Python (должны быть пусты)")
        cur.execute("""
            SELECT course_id, count(*) AS cnt
            FROM tasks WHERE course_id = ANY(%s)
            GROUP BY course_id ORDER BY course_id
        """, (TARGET_COURSES,))
        rows = cur.fetchall()
        before_in_modules = sum(r[1] for r in rows)
        if rows:
            for r in rows:
                print(f"  курс {r[0]}: {r[1]} заданий")
        else:
            print("  все 10 модулей пусты ✓")
        print(f"  Итого в модулях ДО: {before_in_modules}")

        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id = %s AND external_uid ILIKE '%%python%%'
        """, (ARCHIVE_COURSE,))
        before_in_archive = cur.fetchone()[0]
        print(f"  Python-заданий в архиве {ARCHIVE_COURSE} ДО: {before_in_archive}")

        cur.execute("""
            SELECT count(*) FROM tasks WHERE course_id = %s
        """, (ARCHIVE_COURSE,))
        archive_total_before = cur.fetchone()[0]
        print(f"  Всего заданий в архиве {ARCHIVE_COURSE} ДО: {archive_total_before} "
              f"(не-Python: {archive_total_before - before_in_archive})")

        # ── Восстановление по темам ──────────────────────────────────────────
        section("Восстановление: course_id + order_position из content_hub")
        moved_total = 0
        per_theme_ok = True
        for theme, (target_course, expected) in THEME_TO_COURSE.items():
            uid_prefix = f"wp:task:komlev:{theme}:%"
            cur.execute("""
                UPDATE public.tasks t
                SET course_id = %s,
                    order_position = ch.order_position
                FROM content_hub.task ch
                WHERE ch.global_uid = t.external_uid
                  AND t.course_id = %s
                  AND t.external_uid LIKE %s
            """, (target_course, ARCHIVE_COURSE, uid_prefix))
            moved = cur.rowcount
            moved_total += moved
            mark = "OK" if moved == expected else "FAIL"
            if moved != expected:
                per_theme_ok = False
            print(f"  [{mark}] {theme} → курс {target_course}: {moved} (ожидалось {expected})")

        print(f"\n  Всего перемещено: {moved_total} (ожидалось {EXPECTED_TOTAL})")

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ — модули Python")
        cur.execute("""
            SELECT t.course_id, c.title,
                   count(*) AS cnt,
                   count(*) FILTER (WHERE t.task_content->>'type' = 'SA_COM') AS sa_com,
                   count(*) FILTER (WHERE t.task_content->>'type' = 'SC') AS sc,
                   min(t.order_position) AS mn, max(t.order_position) AS mx
            FROM tasks t JOIN courses c ON c.id = t.course_id
            WHERE t.course_id = ANY(%s)
            GROUP BY t.course_id, c.title ORDER BY t.course_id
        """, (TARGET_COURSES,))
        after_in_modules = 0
        for r in cur.fetchall():
            after_in_modules += r[2]
            print(f"  курс {r[0]} «{r[1]}»: {r[2]} (SA_COM={r[3]}, SC={r[4]}), pos {r[5]}-{r[6]}")
        print(f"  Итого в модулях ПОСЛЕ: {after_in_modules}")

        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id = %s AND external_uid ILIKE '%%python%%'
        """, (ARCHIVE_COURSE,))
        after_in_archive = cur.fetchone()[0]
        print(f"  Python-заданий в архиве {ARCHIVE_COURSE} ПОСЛЕ: {after_in_archive}")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id = %s", (ARCHIVE_COURSE,))
        archive_total_after = cur.fetchone()[0]
        print(f"  Всего заданий в архиве {ARCHIVE_COURSE} ПОСЛЕ: {archive_total_after} "
              f"(не-Python сохранены: {archive_total_after})")

        # ── Проверки ─────────────────────────────────────────────────────────
        section("Проверки")
        checks["перемещено ровно 353"] = moved_total == EXPECTED_TOTAL
        checks["каждая тема дала ожидаемое кол-во"] = per_theme_ok
        checks["Python в архиве 561 = 0"] = after_in_archive == 0
        checks["в модулях стало 353"] = after_in_modules == EXPECTED_TOTAL
        checks["не-Python архива не тронуты"] = archive_total_after == (archive_total_before - before_in_archive)

        # Контент не изменён: сверка task_content с content_hub по выборке
        cur.execute("""
            SELECT count(*) FROM public.tasks t
            JOIN content_hub.task ch ON ch.global_uid = t.external_uid
            WHERE t.course_id = ANY(%s)
              AND t.task_content->>'type' IS DISTINCT FROM ch.task_content->>'type'
        """, (TARGET_COURSES,))
        checks["тип контента совпадает с content_hub"] = cur.fetchone()[0] == 0

        # order_position в модулях совпадает с content_hub
        cur.execute("""
            SELECT count(*) FROM public.tasks t
            JOIN content_hub.task ch ON ch.global_uid = t.external_uid
            WHERE t.course_id = ANY(%s)
              AND t.order_position IS DISTINCT FROM ch.order_position
        """, (TARGET_COURSES,))
        checks["order_position восстановлен из content_hub"] = cur.fetchone()[0] == 0

        all_ok = True
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")
            if not ok:
                all_ok = False

        if all_ok and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: COMMIT. Задания Python для ЕГЭ восстановлены.")
        elif all_ok:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN успешен. Запусти с --apply для применения.")
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
