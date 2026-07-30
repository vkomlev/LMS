# -*- coding: utf-8 -*-
"""tsk-471: восстановить 5 вводных заданий курса 156 «Задание 5 ЕГЭ».

Контекст (полный разбор — reviews/2026-07-30-tsk471-vvod-tasks.md):
живая WP-страница перечисляет 8 «Вводных задач» (5_1..5_8), пронумерованных
по позиции <li> в списке. В курсе 156 (LMS):
  - 5_2 (id=4818), 5_3 (id=4819) — активны, без изменений.
  - 5_4 (id=4820), 5_5 (id=4821) — уже под верным external_uid,
    просто is_active=false.
  - 5_1 (id=3240), 5_6 (id=3451), 5_8 (id=3450) — контент цел, но их
    external_uid был кем-то переписан на чужие tg:ege:643/293/294
    и одновременно is_active=false, а difficulty_id стал 3 (NORMAL) —
    у братьев-сиблингов 5_2..5_5 difficulty_id=1. Ни один известный
    скрипт дедупа (tsk-350) их не трогал — источник рассинхронизации
    не найден, чинится restore, а не расследуется дальше.
  - 5_7 отсутствует полностью, ответа нет ни в БД, ни на сайте — не
    создаётся (решение оператора 2026-07-30).

Операция 1 — восстановление identity/активности/сложности (5 строк).
Операция 2 — группировка всех 7 вводных заданий в позиции 1..7 курса
(порядок 5_1,5_2,5_3,5_4,5_5,5_6,5_8), чтобы ученик видел их подряд,
а не вперемешку с банком ЕГЭ-заданий (текущие позиции: 1,2,3,4,53,63,65).
Триггер `trg_set_task_order_position` сам каскадно сдвигает соседей на
UPDATE — обрабатываем задания строго в целевом порядке (1..7), это
гарантирует корректный сдвиг независимо от исходной позиции (проверено
дословной симуляцией трёх кейсов до записи).
"""
import io
import os
import sys

import psycopg2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COURSE_ID = 156

# (id, целевой external_uid или None если уже верный, целевая позиция)
RESTORE = [
    (3240, "lms:c156:vvod:5_1", 1),
    (4818, None,                2),
    (4819, None,                3),
    (4820, None,                4),
    (4821, None,                5),
    (3451, "lms:c156:vvod:5_6", 6),
    (3450, "lms:c156:vvod:5_8", 7),
]


def load_dsn() -> str:
    dsn = os.environ.get("LMS_PROD_DSN")
    if dsn:
        return dsn
    raise RuntimeError("LMS_PROD_DSN не задан в окружении")


def dump_state(cur):
    cur.execute(
        "SELECT id, external_uid, is_active, difficulty_id, order_position "
        "FROM tasks WHERE id = ANY(%s) ORDER BY order_position",
        ([r[0] for r in RESTORE],),
    )
    return cur.fetchall()


def main() -> None:
    apply = "--apply" in sys.argv
    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("── до правки ──────────────────────────────────")
        for row in dump_state(cur):
            print(f"  {row}")

        # 1. identity + активность + сложность
        for task_id, new_uid, _pos in RESTORE:
            if new_uid is not None:
                cur.execute(
                    "UPDATE tasks SET external_uid=%s, is_active=true, difficulty_id=1 "
                    "WHERE id=%s",
                    (new_uid, task_id),
                )
            else:
                cur.execute("UPDATE tasks SET is_active=true WHERE id=%s", (task_id,))
            print(f"identity/активность: id={task_id} -> uid={new_uid or '(без изменений)'}, is_active=true ({cur.rowcount} строк)")

        # 2. позиции — строго в целевом порядке 1..7
        for task_id, _uid, pos in RESTORE:
            cur.execute(
                "UPDATE tasks SET order_position=%s WHERE id=%s AND course_id=%s",
                (pos, task_id, COURSE_ID),
            )
            print(f"позиция: id={task_id} -> order_position={pos} ({cur.rowcount} строк)")

        print("\n── после правки ────────────────────────────────")
        after = dump_state(cur)
        for row in after:
            print(f"  {row}")

        positions = [row[4] for row in after]
        active_flags = [row[2] for row in after]
        uids = [row[1] for row in after]
        expected_uids = {
            3240: "lms:c156:vvod:5_1", 4818: "lms:c156:vvod:5_2",
            4819: "lms:c156:vvod:5_3", 4820: "lms:c156:vvod:5_4",
            4821: "lms:c156:vvod:5_5", 3451: "lms:c156:vvod:5_6",
            3450: "lms:c156:vvod:5_8",
        }
        by_id = {row[0]: row for row in after}

        checks = {
            "все 7 активны": all(active_flags),
            "все difficulty_id=1": all(row[3] == 1 for row in after),
            "позиции = 1..7 без дублей": sorted(positions) == [1, 2, 3, 4, 5, 6, 7],
            "external_uid восстановлены верно": all(
                by_id[tid][1] == uid for tid, uid in expected_uids.items()
            ),
            "порядок позиций совпадает с 5_1..5_8": [
                by_id[tid][4] for tid, _u, _p in RESTORE
            ] == [1, 2, 3, 4, 5, 6, 7],
        }

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
