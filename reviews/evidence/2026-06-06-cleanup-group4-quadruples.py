# -*- coding: utf-8 -*-
"""Удаление оставшихся дублей группы 4 (тройные копии в курсах 138, 156, 162).

Стратегия:
  Курс 138 / poly 4406:
    Оставить pilot:mini50 (id=2058) — имеет кликабельную XLS-ссылку.
    Удалить ext:polyakov (id=1479) и pilot:20260524 (id=2927).

  Курс 156 / poly 141, 1717, 5438:
    Оставить ext:d4 (ids 2070, 2068, 2069) — canonical.
    Удалить ext:polyakov (ids 1478, 1476, 1477)
         и pilot:20260524 (ids 2968, 2969, 2970).
    Примечание: pilot:20260524 для 1717 и 5438 — ответ null (неполные).

  Курс 162 / poly 7854, 7857, 7926:
    Оставить ext:d4 (ids 2065, 2067, 2066) — canonical.
    Удалить ext:polyakov (ids 1473, 1475, 1474)
         и pilot:20260524 (ids 2939, 2940, 2941).

После удаления — пересчёт order_position для курсов 138, 156, 162.
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── список ID к удалению ────────────────��─────────────────────────────────────

DELETE_IDS = [
    # Курс 138, poly 4406 — убираем старый и второй пилот, оставляем pilot:mini50 (2058)
    1479,   # ext:polyakov:4406           pos=11
    2927,   # ext:polyakov:pilot:20260524:4406  pos=50

    # Курс 156, poly 141
    1478,   # ext:polyakov:141            pos=3
    2968,   # ext:polyakov:pilot:20260524:141   pos=48
    # Курс 156, poly 1717
    1476,   # ext:polyakov:1717           pos=1
    2969,   # ext:polyakov:pilot:20260524:1717  pos=49  (answer=null)
    # Курс 156, poly 5438
    1477,   # ext:polyakov:5438           pos=2
    2970,   # ext:polyakov:pilot:20260524:5438  pos=50  (answer=null)

    # Курс 162, poly 7854
    1473,   # ext:polyakov:7854           pos=21
    2939,   # ext:polyakov:pilot:20260524:7854  pos=60
    # Курс 162, poly 7857
    1475,   # ext:polyakov:7857           pos=23
    2940,   # ext:polyakov:pilot:20260524:7857  pos=61
    # Курс 162, poly 7926
    1474,   # ext:polyakov:7926           pos=22
    2941,   # ext:polyakov:pilot:20260524:7926  pos=62
]

AFFECTED_COURSES = [138, 156, 162]

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

        # ── снимок «до» ──────��────────────────────────────────────────────────
        cur.execute(
            "SELECT course_id, count(*) FROM tasks "
            "WHERE course_id = ANY(%s) GROUP BY course_id ORDER BY course_id",
            (AFFECTED_COURSES,),
        )
        before = dict(cur.fetchall())

        # Проверить, что все ID есть, и вывести что именно удаляем
        cur.execute(
            "SELECT id, course_id, order_position, external_uid, "
            "(solution_rules->'short_answer') IS NOT NULL "
            "AND (solution_rules->'short_answer') != 'null'::jsonb AS has_answer "
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
        for tid, cid, pos, uid, has_ans in found_rows:
            ans_flag = "" if has_ans else "  ← answer=null"
            print(f"  id={tid:5d}  курс={cid:3d}  pos={pos:3d}  {uid}{ans_flag}")

        # Показать, какие задачи ОСТАЮТСЯ по каждому poly_id
        KEEP_NOTES = {
            138: "оставляем pilot:mini50 id=2058 (XLS-ссылка)",
            156: "оставляем ext:d4 (ids 2068, 2069, 2070)",
            162: "оставляем ext:d4 (ids 2065, 2066, 2067)",
        }
        print("\nЧто остаётся:")
        for cid, note in KEEP_NOTES.items():
            print(f"  курс {cid}: {note}")

        # ── удаление ────���─────────────────────────────────────────────────────
        cur.execute("DELETE FROM tasks WHERE id = ANY(%s)", (DELETE_IDS,))
        deleted = cur.rowcount
        print(f"\nУдалено: {deleted}")

        # ── пересчёт order_position ──────��────────────────────────────────────
        print("\nПересчёт позиц��й:")
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

        # ── снимок «после» ───────���────────────────────────────────────────────
        cur.execute(
            "SELECT course_id, count(*), "
            "min(order_position), max(order_position), "
            "count(DISTINCT order_position) "
            "FROM tasks WHERE course_id = ANY(%s) "
            "GROUP BY course_id ORDER BY course_id",
            (AFFECTED_COURSES,),
        )
        after_rows = {r[0]: r for r in cur.fetchall()}

        # ── проверки ���─────────────────────────────��───────────────────────────
        checks = {}
        checks[f"удалено ровно {len(DELETE_IDS)}"] = (deleted == len(DELETE_IDS))
        checks["все ID найдены"]                    = (len(missing) == 0)

        print("\n── состояние по курсам ──────────────────────────────────────────")
        print(f"{'Курс':>5}  {'До':>5}  {'После':>5}  {'Δ':>3}  {'Позиции':}")
        for cid in AFFECTED_COURSES:
            b = before.get(cid, 0)
            _, cnt, pmin, pmax, pdist = after_rows[cid]
            delta = b - cnt
            ok = (pmin == 1 and pmax == cnt and pdist == cnt)
            print(f"  {cid:3d}  {b:5d}  {cnt:5d}  -{delta:<3d}  1..{pmax}  [{'OK' if ok else 'FAIL'}]")
            checks[f"курс {cid} непрерывен"] = ok

        print("\n── проверки ───────���─────────────────────────────────────────────")
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
            print("\nРЕЗУЛЬТАТ: проверки НЕ пройд��ны, ROLLBACK.")
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
