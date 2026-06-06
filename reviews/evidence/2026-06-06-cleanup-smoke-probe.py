# -*- coding: utf-8 -*-
"""Удаление smoke-артефактов и probe-заглушки из базы LMS.

Группа 1: 19 задач pdf:smoke:pdf:crylov:v1:20260525-fix:v1tXX (OCR-мусор, 14 курсов)
Группа 2:  1 задача ext:polyakov:pilot:probe:test (id=1480, курс 162)

После удаления — пересчёт order_position (ROW_NUMBER) для каждого затронутого курса,
чтобы позиции были непрерывны 1..N.
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── список ID к удалению ──────────────────────────────────────────────────────

DELETE_IDS = [
    # Группа 1 — smoke pdf:smoke:pdf:crylov:v1:20260525-fix
    2928,  # course 138, pos 53
    2931,  # course 139, pos 81
    2932,  # course 140, pos 43
    2934,  # course 141, pos 26
    2957,  # course 142, pos 72
    2958,  # course 143, pos 34
    2959,  # course 144, pos 36
    2960,  # course 145, pos 35
    2961,  # course 146, pos 37
    2962,  # course 147, pos 72
    2963,  # course 147, pos 73
    2965,  # course 149, pos 34
    2966,  # course 152, pos 46
    2967,  # course 155, pos 31
    2973,  # course 157, pos 48
    2975,  # course 158, pos 81
    2987,  # course 160, pos 60
    2945,  # course 162, pos 67
    2946,  # course 163, pos 26
    # Группа 2 — probe:test
    1480,  # course 162, pos 24  "<p>probe</p>"
]

AFFECTED_COURSES = sorted({
    138, 139, 140, 141, 142, 143, 144, 145, 146,
    147, 149, 152, 155, 157, 158, 160, 162, 163,
})

# ─────────────────────────────────────────────────────────────────────────────

def load_dsn() -> str:
    dsn = os.environ.get("LMS_DB_DSN")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    url = None
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise RuntimeError("DATABASE_URL не найден в .env")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


def main() -> None:
    apply = "--apply" in sys.argv

    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        # ── снимок «до» ───────────────────────────────────────────────────────
        cur.execute(
            "SELECT course_id, count(*) FROM tasks "
            "WHERE course_id = ANY(%s) GROUP BY course_id ORDER BY course_id",
            (AFFECTED_COURSES,),
        )
        before = dict(cur.fetchall())

        # Убедиться, что все ID к удалению реально есть
        cur.execute(
            "SELECT id, course_id, order_position, external_uid "
            "FROM tasks WHERE id = ANY(%s) ORDER BY course_id, order_position",
            (DELETE_IDS,),
        )
        found_rows = cur.fetchall()
        found_ids = {r[0] for r in found_rows}
        missing = set(DELETE_IDS) - found_ids

        print(f"Задач к удалению:  {len(DELETE_IDS)}")
        print(f"Найдено в БД:       {len(found_ids)}")
        if missing:
            print(f"ВНИМАНИЕ: не найдены ID: {sorted(missing)}")

        print("\nЗадачи к удалению:")
        for tid, cid, pos, uid in found_rows:
            print(f"  id={tid:5d}  курс={cid:3d}  pos={pos:3d}  {uid}")

        # ── удаление ──────────────────────────────────────────────────────────
        cur.execute("DELETE FROM tasks WHERE id = ANY(%s)", (DELETE_IDS,))
        deleted = cur.rowcount
        print(f"\nУдалено: {deleted}")

        # ── пересчёт order_position для каждого курса ────────────────────────
        print("\nПересчёт позиций:")
        for cid in AFFECTED_COURSES:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY course_id ORDER BY order_position
                           ) AS new_pos
                    FROM tasks
                    WHERE course_id = %s
                )
                UPDATE tasks t
                SET order_position = r.new_pos
                FROM ranked r
                WHERE t.id = r.id
                  AND t.order_position != r.new_pos
                """,
                (cid,),
            )
            updated = cur.rowcount
            print(f"  курс {cid}: сдвинуто позиций = {updated}")

        # ── снимок «после» ────────────────────────────────────────────────────
        cur.execute(
            "SELECT course_id, count(*), "
            "min(order_position), max(order_position), "
            "count(DISTINCT order_position) "
            "FROM tasks WHERE course_id = ANY(%s) "
            "GROUP BY course_id ORDER BY course_id",
            (AFFECTED_COURSES,),
        )
        after_rows = cur.fetchall()
        after = {r[0]: r for r in after_rows}

        # ── проверки ──────────────────────────────────────────────────────────
        checks = {}
        checks["удалено ровно 20"] = (deleted == len(DELETE_IDS))
        checks["все ID найдены"] = (len(missing) == 0)

        print("\n── состояние по курсам ──────────────────────────────────────────")
        print(f"{'Курс':>5}  {'До':>5}  {'После':>5}  {'Δ':>3}  {'Позиции':}")
        for cid in AFFECTED_COURSES:
            b = before.get(cid, 0)
            if cid not in after:
                print(f"  {cid:3d}  {b:5d}  {'—':>5}  (нет заданий)")
                continue
            _, cnt, pmin, pmax, pdist = after[cid]
            delta = b - cnt
            contiguous = (pmin == 1 and pmax == cnt and pdist == cnt)
            status = "OK" if contiguous else "FAIL"
            print(f"  {cid:3d}  {b:5d}  {cnt:5d}  -{delta:<3d}  1..{pmax}  [{status}]")
            checks[f"курс {cid} непрерывен"] = contiguous

        # ── итог проверок ─────────────────────────────────────────────────────
        print("\n── проверки ─────────────────────────────────────────────────────")
        all_ok = True
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")
            if not ok:
                all_ok = False

        if all_ok and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
        elif all_ok:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN пройден, ROLLBACK. Запусти с --apply.")
        else:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
            sys.exit(1)

    except Exception as exc:
        conn.rollback()
        print(f"ОШИБКА: {exc!r}. ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
