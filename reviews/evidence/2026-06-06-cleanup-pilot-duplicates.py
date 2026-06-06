# -*- coding: utf-8 -*-
"""Удаление pilot-дублей (группа 3) из базы LMS.

17 задач ext:polyakov:pilot:mini50 — дублируют canonical ext:d4:polyakov,
   стемы идентичны, ответ отсутствует или совпадает с d4.
2  задачи ext:d4:polyakov — курс 138 (poly 4406, 7613):
   pilot-версия лучше (имеет кликабельную ссылку на XLS-файл),
   поэтому оставляем pilot, удаляем d4.

После удаления — пересчёт order_position для каждого затронутого курса.
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── pilot:mini50 к удалению (стемы идентичны d4, d4 остаётся) ────────────────
DELETE_PILOT = [
    2929,  # course 139, poly 7017
    2930,  # course 139, poly 7048
    2933,  # course 140, poly 7442
    2060,  # course 142, poly 5918
    2061,  # course 145, poly 6757
    2062,  # course 146, poly 2380
    2063,  # course 147, poly 4109  (short_answer=null)
    2964,  # course 148, poly 7239
    2057,  # course 156, poly 5438  (short_answer=null)
    2971,  # course 156, poly 141
    2972,  # course 156, poly 1717
    2064,  # course 157, poly 6350  (short_answer=null)
    2974,  # course 158, poly 8064
    2986,  # course 160, poly 2807
    2942,  # course 162, poly 7854
    2943,  # course 162, poly 7857
    2944,  # course 162, poly 7926
]

# ── d4 к удалению (курс 138: pilot имеет ссылку на XLS, d4 — нет) ────────────
DELETE_D4_COURSE138 = [
    2071,  # course 138, ext:d4:polyakov:20260602:4406  (pilot id=2058 остаётся)
    2072,  # course 138, ext:d4:polyakov:20260602:7613  (pilot id=2059 остаётся)
]

DELETE_IDS = DELETE_PILOT + DELETE_D4_COURSE138

AFFECTED_COURSES = sorted({
    138, 139, 140, 142, 145, 146, 147, 148, 156, 157, 158, 160, 162,
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

        # Убедиться, что все ID реально есть
        cur.execute(
            "SELECT id, course_id, order_position, external_uid "
            "FROM tasks WHERE id = ANY(%s) ORDER BY course_id, order_position",
            (DELETE_IDS,),
        )
        found_rows = cur.fetchall()
        found_ids  = {r[0] for r in found_rows}
        missing    = set(DELETE_IDS) - found_ids

        print(f"Задач к удалению:  {len(DELETE_IDS)}")
        print(f"Найдено в БД:       {len(found_ids)}")
        if missing:
            print(f"ВНИМАНИЕ: не найдены ID: {sorted(missing)}")

        print("\nЗадачи к удалению:")
        for tid, cid, pos, uid in found_rows:
            tag = "(d4→pilot)" if tid in DELETE_D4_COURSE138 else "(pilot)"
            print(f"  id={tid:5d}  курс={cid:3d}  pos={pos:3d}  {uid}  {tag}")

        # ── удаление ──────────────────────────────────────────────────────────
        cur.execute("DELETE FROM tasks WHERE id = ANY(%s)", (DELETE_IDS,))
        deleted = cur.rowcount
        print(f"\nУдалено: {deleted}")

        # ── пересчёт order_position ───────────────────────────────────────────
        print("\nПересчёт позиций:")
        for cid in AFFECTED_COURSES:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY course_id ORDER BY order_position
                           ) AS new_pos
                    FROM tasks WHERE course_id = %s
                )
                UPDATE tasks t SET order_position = r.new_pos
                FROM ranked r
                WHERE t.id = r.id AND t.order_position != r.new_pos
                """,
                (cid,),
            )
            print(f"  курс {cid}: сдвинуто позиций = {cur.rowcount}")

        # ── снимок «после» ────────────────────────────────────────────────────
        cur.execute(
            "SELECT course_id, count(*), "
            "min(order_position), max(order_position), "
            "count(DISTINCT order_position) "
            "FROM tasks WHERE course_id = ANY(%s) "
            "GROUP BY course_id ORDER BY course_id",
            (AFFECTED_COURSES,),
        )
        after_rows = {r[0]: r for r in cur.fetchall()}

        # ── проверки ──────────────────────────────────────────────────────────
        checks = {}
        checks[f"удалено ровно {len(DELETE_IDS)}"] = (deleted == len(DELETE_IDS))
        checks["все ID найдены"]                    = (len(missing) == 0)

        print("\n── состояние по курсам ──────────────────────────────────────────")
        print(f"{'Курс':>5}  {'До':>5}  {'После':>5}  {'Δ':>3}  {'Позиции':}")
        for cid in AFFECTED_COURSES:
            b = before.get(cid, 0)
            if cid not in after_rows:
                print(f"  {cid:3d}  {b:5d}  {'—':>5}  (нет заданий)")
                continue
            _, cnt, pmin, pmax, pdist = after_rows[cid]
            delta = b - cnt
            ok = (pmin == 1 and pmax == cnt and pdist == cnt)
            print(f"  {cid:3d}  {b:5d}  {cnt:5d}  -{delta:<3d}  1..{pmax}  [{'OK' if ok else 'FAIL'}]")
            checks[f"курс {cid} непрерывен"] = ok

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
