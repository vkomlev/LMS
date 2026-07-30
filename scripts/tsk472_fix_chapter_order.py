# -*- coding: utf-8 -*-
"""tsk-472: восстановить порядок глав курсов 826 и 1283.

Корень (см. коммит tsk-237, 2026-07-17): course_parents.order_number ставится
триггером по моменту публикации (max+1), поэтому раздел, опубликованный позже,
уезжает в конец. Курс 826 (Информатика 7 класс) и 1283 (Тестировщик ПО) —
два независимых случая одного класса (найдено системным SQL-сканом course_uid
с суффиксом '-g<N>' по всей системе, других экземпляров не найдено).

Триггер `trg_set_course_parent_order_number` на UPDATE сам каскадно
пересчитывает order_number соседей (см. definition set_course_parent_order_number) —
скрипту достаточно одного явного UPDATE на строку, каскад делает БД.

  Курс 826, Глава 4 (course_id=853): order_number 8 -> 4
    (Глава 5 автоматически уедет с 5 на 6 — расположение не нарушится:
     1,2,3,4,6 — по-прежнему монотонно и совпадает с номерами глав)

  Курс 1283, Глава 7 (course_id=1326): order_number 18 -> 8
    (Главы 8..17 автоматически сдвинутся на +1, освобождая слот 8)
"""
import io
import os
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

FIXES = [
    # (parent_course_id, course_id, chapter_label, new_order_number)
    (826, 853, "Глава 4 (Информатика 7 класс)", 4),
    (1283, 1326, "Глава 7 (Тестировщик ПО)", 8),
]


def load_dsn() -> str:
    dsn = os.environ.get("LMS_PROD_DSN")
    if dsn:
        return dsn
    raise RuntimeError("LMS_PROD_DSN не задан в окружении")


def dump_order(cur, parent_id):
    cur.execute(
        "SELECT cp.course_id, c.title, cp.order_number "
        "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
        "WHERE cp.parent_course_id = %s ORDER BY cp.order_number",
        (parent_id,),
    )
    return cur.fetchall()


def is_monotonic_by_glava(rows) -> bool:
    import re

    nums = []
    for _cid, title, _pos in rows:
        m = re.search(r"Глава\s+(\d+)", title or "")
        if m:
            nums.append(int(m.group(1)))
    return nums == sorted(nums)


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("── до правки ──────────────────────────────────")
        before = {}
        for parent_id, course_id, label, new_pos in FIXES:
            rows = dump_order(cur, parent_id)
            before[parent_id] = rows
            print(f"\nparent={parent_id} ({label}):")
            for cid, title, pos in rows:
                marker = " <-- будет исправлено" if cid == course_id else ""
                print(f"  pos={pos:<4} course_id={cid:<6} {title}{marker}")

        for parent_id, course_id, label, new_pos in FIXES:
            cur.execute(
                "UPDATE course_parents SET order_number = %s "
                "WHERE parent_course_id = %s AND course_id = %s",
                (new_pos, parent_id, course_id),
            )
            print(f"\nОбновлено: parent={parent_id} course_id={course_id} -> order_number={new_pos} ({cur.rowcount} строк)")

        print("\n── после правки ────────────────────────────────")
        checks = {}
        for parent_id, course_id, label, new_pos in FIXES:
            rows = dump_order(cur, parent_id)
            print(f"\nparent={parent_id} ({label}):")
            for cid, title, pos in rows:
                print(f"  pos={pos:<4} course_id={cid:<6} {title}")

            ok_monotonic = is_monotonic_by_glava(rows)
            ok_distinct = len({pos for _c, _t, pos in rows}) == len(rows)
            ok_count = len(rows) == len(before[parent_id])
            checks[f"parent {parent_id}: главы по возрастанию номера"] = ok_monotonic
            checks[f"parent {parent_id}: order_number без дублей"] = ok_distinct
            checks[f"parent {parent_id}: число детей не изменилось ({len(rows)})"] = ok_count

        print("\n── проверки ────────────────────────────────────")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")

        if all(checks.values()) and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
        elif all(checks.values()):
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
