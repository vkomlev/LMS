# -*- coding: utf-8 -*-
"""Нормализация task_content.title: пустая строка "" -> JSON null.

Причина: D4-конвейер ContentBackbone (kompege/yandex/polyakov/sdamgia) писал
title="", из-за чего фронт SPW (tc.title ?? "Задача #N") не подставлял подпись.
Приводим к единому виду: "" -> null, как у остальных заданий.
"""
import io
import os
import sys
import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
# Строка подключения — из env (LMS_DB_DSN), без секретов в коде.
# Локально: задать LMS_DB_DSN или положиться на PGPASSWORD/.pgpass.
DSN = os.environ.get("LMS_DB_DSN", "host=localhost port=5432 dbname=Learn user=postgres")


def main() -> None:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        cur.execute("SELECT count(*) FROM tasks WHERE task_content->>'title' = ''")
        before_empty = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM tasks "
                    "WHERE jsonb_typeof(task_content->'title') = 'null'")
        before_null = cur.fetchone()[0]

        cur.execute(
            "UPDATE tasks SET task_content = jsonb_set(task_content, '{title}', "
            "'null'::jsonb) WHERE task_content->>'title' = ''")
        updated = cur.rowcount

        cur.execute("SELECT count(*) FROM tasks WHERE task_content->>'title' = ''")
        after_empty = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM tasks "
                    "WHERE jsonb_typeof(task_content->'title') = 'null'")
        after_null = cur.fetchone()[0]

        print(f"пустых '' до/после: {before_empty}/{after_empty}")
        print(f"json null до/после: {before_null}/{after_null}")
        print(f"обновлено строк: {updated}")

        ok = (after_empty == 0 and updated == before_empty
              and after_null == before_null + before_empty)
        if ok:
            conn.commit()
            print("РЕЗУЛЬТАТ: проверки пройдены, COMMIT.")
        else:
            conn.rollback()
            print("РЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"ОШИБКА: {exc!r}. ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
