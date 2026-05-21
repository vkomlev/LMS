"""tsk-004 Этап 1.4 — миграция подкурсов «Python для ЕГЭ».

Курс верхнего уровня id=88 «Python для ЕГЭ» содержит 10 промежуточных
курсов («Тема N. <название>», ids 89, 94..102), каждый из которых имеет
ровно один листовой подкурс (ids 90,106,103,108,111,110,109,104,105,107).
Промежуточные курсы и листовые — это одна и та же тема в разных
формулировках, дублируют структуру.

После операции:
- Материалы (и tasks, если будут) из intermediate -> переходят в leaf
  в начало (order_position 1..N), существующие leaf-материалы
  сдвигаются на N позиций вправо.
- Курс 88 получает 10 leaf-курсов как прямых детей (с тем же
  order_number, что был у соответствующего intermediate).
- 10 intermediate-курсов удаляются полностью.

Запуск:
    python scripts/flatten_python_ege_subcourses.py            # dry-run
    python scripts/flatten_python_ege_subcourses.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

ROOT_COURSE_ID = 88

# (intermediate, leaf) — порядок строго соответствует Темам 1..10
PAIRS: list[tuple[int, int]] = [
    (89, 90),   # Тема 1. Установка Python  -> Как установить Python
    (94, 106),  # Тема 2. Программа          -> Первая программа на Python
    (95, 103),  # Тема 3. Числа              -> Числа в Python
    (96, 108),  # Тема 4. Строки             -> Работа со строками
    (97, 111),  # Тема 5. Условные           -> Условные конструкции
    (98, 110),  # Тема 6. Циклы              -> Циклы
    (99, 109),  # Тема 7. Списки             -> Списки (массивы)
    (100, 104), # Тема 8. Функции            -> Функции в Python
    (101, 105), # Тема 9. Множества          -> Использование множеств
    (102, 107), # Тема 10. Словари           -> Работа со словарями
]
INTERMEDIATE_IDS = [p[0] for p in PAIRS]
LEAF_IDS = [p[1] for p in PAIRS]


async def snapshot(db, label: str) -> dict[str, int]:
    """Считалка ключевых метрик: материалов в каждом курсе пары + хвост."""
    counts: dict[str, int] = {}
    for int_id, leaf_id in PAIRS:
        m_int = (await db.execute(
            text("SELECT COUNT(*) FROM materials WHERE course_id=:c"), {"c": int_id}
        )).scalar()
        m_leaf = (await db.execute(
            text("SELECT COUNT(*) FROM materials WHERE course_id=:c"), {"c": leaf_id}
        )).scalar()
        counts[f"{int_id}->mat"] = m_int
        counts[f"{leaf_id}->mat"] = m_leaf
    counts["intermediate_courses_alive"] = (await db.execute(
        text("SELECT COUNT(*) FROM courses WHERE id = ANY(:ids)"),
        {"ids": INTERMEDIATE_IDS},
    )).scalar()
    counts["children_of_root"] = (await db.execute(
        text("SELECT COUNT(*) FROM course_parents WHERE parent_course_id=:r"),
        {"r": ROOT_COURSE_ID},
    )).scalar()
    print(f"--- snapshot ({label}) ---")
    for k, v in counts.items():
        print(f"  {k:<35} {v}")
    return counts


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== Flatten Python для ЕГЭ subcourses — {mode} ===\n")

    async with async_session_factory() as db:
        before = await snapshot(db, "BEFORE")

        # 1. Сохранить order_number 88->intermediate
        order_rows = (await db.execute(
            text(
                "SELECT course_id, order_number FROM course_parents "
                "WHERE parent_course_id = :r AND course_id = ANY(:ids)"
            ),
            {"r": ROOT_COURSE_ID, "ids": INTERMEDIATE_IDS},
        )).fetchall()
        order_map = {r.course_id: r.order_number for r in order_rows}
        print(f"\nЗафиксированы order_number у 88->intermediate: {order_map}")

        # 2. Отключить triggers materials и course_parents
        for trigger in (
            ("materials", "trg_set_material_order_position"),
            ("materials", "trg_reorder_materials_after_delete"),
            ("course_parents", "trg_set_course_parent_order_number"),
            ("course_parents", "trg_reorder_course_parents_after_delete"),
        ):
            await db.execute(text(f"ALTER TABLE {trigger[0]} DISABLE TRIGGER {trigger[1]}"))

        try:
            # 3. Для каждой пары: сдвинуть существующие leaf-материалы на N и перенести intermediate
            for int_id, leaf_id in PAIRS:
                n_int = (await db.execute(
                    text("SELECT COUNT(*) FROM materials WHERE course_id=:c"),
                    {"c": int_id},
                )).scalar()
                if n_int == 0:
                    continue
                # Сдвинуть existing leaf-материалы: order_position += n_int
                await db.execute(
                    text(
                        "UPDATE materials SET order_position = order_position + :n "
                        "WHERE course_id = :c"
                    ),
                    {"n": int(n_int), "c": leaf_id},
                )
                # Перенести intermediate-материалы (сохраняя их order_position 1..n_int)
                await db.execute(
                    text("UPDATE materials SET course_id = :leaf WHERE course_id = :int"),
                    {"leaf": leaf_id, "int": int_id},
                )

            # 4. Перенести tasks на всякий случай (хотя intermediate task_count=0)
            for int_id, leaf_id in PAIRS:
                await db.execute(
                    text("UPDATE tasks SET course_id = :leaf WHERE course_id = :int"),
                    {"leaf": leaf_id, "int": int_id},
                )

            # 5. course_parents: убрать 88->intermediate и intermediate->leaf
            await db.execute(
                text(
                    "DELETE FROM course_parents "
                    "WHERE parent_course_id = :r AND course_id = ANY(:ids)"
                ),
                {"r": ROOT_COURSE_ID, "ids": INTERMEDIATE_IDS},
            )
            await db.execute(
                text("DELETE FROM course_parents WHERE parent_course_id = ANY(:ids)"),
                {"ids": INTERMEDIATE_IDS},
            )

            # 6. Связать 88->leaf с original order_number от intermediate
            for int_id, leaf_id in PAIRS:
                order_n = order_map.get(int_id)
                if order_n is None:
                    raise RuntimeError(f"Нет order_number для intermediate id={int_id}")
                await db.execute(
                    text(
                        "INSERT INTO course_parents "
                        "(parent_course_id, course_id, order_number) "
                        "VALUES (:p, :c, :o)"
                    ),
                    {"p": ROOT_COURSE_ID, "c": leaf_id, "o": int(order_n)},
                )

            # 7. Удалить intermediate-курсы (CASCADE снимет остатки course_parents — их уже нет)
            r_del = await db.execute(
                text("DELETE FROM courses WHERE id = ANY(:ids)"),
                {"ids": INTERMEDIATE_IDS},
            )
            print(f"\nDELETE courses (intermediate): rowcount={r_del.rowcount}")
        finally:
            # 8. Включить triggers обратно
            for trigger in (
                ("materials", "trg_set_material_order_position"),
                ("materials", "trg_reorder_materials_after_delete"),
                ("course_parents", "trg_set_course_parent_order_number"),
                ("course_parents", "trg_reorder_course_parents_after_delete"),
            ):
                await db.execute(text(f"ALTER TABLE {trigger[0]} ENABLE TRIGGER {trigger[1]}"))

        # 9. AFTER snapshot
        print()
        after = await snapshot(db, "AFTER")

        # 10. Sanity: что лежит под 88 теперь
        rows = (await db.execute(
            text(
                "SELECT cp.order_number, c.id, c.title "
                "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
                "WHERE cp.parent_course_id = :r ORDER BY cp.order_number"
            ),
            {"r": ROOT_COURSE_ID},
        )).fetchall()
        print("\n--- Дети курса 88 после операции ---")
        for r in rows:
            print(f"  order={r.order_number}  id={r.id:>3}  {r.title}")

        # 11. Проверка: intermediate-курсы удалены, leaf не имеют дыр в order
        orphan_int = (await db.execute(
            text("SELECT COUNT(*) FROM courses WHERE id = ANY(:ids)"),
            {"ids": INTERMEDIATE_IDS},
        )).scalar()
        leaf_total_mat = (await db.execute(
            text("SELECT COUNT(*) FROM materials WHERE course_id = ANY(:ids)"),
            {"ids": LEAF_IDS},
        )).scalar()
        print(f"\nintermediate курсов осталось: {orphan_int} (ожидаем 0)")
        print(f"всего материалов в leaf:       {leaf_total_mat}")

        # 12. Проверка консистентности порядка material order_position в каждом leaf
        gaps = (await db.execute(
            text(
                "WITH q AS ("
                "  SELECT course_id, order_position, "
                "  ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY order_position) AS rn "
                "  FROM materials WHERE course_id = ANY(:ids)"
                ") SELECT course_id, COUNT(*) FROM q "
                "WHERE order_position <> rn GROUP BY course_id"
            ),
            {"ids": LEAF_IDS},
        )).fetchall()
        if gaps:
            print("ВНИМАНИЕ: курсы с разрывами order_position:")
            for g in gaps:
                print(f"  course_id={g.course_id}, mismatch_count={g[1]}")
        else:
            print("order_position в leaf-курсах сплошной (1..N) OK")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения сохранены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT. Без флага — dry-run.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
