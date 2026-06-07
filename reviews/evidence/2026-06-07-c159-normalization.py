# -*- coding: utf-8 -*-
"""Курс 159 (Задание 8, Комбинаторика) — нормализация.

== Особенности курса ==
- 13 вводных (lms:c159:vvod:01-13), сейчас в конце (pos 146-158) → переместить в 1-13
- vvod:01-06 diff=2, vvod:07-11 diff=3, vvod:12-13 diff=4 — НЕ ТРОГАТЬ difficulty!
- crylov:v1t8 имеет «Уровень средний» в stem → остаётся diff=3 (НЕ diff=2)
- 3 TG без маркера уровня — вводные/вспомогательные артефакты → деактивировать
- nav_parser нашёл 0 материалов (нетипичная структура страницы) → материалы не трогаем

== Источник классификации ==
nav_parser на zadanie-8-ege-po-informatike-kombinatorika + stem markers TG

== Состав курса (158 активных → 155 после деактиваций) ==
  wp_nav:          96 (yandex=27, kompege=33, polyakov=36)
  vvod_c159:       13 (lms:c159:vvod:01-13)
  sdamgia_direct:  12
  crylov_new:      11 (pdf:d4:pdf:crylov:*)
  tg:              10 (7 с маркером + 3 деактивируем)
  crylov_ext:       9 (ext:pdf:d4:pdf:crylov:*)
  kompege_direct:   6
  crylov_old:       1 (crylov:v1t8 — «Уровень средний», diff=3!)

== Навигатор: разделы ==
Простые (6):
  - wp_nav kompege: 7, 52, 91, 195 (4 шт.) → diff=2 (ИСКЛЮЧЕНИЯ, источник Простые)
  - kompege_direct: 199, 265 (2 шт.) → diff=2
Средние (14): все уже correct diff=3 — НЕ ТРОГАТЬ
  - sdamgia: 69913, 3193, 59832, 15626, 40724, 13459, 69886, 76223, 76111, 68241, 26982 (11)
  - kompege: 19240, 21894, 21703 (3)
Сложные (67):
  - wp_nav kompege: 29 шт. → diff=4
  - wp_nav polyakov: 36 шт. → diff=4
  - kompege_direct: 11635 → diff=4
  - sdamgia_direct: 72566 → diff=4
  - 27 yandex wp_nav (не в nav) → diff=4 (treat как Сложные, wp_nav origin)

== TG (п.6а: 3 без маркера — деактивировать) ==
  Легкие (diff=2): tg:ege:529 («прост»), 526 («легк»), 521 («легк»), 484 («легк»)
  Средние (diff=3): tg:ege:914 («средн»), 497 («средн»), 496 («средн»)
  ДЕАКТИВИРОВАТЬ (вводн./вспом. без маркера):
    tg:ege:705 «Вводное задание 8_11» → соответствует vvod:11
    tg:ege:474 «Вспомогательное задание 8_6» → вспом. артефакт
    tg:ege:441 «Вспомогательное задание 8_9» → вспом. артефакт

== Материалы ==
  nav_parser нашёл 0 материалов (нетипичная структура nav-страницы курса 8).
  TODO: проверить иконки вручную и обновить req_level в отдельном патче.
  Текущее состояние: 7 активных материалов (id=439,440,441,653,654,655,656) — все required.
  2 неактивных (id=442,443) — оставляем as is.

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 159
VVOD_PATTERN = 'lms:c159:vvod:%'
VVOD_COUNT = 13  # позиции 1-13

# ШАГ 0: Деактивация вводных/вспомогательных TG без маркера уровня
DEACTIVATE_UIDS = ('tg:ege:705', 'tg:ege:474', 'tg:ege:441')

# ШАГ 2Б-исключения: wp_nav kompege Простые → diff=2
WP_NAV_EASY_STIDS = {'7', '52', '91', '195'}

# ШАГ 2В: kompege_direct Простые → diff=2
KOMPEGE_EASY_UIDS = (
    'ext:d4:kompege:20260602:199',
    'ext:d4:kompege:20260602:265',
)

# ШАГ 2Г: sdamgia_direct Сложные → diff=4
SDAMGIA_HARD_UIDS = ('ext:d4:sdamgia:20260602:72566',)

# ШАГ 2Д: kompege_direct Сложные → diff=4
KOMPEGE_HARD_UIDS = ('ext:d4:kompege:20260602:11635',)

# ШАГ 2Е: TG Легкие → diff=2
TG_EASY_UIDS = ('tg:ege:529', 'tg:ege:526', 'tg:ege:521', 'tg:ege:484')

# НЕ МЕНЯТЬ:
# crylov:v1t8 — «Уровень средний» в stem → diff=3 (не включаем в Крылов-апдейт)
# kompege_direct Средние (19240, 21894, 21703) — уже diff=3
# sdamgia_direct Средние (11 tasks) — уже diff=3
# TG Средние (914, 497, 496) — уже diff=3
# vvod:01-13 difficulty — НЕ ТРОГАТЬ


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
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true", (COURSE_ID,)
        )
        before_total = cur.fetchone()[0]
        print(f"  Итого активных ДО: {before_total}")

        cur.execute(
            "SELECT min(order_position), max(order_position) FROM tasks "
            "WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true",
            (COURSE_ID, VVOD_PATTERN),
        )
        vvod_pos = cur.fetchone()
        print(f"  Вводные позиции ДО: {vvod_pos[0]}-{vvod_pos[1]}")

        # ── ШАГ 0: Деактивация TG-артефактов ─────────────────────────────────
        section("ШАГ 0: Деактивация вводных/вспомогательных TG без маркера")
        for uid in DEACTIVATE_UIDS:
            cur.execute(
                "UPDATE tasks SET is_active=false WHERE course_id=%s AND external_uid=%s",
                (COURSE_ID, uid),
            )
            print(f"  {uid} → is_active=false: {cur.rowcount} строк")

        # ── ШАГ 1: Материалы ─────────────────────────────────────────────────
        section("ШАГ 1: Материалы (nav_parser нашёл 0 — не трогаем)")
        cur.execute(
            "SELECT count(*) FROM materials WHERE course_id=%s AND is_active=true "
            "AND requirement_level='required'",
            (COURSE_ID,),
        )
        print(f"  Текущих active required: {cur.fetchone()[0]} (TODO: проверить иконки вручную)")

        # ── ШАГ 2: Difficulty ─────────────────────────────────────────────────
        section("ШАГ 2: Difficulty заданий")

        # А) wp_nav все → diff=4
        cur.execute("""
            UPDATE tasks SET difficulty_id=4
            WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%' AND is_active=true
        """, (COURSE_ID,))
        print(f"  wp_nav все → diff=4: {cur.rowcount}")

        # Б) wp_nav kompege Простые → diff=2 (4 исключения)
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
        print(f"  kompege_direct Простые (199, 265) → diff=2: {cur.rowcount}")

        # Г) sdamgia_direct Сложные → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(SDAMGIA_HARD_UIDS)),
        )
        print(f"  sdamgia_direct Сложные (72566) → diff=4: {cur.rowcount}")

        # Д) kompege_direct Сложные → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(KOMPEGE_HARD_UIDS)),
        )
        print(f"  kompege_direct Сложные (11635) → diff=4: {cur.rowcount}")

        # Е) Крылов new+ext → diff=2 (crylov_old v1t8 НЕ ТРОГАЕМ — уже diff=3)
        cur.execute("""
            UPDATE tasks SET difficulty_id=2
            WHERE course_id=%s AND is_active=true
              AND (
                external_uid ILIKE 'ext:pdf:d4:pdf:crylov:%%'
                OR external_uid ILIKE 'pdf:d4:pdf:crylov:%%'
              )
        """, (COURSE_ID,))
        print(f"  Крылов new+ext → diff=2: {cur.rowcount}")
        # crylov:v1t8 (diff=3, «Уровень средний») — не тронут ✓

        # Ж) TG Легкие → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"  TG Легкие → diff=2: {cur.rowcount}")

        # NB: НЕ ТРОГАТЬ:
        # kompege_direct Средние (19240, 21894, 21703) — diff=3 OK
        # sdamgia_direct Средние (11 tasks) — diff=3 OK
        # TG Средние (914, 497, 496) — diff=3 OK

        # ── ШАГ 3: req_level ─────────────────────────────────────────────────
        section("ШАГ 3: req_level заданий")

        cur.execute(
            "UPDATE tasks SET requirement_level='required' WHERE course_id=%s AND external_uid ILIKE %s",
            (COURSE_ID, VVOD_PATTERN),
        )
        print(f"  вводные (lms:c159:vvod:*) → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level='required'
            WHERE course_id=%s AND difficulty_id < 4
              AND external_uid NOT ILIKE %s AND is_active=true
        """, (COURSE_ID, VVOD_PATTERN))
        print(f"  diff<4 non-vvod active → required: {cur.rowcount}")

        cur.execute("""
            UPDATE tasks SET requirement_level='recommended'
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
              AND external_uid NOT ILIKE %s
        """, (COURSE_ID, VVOD_PATTERN))
        print(f"  diff=4 non-vvod Сложная → recommended: {cur.rowcount}")

        # vvod:12, 13 (diff=4) → required (они вводные, не non-vvod)
        cur.execute(
            "UPDATE tasks SET requirement_level='required' WHERE course_id=%s AND external_uid ILIKE %s",
            (COURSE_ID, VVOD_PATTERN),
        )
        print(f"  вводные (повтор — зафиксировать required): {cur.rowcount}")

        # ── ШАГ 4: Переупорядочивание ────────────────────────────────────────
        section(f"ШАГ 4: Переупорядочивание (вводные 1-{VVOD_COUNT}, non-vvod {VVOD_COUNT+1}+)")

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

        cur.execute(
            "UPDATE tasks SET order_position = order_position + 2000 WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        for new_pos, task_id in enumerate(vvod_ids, start=1):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))
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

        # 1. Сложная non-vvod = recommended
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
              AND external_uid NOT ILIKE %s
        """, (COURSE_ID, VVOD_PATTERN))
        hard_non_vvod = cur.fetchone()[0]
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND difficulty_id=4 AND is_active=true
              AND external_uid NOT ILIKE %s AND requirement_level='recommended'
        """, (COURSE_ID, VVOD_PATTERN))
        checks["все Сложные non-vvod = recommended"] = cur.fetchone()[0] == hard_non_vvod

        # 2. diff<4 = required
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

        # 3. vvod в pos 1-VVOD_COUNT
        cur.execute(
            "SELECT max(order_position) FROM tasks WHERE course_id=%s AND external_uid ILIKE %s AND is_active=true",
            (COURSE_ID, VVOD_PATTERN),
        )
        vvod_max = cur.fetchone()[0]
        checks[f"вводные ≤ pos {VVOD_COUNT} (факт max={vvod_max})"] = (
            vvod_max is not None and vvod_max <= VVOD_COUNT
        )

        # 4. vvod:12,13 (diff=4) required
        cur.execute("""
            SELECT count(*) FROM tasks
            WHERE course_id=%s AND external_uid IN ('lms:c159:vvod:12','lms:c159:vvod:13')
              AND requirement_level='required'
        """, (COURSE_ID,))
        checks["вводные vvod:12/13 (diff=4) = required"] = cur.fetchone()[0] == 2

        # 5. Нет дублей позиций
        cur.execute(
            "SELECT order_position, count(*) FROM tasks WHERE course_id=%s AND is_active=true "
            "GROUP BY order_position HAVING count(*)>1",
            (COURSE_ID,),
        )
        checks["нет дублей order_position"] = len(cur.fetchall()) == 0

        # 6. Блоки non-vvod не пересекаются
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

        # 7. Деактивированные TG
        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=false",
            (COURSE_ID, list(DEACTIVATE_UIDS)),
        )
        checks[f"деактивированы {len(DEACTIVATE_UIDS)} TG-артефакта"] = cur.fetchone()[0] == len(DEACTIVATE_UIDS)

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
