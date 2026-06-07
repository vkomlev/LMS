# -*- coding: utf-8 -*-
"""Курс 148 (Задание 2) — патч: деактивация tg:ege:540.

Задание tg:ege:540 («Задание 2_3650 (Поляков). Внимание, задание некорректно!»)
не было деактивировано в предыдущем скрипте нормализации.
Остаётся активным с pos=45. Деактивируем.

После деактивации в курсе 148 появляется дырка на pos=45 — не критично,
позиции уникальны и относительный порядок не нарушен.

Задача: tsk-112
"""
import io, os, re, sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID = 148
TARGET_UID = 'tg:ege:540'
TARGET_ID  = 3312


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


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    checks: dict[str, bool] = {}

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        # ── Снимок ДО ────────────────────────────────────────────────────────
        cur.execute(
            "SELECT id, external_uid, is_active, order_position, difficulty_id "
            "FROM tasks WHERE course_id=%s AND external_uid=%s",
            (COURSE_ID, TARGET_UID),
        )
        row = cur.fetchone()
        print(f"ДО: id={row[0]} uid={row[1]} active={row[2]} pos={row[3]} diff={row[4]}")

        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        before_cnt = cur.fetchone()[0]
        print(f"ДО: итого активных = {before_cnt}")

        # ── Деактивация ───────────────────────────────────────────────────────
        cur.execute(
            "UPDATE tasks SET is_active=false WHERE id=%s AND course_id=%s",
            (TARGET_ID, COURSE_ID),
        )
        print(f"Деактивировано строк: {cur.rowcount}")

        # ── Снимок ПОСЛЕ ─────────────────────────────────────────────────────
        cur.execute(
            "SELECT count(*) FROM tasks WHERE course_id=%s AND is_active=true",
            (COURSE_ID,),
        )
        after_cnt = cur.fetchone()[0]
        print(f"ПОСЛЕ: итого активных = {after_cnt}")

        # ── Проверки ─────────────────────────────────────────────────────────
        checks["активных стало меньше на 1"] = after_cnt == before_cnt - 1

        cur.execute(
            "SELECT is_active FROM tasks WHERE id=%s",
            (TARGET_ID,),
        )
        checks["tg:ege:540 деактивирована"] = not cur.fetchone()[0]

        cur.execute(
            "SELECT order_position, count(*) FROM tasks "
            "WHERE course_id=%s AND is_active=true "
            "GROUP BY order_position HAVING count(*)>1",
            (COURSE_ID,),
        )
        checks["нет дублей order_position"] = len(cur.fetchall()) == 0

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
