# -*- coding: utf-8 -*-
"""Курс 158 (Задание 7, Кодирование, передача информации) — нормализация.

== Особенность курса ==
30 вводных задания (lms:c158:vvod:01-30), сейчас в конце (pos 85-114).
Нужно переместить в начало (pos 1-30), non-vvod → 31+.

== Источник классификации ==
nav_parser на zadanie-7-ege-po-informatike-kodirovanie-razlichnyh-vidov-informatsii
+ stem markers TG

== Состав курса (114 активных) ==
  wp_nav:          28 (kompege=19, yandex=3, sdamgia=2, polyakov=4)
  vvod_c158:       30 (lms:c158:vvod:01-30, НЕ ТРОГАТЬ difficulty!)
  sdamgia_direct:  16
  crylov_new:      13 (pdf:d4:pdf:crylov:*)
  kompege_direct:  12
  crylov_ext:       7 (ext:pdf:d4:pdf:crylov:*)
  tg:               7
  polyakov_direct:  1

== Навигатор: разделы ==
Простые (16):
  - kompege_direct: 6, 354, 984, 23744, 21702, 20482, 20185 (7 шт.) → diff=2
  - wp_nav kompege: 51, 146, 166, 356, 23553, 23745, 19363, 19556 (8 шт.) → diff=2 (ИСКЛЮЧЕНИЕ)
  - sdamgia_direct: 26981 (1 шт.) → diff=2
Средние (21): уже correct diff=3 — НЕ ТРОГАТЬ
  - в т.ч. kompege:490, sdamgia:29194, sdamgia:64892, polyakov:8064
    присутствуют И в Средние, И в Сложные → консервативно остаётся diff=3
Сложные (23):
  - wp_nav: 19 уникальных заданий → diff=4
  - 4 задания из обеих секций (490, 29194, 64892, 8064) → остаются diff=3 (Средняя)

== TG (п.6а: все 7 имеют маркер в stem) ==
  Легкие (diff=2): tg:ege:958 («прост»), 858 («прост»), 652 («легк»), 519 («легк»), 472 («легк»)
  Средние (diff=3): tg:ege:659 («средн»), 658 («средн») — НЕ ТРОГАТЬ

== ШАГ 1: Материалы ==
  id=665 → recommended (нет иконки, «Решение заданий 7»)
  Остальные OK. id=437, 438 — уже inactive, не трогать.

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 158
VVOD_PATTERN = 'lms:c158:vvod:%'
VVOD_COUNT = 30  # позиции 1-30

# Материалы
MAT_UPDATES = {
    665: 'recommended',  # Решение заданий 7 (нет иконки)
}

# ШАГ 2Б-исключение: wp_nav kompege Простые → diff=2 (НЕ Сложная!)
WP_NAV_EASY_STIDS = {'51', '146', '166', '356', '23745', '23553', '19363', '19556'}

# ШАГ 2В: kompege_direct Простые → diff=2
KOMPEGE_EASY_UIDS = (
    'ext:d4:kompege:20260602:6',
    'ext:d4:kompege:20260602:354',
    'ext:d4:kompege:20260602:984',
    'ext:d4:kompege:20260602:23744',
    'ext:d4:kompege:20260602:21702',
    'ext:d4:kompege:20260602:20482',
    'ext:d4:kompege:20260602:20185',
)

# ШАГ 2Г: sdamgia_direct Простые → diff=2
SDAMGIA_EASY_UIDS = ('ext:d4:sdamgia:20260602:26981',)

# ШАГ 2Ж: TG Легкие → diff=2
TG_EASY_UIDS = ('tg:ege:958', 'tg:ege:858', 'tg:ege:652', 'tg:ege:519', 'tg:ege:472')

# НЕ МЕНЯТЬ (средние, консервативно):
# kompege:490 (обе секции), sdamgia:29194, sdamgia:64892, polyakov:8064 — stay diff=3


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

        cur.execute(
            "SELECT min(order_position), max(order_position) FROM tasks "
            "WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true",
            (COURSE_ID, VVOD_PATTERN),
        )
        vvod_pos = cur.fetchone()
        print(f"  Вводных позиции ДО: {vvod_pos[0]}-{vvod_pos[1]}")

        section("Снимок ДО — материалы")
        cur.execute(
            "SELECT id, requirement_level, is_active, external_uid "
            "FROM materials WHERE course_id=%s ORDER BY id",
            (COURSE_ID,),
        )
        for r in cur.fetchall():
            print(f"  id={r[0]} req={r[1]} ({'active' if r[2] else 'inactive'}) {r[3]}")

        # ── ШАГ 1: Материалы ─────────────────────────────────────────────────
        section("ШАГ 1: Материалы req_level")
        for mat_id, new_req in MAT_UPDATES.items():
            cur.execute(
                "UPDATE materials SET requirement_level=%s WHERE id=%s AND course_id=%s",
                (new_req, mat_id, COURSE_ID),
            )
            print(f"  id={mat_id} → {new_req}: {cur.rowcount} строк")

        # ── ШАГ 2: Difficulty ─────────────────────────────────────────────────
        section("ШАГ 2: Difficulty заданий")

        # А) wp_nav все → diff=4
        cur.execute("""
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%' AND is_active=true
        """, (COURSE_ID,))
        print(f"  wp_nav все → diff=4: {cur.rowcount}")

        # Б) wp_nav компеге Простые → diff=2 (8 исключений)
        for stid in WP_NAV_EASY_STIDS:
            cur.execute("""
                UPDATE tasks SET difficulty_id=2
                WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
                  AND (task_content->>'source_kind') = 'kompege'
                  AND (task_content->>'source_task_id') = %s
            """, (COURSE_ID, stid))
            print(f"  wp_nav kompege:{stid} → diff=2 (Простые): {cur.rowcount}")

        # В) kompege_direct Простые → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(KOMPEGE_EASY_UIDS)),
        )
        print(f"  kompege_direct Простые → diff=2: {cur.rowcount}")

        # Г) sdamgia_direct Простые → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(SDAMGIA_EASY_UIDS)),
        )
        print(f"  sdamgia_direct Простые (26981) → diff=2: {cur.rowcount}")

        # Д) Крылов все форматы → diff=2 (нет маркеров в stem)
        cur.execute("""
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'crylov:%%'
                OR external_uid ILIKE 'ext:pdf:d4:pdf:crylov:%%'
                OR external_uid ILIKE 'pdf:d4:pdf:crylov:%%'
              )
        """, (COURSE_ID,))
        print(f"  Крылов (все форматы) → diff=2: {cur.rowcount}")

        # Е) TG Легкие → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"  TG Легкие → diff=2: {cur.rowcount}")

        # NB: НЕ ТРОГАТЬ kompege_direct Средние, sdamgia Средние,
        #     polyakov:8064, tg:659/658 — уже diff=3, всё верно.
        # NB: компеге:490, sdamgia:29194/64892, polyakov:8064 в обеих секциях → remain diff=3.

        # ── ШАГ 3: req_level ─────────────────────────────────────────────────
        section("ШАГ 3: req_level заданий")

        cur.execute(
            "UPDATE tasks SET requirement_level='required' WHERE course_id=%s AND external_uid ILIKE %s",
            (COURSE_ID, VVOD_PATTERN),
        )
        print(f"  вводные (lms:c158:vvod:*) → required: {cur.rowcount}")

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
        section("ШАГ 4: Переупорядочивание (вводные → 1-30, non-vvod → 31+)")

        cur.execute(
            "SELECT id FROM tasks WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true "
            "ORDER BY order_position ASC",
            (COURSE_ID, VVOD_PATTERN),
        )
        vvod_ids = [r[0] for r in cur.fetchall()]
        print(f"  вводных: {len(vvod_ids)}")

        cur.execute(
            "SELECT id FROM tasks WHERE course_id=%s AND external_uid NOT ILIKE %s AND is_active=true "
            "ORDER BY difficulty_id ASC, order_position ASC",
            (COURSE_ID, VVOD_PATTERN),
        )
        non_vvod_ids = [r[0] for r in cur.fetchall()]
        print(f"  non-vvod active: {len(non_vvod_ids)}")

        # Сдвиг во временную зону
        cur.execute(
            "UPDATE tasks SET order_position = order_position + 2000 WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        # Вводные → pos 1-30
        for new_pos, task_id in enumerate(vvod_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))
        # Non-vvod → pos 31+
        for new_pos, task_id in enumerate(non_vvod_ids, start=VVOD_COUNT + 1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

        print(f"  позиции: вводные 1-{len(vvod_ids)}, non-vvod {VVOD_COUNT+1}-{VVOD_COUNT + len(non_vvod_ids)}")

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
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,)
        )
        total = cur.fetchone()[0]
        print(f"  Итого активных: {total}")

        # ── Проверки ─────────────────────────────────────────────────────────
        section("Проверки")

        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND difficulty_id=4 AND is_active=true",
            (COURSE_ID,),
        )
        hard_total = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND difficulty_id=4 AND is_active=true "
            "AND requirement_level='recommended'",
            (COURSE_ID,),
        )
        checks["все Сложные active = recommended"] = cur.fetchone()[0] == hard_total

        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND difficulty_id < 4 AND is_active=true",
            (COURSE_ID,),
        )
        easy_total = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND difficulty_id < 4 AND is_active=true "
            "AND requirement_level='required'",
            (COURSE_ID,),
        )
        checks["все Теория/Легко/Средняя = required"] = cur.fetchone()[0] == easy_total

        cur.execute(
            "SELECT max(order_position) FROM tasks WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true",
            (COURSE_ID, VVOD_PATTERN),
        )
        vvod_max = cur.fetchone()[0]
        checks[f"вводные ≤ pos {VVOD_COUNT} (факт max={vvod_max})"] = (
            vvod_max is not None and vvod_max <= VVOD_COUNT
        )

        cur.execute(
            "SELECT order_position, count(*) FROM tasks WHERE course_id=%s AND is_active=true "
            "GROUP BY order_position HAVING count(*)>1",
            (COURSE_ID,),
        )
        checks["нет дублей order_position"] = len(cur.fetchall()) == 0

        cur.execute("""
            SELECT difficulty_id, min(order_position) AS mn, max(order_position) AS mx
            FROM tasks WHERE course_id=%s AND is_active=true AND external_uid NOT ILIKE %s
            GROUP BY difficulty_id ORDER BY difficulty_id
        """, (COURSE_ID, VVOD_PATTERN))
        blocks = cur.fetchall()
        contiguous = (
            all(blocks[i][2] < blocks[i+1][1] for i in range(len(blocks)-1))
            if len(blocks) >= 2 else True
        )
        checks["блоки difficulty не пересекаются (non-vvod)"] = contiguous

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
