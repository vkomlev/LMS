# -*- coding: utf-8 -*-
"""Курс 140 — исправление сложности ТГ-заданий + перемещение чужих задач.

Проблема: предыдущий скрипт выставил всем ТГ-заданиям difficulty=4 (Сложная).
Это неверно — сложность прописана прямо в stem каждого задания.

Также: два задания (tg:ege:260, tg:ege:220) — это разборы Задания 18,
они попали в курс 140 по ошибке. Перемещаем в курс 146.

Операции:
 1. Переместить tg:ege:260 и tg:ege:220 → course_id=146, pos=91-92, diff=3
 2. Исправить difficulty для 7 оставшихся ТГ-заданий по тексту stem:
      tg:ege:829, tg:ege:707 → 3 (Средняя)
      tg:ege:683             → 4 (Сложная, уже верно)
      tg:ege:523, 522, 507, 505 → 2 (Легко)
 3. Исправить requirement_level ТГ-заданий в курсе 140
 4. Переупорядочить курс 140 pos 11+:
      Легко(24) → pos 11-34, Средняя(15) → pos 35-49, Сложная(38) → pos 50-87

Итого заданий в курсе 140 после: 10 + 24 + 15 + 38 = 87.
Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_140 = 140
COURSE_146 = 146

DIFF_EASY   = 2
DIFF_NORMAL = 3
DIFF_HARD   = 4

REQ_REQUIRED    = 'required'
REQ_RECOMMENDED = 'recommended'

# Исправления сложности ТГ-заданий в курсе 140 (по тексту stem)
# external_uid → (difficulty_id, обоснование)
TG_DIFFICULTY = {
    'tg:ege:829': (DIFF_NORMAL, 'stem: «Уровень средний»'),
    'tg:ege:707': (DIFF_NORMAL, 'stem: «Уровень средний»'),
    'tg:ege:683': (DIFF_HARD,   'stem: «Уровень сложный»'),
    'tg:ege:523': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:522': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:507': (DIFF_EASY,   'stem: «Уровень легкий»'),
    'tg:ege:505': (DIFF_EASY,   'stem: «Уровень легкий»'),
}

# Задания-чужаки → перемещаем в курс 146
TG_MOVE_TO_146 = ['tg:ege:260', 'tg:ege:220']  # разборы Задания 18


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
        section("Снимок ДО (курс 140, pos 11+)")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, count(*) as cnt,
                   min(t.order_position) as min_pos, max(t.order_position) as max_pos
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s AND t.order_position>10
            GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
        """, (COURSE_140,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}): cnt={r[2]}, pos {r[3]}-{r[4]}")

        cur.execute("SELECT max(order_position) FROM tasks WHERE course_id=%s", (COURSE_146,))
        max_pos_146 = cur.fetchone()[0] or 0
        print(f"\n  Курс 146: max order_position = {max_pos_146}")

        # ── ШАГ 1: Перемещение двух заданий в курс 146 ───────────────────────
        section("ШАГ 1: Перемещение tg:ege:260 и tg:ege:220 → курс 146")
        moved = 0
        for i, uid in enumerate(TG_MOVE_TO_146, start=1):
            new_pos = max_pos_146 + i
            cur.execute("""
                UPDATE tasks
                SET course_id=%s, order_position=%s, difficulty_id=%s,
                    requirement_level=%s
                WHERE external_uid=%s AND course_id=%s
            """, (COURSE_146, new_pos, DIFF_NORMAL, REQ_REQUIRED, uid, COURSE_140))
            moved += cur.rowcount
            print(f"  {uid} → course_146 pos={new_pos}: {cur.rowcount} строк")

        checks["перемещено 2 задания в курс 146"] = moved == 2

        # ── ШАГ 2: Исправление difficulty ТГ-заданий в курсе 140 ─────────────
        section("ШАГ 2: Исправление difficulty ТГ-заданий")
        diff_fixed = 0
        for uid, (diff_id, reason) in TG_DIFFICULTY.items():
            cur.execute("""
                UPDATE tasks SET difficulty_id=%s
                WHERE external_uid=%s AND course_id=%s
            """, (diff_id, uid, COURSE_140))
            n = cur.rowcount
            diff_fixed += n
            print(f"  {uid} → diff={diff_id} ({reason}): {n} строк")

        checks["исправлено difficulty у 7 ТГ-заданий"] = diff_fixed == 7

        # ── ШАГ 3: requirement_level ТГ-заданий в курсе 140 ──────────────────
        section("ШАГ 3: requirement_level ТГ-заданий")

        # Легко → required
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'tg:ege:%%'
              AND difficulty_id=%s
        """, (REQ_REQUIRED, COURSE_140, DIFF_EASY))
        print(f"  ТГ Легко → required: {cur.rowcount}")

        # Средняя → required
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'tg:ege:%%'
              AND difficulty_id=%s
        """, (REQ_REQUIRED, COURSE_140, DIFF_NORMAL))
        print(f"  ТГ Средняя → required: {cur.rowcount}")

        # Сложная → recommended
        cur.execute("""
            UPDATE tasks SET requirement_level=%s
            WHERE course_id=%s AND external_uid ILIKE 'tg:ege:%%'
              AND difficulty_id=%s
        """, (REQ_RECOMMENDED, COURSE_140, DIFF_HARD))
        print(f"  ТГ Сложная → recommended: {cur.rowcount}")

        # ── ШАГ 4: Переупорядочивание курса 140 pos 11+ ──────────────────────
        section("ШАГ 4: Переупорядочивание курса 140")

        # Получаем все задания pos > 10 в нужном порядке (diff ASC, pos ASC)
        cur.execute("""
            SELECT id FROM tasks
            WHERE course_id=%s AND order_position>10
            ORDER BY difficulty_id ASC, order_position ASC
        """, (COURSE_140,))
        ordered_ids = [r[0] for r in cur.fetchall()]
        total = len(ordered_ids)
        print(f"  заданий для переупорядочивания: {total}")

        # Сдвигаем все в temp space
        cur.execute("""
            UPDATE tasks SET order_position = order_position + 2000
            WHERE course_id=%s AND order_position>10
        """, (COURSE_140,))
        print(f"  сдвинуто в temp space: {cur.rowcount}")

        # Назначаем новые позиции 11, 12, ..., 10+total
        for new_pos, task_id in enumerate(ordered_ids, start=11):
            cur.execute(
                "UPDATE tasks SET order_position=%s WHERE id=%s",
                (new_pos, task_id)
            )

        print(f"  назначено новых позиций: {total}")

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        section("Снимок ПОСЛЕ (курс 140)")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, t.requirement_level,
                   count(*) as cnt,
                   min(t.order_position) as min_pos, max(t.order_position) as max_pos
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s AND t.order_position>10
            GROUP BY t.difficulty_id, d.name_ru, t.requirement_level
            ORDER BY t.difficulty_id
        """, (COURSE_140,))
        rows_140 = cur.fetchall()
        for r in rows_140:
            print(f"  diff={r[0]}({r[1]}) req={r[2]}: cnt={r[3]}, pos {r[4]}-{r[5]}")

        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_140,))
        total_140 = cur.fetchone()[0]
        print(f"  Итого заданий в курсе 140: {total_140}")

        section("Снимок ПОСЛЕ (курс 146)")
        cur.execute("""
            SELECT t.difficulty_id, d.name_ru, count(*) as cnt,
                   min(t.order_position), max(t.order_position)
            FROM tasks t JOIN difficulties d ON d.id=t.difficulty_id
            WHERE t.course_id=%s
            GROUP BY t.difficulty_id, d.name_ru ORDER BY t.difficulty_id
        """, (COURSE_146,))
        for r in cur.fetchall():
            print(f"  diff={r[0]}({r[1]}): cnt={r[2]}, pos {r[3]}-{r[4]}")

        # ── Проверки дублей ───────────────────────────────────────────────────
        for cid in (COURSE_140, COURSE_146):
            cur.execute("""
                SELECT order_position, count(*) FROM tasks
                WHERE course_id=%s GROUP BY order_position HAVING count(*)>1
            """, (cid,))
            dups = cur.fetchall()
            checks[f"нет дублей order_position в курсе {cid}"] = len(dups) == 0
            if dups:
                print(f"\n  ВНИМАНИЕ дубли в курсе {cid}: {dups}")

        checks["итого заданий в курсе 140 = 87"] = total_140 == 87

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
