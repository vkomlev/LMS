# -*- coding: utf-8 -*-
"""Курс 156 (Задание 5) — нормализация difficulty, req_level и порядка.

== Источник классификации ==
nav_parser на navigator-po-zadaniyu-5-ege + TG-посты (screenshot) + stem markers

== Что делает скрипт ==

ШАГ 0: Деактивация вспомогательных TG-заданий
  tg:ege:293, 294, 643 — «Вспомогательное задание 5_X» из TG-канала;
  заменены вводными lms:c156:vvod:5_2..5_5 → деактивировать.

ШАГ 1: Материалы — req_level по иконкам навигатора (3 расхождения)
  id=642 → recommended  (нет иконки ☝️)
  id=644 → recommended  (нет иконки ☝️)
  id=645 → recommended  (нет иконки ☝️)

ШАГ 2: Difficulty заданий по навигатору
  А) wp_nav (75 шт.) → все diff=4 Сложная
     ИСКЛЮЧЕНИЕ: wp_nav kompege:49 → diff=2 Легко (Простые секция навигатора)
  Б) kompege_direct Простые (4, 262, 350) → diff=2 Легко
  В) kompege_direct Сложная (5899) → diff=4 Сложная
  Г) sdamgia Сложные (61385, 56505, 73831) → diff=4 Сложная
  Д) Крылов (все форматы) → diff=2 Легко
     ИСКЛЮЧЕНИЕ: crylov:v11t5 («Уровень средний» в stem) → diff=3, не трогаем
  Е) TG Легкие (557, 594, 597, 636, 781, 843, 867) → diff=2 Легко
     NB: tg:ege:781 — «Уровень простой» подтверждён скриншотом TG-поста;
         в stem этот маркер не сохранился при импорте
  Ж) TG Сложная (713) → diff=4 Сложная
  З) Остальные (vvod lms:c156:vvod:*, sdamgia/polyakov/kompege Средние,
     TG средние, crylov:v11t5) — difficulty не трогаем (уже верна)

ШАГ 3: req_level
  вводные (lms:c156:vvod:*) → required
  diff<4 non-vvod active → required
  diff=4 active → recommended

ШАГ 4: Переупорядочивание
  вводные → pos 1-4 (сохраняем текущий порядок)
  non-vvod active → pos 11+ ORDER BY difficulty_id ASC, order_position ASC

== Особые случаи ==
  kompege:206 появляется в Средних И Сложных → консервативно Средняя (diff=3, не трогаем)
  Вводные используют формат lms:c156:vvod:*, НЕ lms:tsk109:*

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 156

# Материалы: id → нужное req_level
MAT_UPDATES = {
    642: 'recommended',  # Разбор заданий 5 и 14 (нет ☝️)
    644: 'recommended',  # Как посчитать сумму цифр (нет ☝️)
    645: 'recommended',  # Как быстро получить сумму цифр (нет ☝️)
}

# ШАГ 0: вспомогательные TG → деактивировать
DEACTIVATE_UIDS = ('tg:ege:293', 'tg:ege:294', 'tg:ege:643')

# ШАГ 2Б: kompege_direct Простые → diff=2
KOMPEGE_EASY_UIDS = (
    'ext:d4:kompege:20260602:4',
    'ext:d4:kompege:20260602:262',
    'ext:d4:kompege:20260602:350',
)

# ШАГ 2В: kompege_direct Сложная → diff=4
KOMPEGE_HARD_UID = 'ext:d4:kompege:20260602:5899'

# ШАГ 2Г: sdamgia Сложные → diff=4
SDAMGIA_HARD_UIDS = (
    'ext:d4:sdamgia:20260602:61385',
    'ext:d4:sdamgia:20260602:56505',
    'ext:d4:sdamgia:20260602:73831',
)

# ШАГ 2Е: TG Легкие → diff=2
# tg:ege:781 подтверждён скриншотом («Уровень простой»); маркер не в stem
TG_EASY_UIDS = (
    'tg:ege:557', 'tg:ege:594', 'tg:ege:597',
    'tg:ege:636', 'tg:ege:781', 'tg:ege:843', 'tg:ege:867',
)

# ШАГ 2Ж: TG Сложная → diff=4
TG_HARD_UID = 'tg:ege:713'

# Паттерн вводных для course 156 (ОТЛИЧАЕТСЯ от lms:tsk109:* других курсов!)
VVOD_PATTERN = 'lms:c156:vvod:%'


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

        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        print(f"  Итого активных ДО: {cur.fetchone()[0]}")

        section("Снимок ДО — материалы")
        cur.execute(
            "SELECT id, requirement_level, is_active, external_uid "
            "FROM materials WHERE course_id=%s ORDER BY id",
            (COURSE_ID,),
        )
        for r in cur.fetchall():
            print(f"  id={r[0]} req={r[1]} ({'active' if r[2] else 'inactive'}) {r[3]}")

        # ── ШАГ 0: Деактивация вспомогательных ──────────────────────────────
        section("ШАГ 0: Деактивация вспомогательных TG")
        for uid in DEACTIVATE_UIDS:
            cur.execute(
                "UPDATE tasks SET is_active=false "
                "WHERE course_id=%s AND external_uid=%s AND is_active=true",
                (COURSE_ID, uid),
            )
            print(f"  {uid} → is_active=false: {cur.rowcount} строк")

        # ── ШАГ 1: Материалы req_level ────────────────────────────────────────
        section("ШАГ 1: Материалы req_level")
        for mat_id, new_req in MAT_UPDATES.items():
            cur.execute(
                "UPDATE materials SET requirement_level=%s WHERE id=%s AND course_id=%s",
                (new_req, mat_id, COURSE_ID),
            )
            print(f"  id={mat_id} → {new_req}: {cur.rowcount} строк")

        cur.execute(
            "SELECT count(*) FROM materials WHERE course_id=%s AND is_active=true AND requirement_level='required'",
            (COURSE_ID,),
        )
        mat_req = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM materials WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        mat_total = cur.fetchone()[0]

        # ── ШАГ 2: Difficulty ─────────────────────────────────────────────────
        section("ШАГ 2: Difficulty заданий")

        # А) wp_nav все → diff=4
        cur.execute("""
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%' AND is_active=true
        """, (COURSE_ID,))
        print(f"  wp_nav все → diff=4: {cur.rowcount}")

        # А-исключение) wp_nav kompege:49 → diff=2
        cur.execute("""
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
              AND (task_content->>'source_kind') = 'kompege'
              AND (task_content->>'source_task_id') = '49'
        """, (COURSE_ID,))
        print(f"  wp_nav kompege:49 → diff=2 (исключение, Простые): {cur.rowcount}")

        # Б) kompege_direct Простые → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 "
            "WHERE course_id=%s AND external_uid = ANY(%s) AND is_active=true",
            (COURSE_ID, list(KOMPEGE_EASY_UIDS)),
        )
        print(f"  kompege_direct Простые → diff=2: {cur.rowcount}")

        # В) kompege_direct Сложная → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 "
            "WHERE course_id=%s AND external_uid=%s AND is_active=true",
            (COURSE_ID, KOMPEGE_HARD_UID),
        )
        print(f"  kompege_direct Сложная (5899) → diff=4: {cur.rowcount}")

        # Г) sdamgia Сложные → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 "
            "WHERE course_id=%s AND external_uid = ANY(%s) AND is_active=true",
            (COURSE_ID, list(SDAMGIA_HARD_UIDS)),
        )
        print(f"  sdamgia Сложные → diff=4: {cur.rowcount}")

        # Д) Крылов → diff=2 (все форматы), кроме crylov:v11t5
        cur.execute("""
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND is_active=true
              AND external_uid != 'crylov:v11t5'
              AND (
                external_uid ILIKE 'crylov:%%'
                OR external_uid ILIKE 'ext:pdf:d4:pdf:crylov:%%'
                OR external_uid ILIKE 'pdf:d4:pdf:crylov:%%'
              )
        """, (COURSE_ID,))
        print(f"  Крылов (все форматы, кроме v11) → diff=2: {cur.rowcount}")

        # Е) TG Легкие → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 "
            "WHERE course_id=%s AND external_uid = ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"  TG Легкие → diff=2: {cur.rowcount}")

        # Ж) TG Сложная → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 "
            "WHERE course_id=%s AND external_uid=%s AND is_active=true",
            (COURSE_ID, TG_HARD_UID),
        )
        print(f"  TG Сложная (713) → diff=4: {cur.rowcount}")

        # ── ШАГ 3: req_level ─────────────────────────────────────────────────
        section("ШАГ 3: req_level заданий")

        cur.execute("""
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND external_uid ILIKE %s
        """, (COURSE_ID, VVOD_PATTERN))
        print(f"  вводные (lms:c156:vvod:*) → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND difficulty_id < 4
              AND external_uid NOT ILIKE %s AND is_active=true
        """, (COURSE_ID, VVOD_PATTERN))
        print(f"  diff<4 non-vvod active → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
        """, (COURSE_ID,))
        print(f"  diff=4 Сложная → recommended: {cur.rowcount}")

        # ── ШАГ 4: Переупорядочивание ────────────────────────────────────────
        section("ШАГ 4: Переупорядочивание")

        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true
            ORDER BY order_position ASC
        """, (COURSE_ID, VVOD_PATTERN))
        vvod_ids = [r[0] for r in cur.fetchall()]
        print(f"  вводных: {len(vvod_ids)}")

        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND external_uid NOT ILIKE %s AND is_active=true
            ORDER BY difficulty_id ASC, order_position ASC
        """, (COURSE_ID, VVOD_PATTERN))
        non_vvod_ids = [r[0] for r in cur.fetchall()]
        print(f"  non-vvod active: {len(non_vvod_ids)}")

        # Сдвиг в temp-пространство
        cur.execute("""
            UPDATE tasks SET order_position = order_position + 2000
            WHERE course_id=%s AND is_active=true
        """, (COURSE_ID,))

        # Вводные → 1..len(vvod_ids)
        for new_pos, task_id in enumerate(vvod_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

        # Non-vvod → 11+ (резерв 5-10 для будущих вводных)
        for new_pos, task_id in enumerate(non_vvod_ids, start=11):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

        print(f"  позиции: вводные 1-{len(vvod_ids)}, "
              f"non-vvod 11-{10 + len(non_vvod_ids)}")

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

        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        total = cur.fetchone()[0]
        print(f"  Итого активных: {total}")

        # ── Проверки ─────────────────────────────────────────────────────────
        section("Проверки")

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
        checks["все Легко/Средняя = required"] = easy_req == easy_total

        cur.execute("""
            SELECT max(order_position) FROM tasks
            WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true
        """, (COURSE_ID, VVOD_PATTERN))
        vvod_max = cur.fetchone()[0]
        checks[f"вводные ≤ pos {len(vvod_ids)} (факт max={vvod_max})"] = (
            vvod_max is not None and vvod_max <= len(vvod_ids)
        )

        cur.execute("""
            SELECT order_position, count(*) FROM tasks
            WHERE course_id=%s AND is_active=true
            GROUP BY order_position HAVING count(*)>1
        """, (COURSE_ID,))
        dups = cur.fetchall()
        checks["нет дублей order_position"] = len(dups) == 0

        cur.execute("""
            SELECT difficulty_id, min(order_position) AS mn, max(order_position) AS mx
            FROM tasks
            WHERE course_id=%s AND is_active=true
              AND external_uid NOT ILIKE %s
            GROUP BY difficulty_id ORDER BY difficulty_id
        """, (COURSE_ID, VVOD_PATTERN))
        blocks = cur.fetchall()
        contiguous = (
            all(blocks[i][2] < blocks[i+1][1] for i in range(len(blocks)-1))
            if len(blocks) >= 2 else True
        )
        checks["блоки difficulty не пересекаются"] = contiguous

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
