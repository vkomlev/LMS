# -*- coding: utf-8 -*-
"""Курс 157 (Задание 6, Исполнитель Черепаха) — нормализация difficulty, req_level и порядка.

== Источник классификации ==
nav_parser на navigator-po-zadaniyu-6-ege + stem markers TG

== Состав курса (105 активных) ==
  wp_nav:        52 (polyakov=21, kompege=19, yandex=12)
  crylov_new:    14 (pdf:d4:pdf:crylov:*)
  vvod_c157:     10 (lms:c157:vvod:01-10)
  sdamgia:        9 (direct)
  crylov_ext:     6 (ext:pdf:d4:pdf:crylov:*)
  kompege:        6 (direct)
  tg:             5
  crylov_old:     2 (crylov:v5t6, v11t6)
  polyakov:       1 (direct, 6350)

== ШАГ 1: Материалы ==
  id=430 → skippable   («Подсчёт целочисленных точек: 2 подхода», 🔽)
  id=431 → skippable   («Применяем к задачам из урока», 🔽)
  id=432 → skippable   («О решении на PascalABC», 🔽)
  id=433 → skippable   («Чек-лист перед сдачей решения», 🔽)
  id=650 → recommended («Решение заданий 6», нет иконки)
  id=434, 652 — не найдены в навигаторе, оставляем required без изменений

== ШАГ 2: Difficulty ==
  А) wp_nav все → diff=4 Сложная
     ИСКЛЮЧЕНИЯ: kompege:4717, kompege:4752 → diff=2 Легко (Простые секция навигатора)
  Б) kompege_direct Простые (4694, 4742, 4747, 17860, 23190) → diff=2 Легко
  В) sdamgia Простые (75243) → diff=2 Легко
  Г) sdamgia Сложные (55593, 58248, 58249, 68239) → diff=4 Сложная
  Д) polyakov_direct (6350) → diff=4 Сложная
  Е) Крылов (все форматы) → diff=2 Легко
     NB: crylov:v5t6 и v11t6 оба имеют «Уровень простой» в stem — diff=2
  Ж) TG Легкие (471, 913, 957) → diff=2 Легко
  З) TG Сложная (558) → diff=4 Сложная
  И) Не трогать: вводные lms:c157:vvod:*, kompege:9987 (Средние), sdamgia Средние,
     TG Средняя (984)

== Вводные ==
  Паттерн: lms:c157:vvod:% (10 заданий, 01-10)
  diff=1: 01-03 (Теория), diff=2: 04-08 (Легко), diff=3: 09-10 (Средняя) — НЕ ТРОГАТЬ

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 157

# Материалы: id → нужное req_level
MAT_UPDATES = {
    430: 'skippable',    # Подсчёт целочисленных точек: 2 подхода (🔽)
    431: 'skippable',    # Применяем к задачам из урока (🔽)
    432: 'skippable',    # О решении на PascalABC (🔽)
    433: 'skippable',    # Чек-лист перед сдачей решения (🔽)
    650: 'recommended',  # Решение заданий 6 (нет иконки)
}

# ШАГ 2А-исключение: wp_nav Простые (НЕ Сложные!)
WP_NAV_EASY_STIDS = {'4717', '4752'}  # kompege source_task_id

# ШАГ 2Б: kompege_direct Простые → diff=2
KOMPEGE_EASY_UIDS = (
    'ext:d4:kompege:20260602:4694',
    'ext:d4:kompege:20260602:4742',
    'ext:d4:kompege:20260602:4747',
    'ext:d4:kompege:20260602:17860',
    'ext:d4:kompege:20260602:23190',
)

# ШАГ 2В: sdamgia Простые → diff=2
SDAMGIA_EASY_UIDS = ('ext:d4:sdamgia:20260602:75243',)

# ШАГ 2Г: sdamgia Сложные → diff=4
SDAMGIA_HARD_UIDS = (
    'ext:d4:sdamgia:20260602:55593',
    'ext:d4:sdamgia:20260602:58248',
    'ext:d4:sdamgia:20260602:58249',
    'ext:d4:sdamgia:20260602:68239',
)

# ШАГ 2Д: polyakov_direct Сложная → diff=4
POLYAKOV_HARD_UID = 'ext:d4:polyakov:20260602:6350'

# ШАГ 2Ж: TG Легкие → diff=2
TG_EASY_UIDS = ('tg:ege:471', 'tg:ege:913', 'tg:ege:957')

# ШАГ 2З: TG Сложная → diff=4
TG_HARD_UID = 'tg:ege:558'

# Вводные (course 157 использует lms:c157:vvod:*, НЕ lms:tsk109:*)
VVOD_PATTERN = 'lms:c157:vvod:%'


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

        # ── ШАГ 1: Материалы ─────────────────────────────────────────────────
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

        # А-исключения) wp_nav kompege:4717, 4752 → diff=2
        for stid in WP_NAV_EASY_STIDS:
            cur.execute("""
                UPDATE tasks SET difficulty_id=2
                WHERE course_id=%s AND external_uid ILIKE 'wp_nav:%%'
                  AND (task_content->>'source_kind') = 'kompege'
                  AND (task_content->>'source_task_id') = %s
            """, (COURSE_ID, stid))
            print(f"  wp_nav kompege:{stid} → diff=2 (Простые): {cur.rowcount}")

        # Б) kompege_direct Простые → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(KOMPEGE_EASY_UIDS)),
        )
        print(f"  kompege_direct Простые → diff=2: {cur.rowcount}")

        # В) sdamgia Простые → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(SDAMGIA_EASY_UIDS)),
        )
        print(f"  sdamgia Простые → diff=2: {cur.rowcount}")

        # Г) sdamgia Сложные → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(SDAMGIA_HARD_UIDS)),
        )
        print(f"  sdamgia Сложные → diff=4: {cur.rowcount}")

        # Д) polyakov_direct Сложная → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=%s AND is_active=true",
            (COURSE_ID, POLYAKOV_HARD_UID),
        )
        print(f"  polyakov_direct Сложная (6350) → diff=4: {cur.rowcount}")

        # Е) Крылов все форматы → diff=2 (оба crylov_old имеют «Уровень простой» в stem)
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

        # Ж) TG Легкие → diff=2
        cur.execute(
            "UPDATE tasks SET difficulty_id=2 WHERE course_id=%s AND external_uid=ANY(%s) AND is_active=true",
            (COURSE_ID, list(TG_EASY_UIDS)),
        )
        print(f"  TG Легкие → diff=2: {cur.rowcount}")

        # З) TG Сложная → diff=4
        cur.execute(
            "UPDATE tasks SET difficulty_id=4 WHERE course_id=%s AND external_uid=%s AND is_active=true",
            (COURSE_ID, TG_HARD_UID),
        )
        print(f"  TG Сложная (558) → diff=4: {cur.rowcount}")

        # ── ШАГ 3: req_level ─────────────────────────────────────────────────
        section("ШАГ 3: req_level заданий")

        cur.execute(
            "UPDATE tasks SET requirement_level='required' WHERE course_id=%s AND external_uid ILIKE %s",
            (COURSE_ID, VVOD_PATTERN),
        )
        print(f"  вводные (lms:c157:vvod:*) → required: {cur.rowcount}")

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
        for new_pos, task_id in enumerate(non_vvod_ids, start=11):
            cur.execute("UPDATE tasks SET order_position=%s WHERE id=%s", (new_pos, task_id))

        print(f"  позиции: вводные 1-{len(vvod_ids)}, non-vvod 11-{10 + len(non_vvod_ids)}")

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
        checks[f"вводные ≤ pos {len(vvod_ids)} (факт max={vvod_max})"] = (
            vvod_max is not None and vvod_max <= len(vvod_ids)
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
