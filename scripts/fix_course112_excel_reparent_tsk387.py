"""tsk-387 — перенести Excel-теорию (курсы 161, 164) из «Задание 9» (160)
в «Задание 3» (138) — первое место в курсе 112, где ученик встречает
электронные таблицы.

БЕЗОПАСНОСТЬ (проверено read-only через MCP learn_prod_db ДО написания
скрипта, 2026-08-02):
- `course_parents` — единственная таблица, которую трогает этот скрипт
  (обычный UPDATE двух строк parent_course_id 160->138). `materials`/`tasks`
  этих курсов НЕ трогаются — они остаются course_id=161/164, только меняется
  их место в дереве курса 112.
- compute_course_state считает total/done по МНОЖЕСТВУ материалов дерева
  (`course_id IN tree_ids`), порядок роли не играет — перенос НЕ меняет
  total_items/done_items ни для одного студента, откат COMPLETED->IN_PROGRESS
  физически невозможен (тем более: 0 COMPLETED у корня 112 на момент правки).
- `resolve_next_item` (tsk-261) ищет следующий элемент СТРОГО ВПЕРЁД от
  текущей позиции студента и НИКОГДА не тащит назад — пропуски позади
  студент добирает сам из списка «Разделы» (осознанный размен, решение
  оператора). Значит перенос НЕ может «откатить» или заблокировать прогресс:
  для 4 студентов (4507,4511,4512,4526), уже завершивших 161/164, ничего не
  меняется; для студентов, чья текущая позиция уже дальше нового места
  (order_number=4, курс 138) — эти материалы просто перестают быть
  auto-served и остаются доступны через список курса (то же самое поведение,
  что для любого другого пропущенного материала — не новый класс риска).
- `_collect_courses_in_order` — POST-ORDER обход: дети курса идут ПОЛНОСТЬЮ
  перед материалами/заданиями самого курса-контейнера. Значит 161/164 как
  дети 138 автоматически окажутся ПЕРЕД собственными материалами/заданиями
  138 («Что нужно знать и уметь» и т.д.) в обходе движка — «теория перед
  практикой» достигается структурой обхода, а не конкретным order_number.
- У курса 138 сейчас 0 детей (пусто) — INSERT/UPDATE на order_number=1,2 не
  вызовет сдвига посторонних строк (защитный assert на BEFORE это подтверждает).
- Внутренний порядок 161<->164 сохраняет уже принятое в tsk-530 правило
  «базовое понятие раньше специфичного инструмента»: 164 («Основы работы в
  электронных таблицах», базовый интерфейс) -> order_number=1,
  161 («Формулы и функции в Excel», специфичные формулы) -> order_number=2.

Запуск (на прод-сервере, sudo -u app, .env с прод DSN):
    python scripts/fix_course112_excel_reparent_tsk387.py              # dry-run
    python scripts/fix_course112_excel_reparent_tsk387.py --apply       # COMMIT
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

OLD_PARENT = 160  # Задание 9
NEW_PARENT = 138  # Задание 3
COURSE_BASICS = 164  # "Основы работы в электронных таблицах" -> первым
COURSE_FORMULAS = 161  # "Формулы и функции в Excel" -> вторым


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-387: перенос курсов 161/164 из {OLD_PARENT} в {NEW_PARENT} — {mode} ===")

    async with async_session_factory() as db:
        try:
            before = (await db.execute(text(
                "SELECT course_id, parent_course_id, order_number FROM course_parents "
                "WHERE course_id IN (161,164) ORDER BY course_id"
            ))).mappings().all()
            print(f"\nBEFORE: {[dict(r) for r in before]}")
            before_map = {r["course_id"]: (r["parent_course_id"], r["order_number"]) for r in before}
            assert before_map == {161: (160, 3), 164: (160, 2)}, (
                f"неожиданное исходное состояние: {before_map}"
            )

            existing_children_138 = (await db.execute(text(
                "SELECT count(*) FROM course_parents WHERE parent_course_id=:p"
            ), {"p": NEW_PARENT})).scalar()
            print(f"Текущих детей курса {NEW_PARENT}: {existing_children_138}")
            assert existing_children_138 == 0, (
                f"у курса {NEW_PARENT} уже есть дети ({existing_children_138}) — "
                "нужно вручную выбрать order_number, не бить готовый скрипт"
            )

            await db.execute(text(
                "UPDATE course_parents SET parent_course_id=:np, order_number=1 "
                "WHERE course_id=:c AND parent_course_id=:op"
            ), {"np": NEW_PARENT, "c": COURSE_BASICS, "op": OLD_PARENT})
            await db.execute(text(
                "UPDATE course_parents SET parent_course_id=:np, order_number=2 "
                "WHERE course_id=:c AND parent_course_id=:op"
            ), {"np": NEW_PARENT, "c": COURSE_FORMULAS, "op": OLD_PARENT})

            after = (await db.execute(text(
                "SELECT course_id, parent_course_id, order_number FROM course_parents "
                "WHERE course_id IN (161,164) ORDER BY course_id"
            ))).mappings().all()
            print(f"AFTER:  {[dict(r) for r in after]}")
            after_map = {r["course_id"]: (r["parent_course_id"], r["order_number"]) for r in after}
            assert after_map == {161: (138, 2), 164: (138, 1)}, (
                f"перенос не применился как ожидалось: {after_map}"
            )

            # Материалы/задания курсов 161/164 не должны были задеться.
            n_tasks = (await db.execute(text(
                "SELECT count(*) FROM tasks WHERE course_id IN (161,164)"
            ))).scalar()
            n_materials = (await db.execute(text(
                "SELECT count(*) FROM materials WHERE course_id IN (161,164)"
            ))).scalar()
            print(f"materials(161,164)={n_materials} tasks(161,164)={n_tasks} (должно остаться неизменным)")
            # tsk-387 (2026-07-23) писала "0 заданий" — устарело: 3 активных required
            # задания появились в курсе 161 позже (id 10010-10012, 0 task_results ни у
            # кого — проверено read-only перед записью). Не блокер, тот же класс
            # регресс-риска, что материалы: членство в дереве не зависит от порядка.
            assert n_tasks == 3, f"ожидали 3 задания (устаревшее число из tsk-387 было 0), получили {n_tasks}"
            assert n_materials == 28, f"ожидали 28 материалов (9+19), получили {n_materials}"

            # 112 всё ещё видит 161/164 в дереве (просто под другим родителем).
            reachable = (await db.execute(text("""
                WITH RECURSIVE tree AS (
                    SELECT course_id FROM course_parents WHERE parent_course_id = 112
                    UNION
                    SELECT cp.course_id FROM course_parents cp JOIN tree t ON cp.parent_course_id = t.course_id
                )
                SELECT course_id FROM tree WHERE course_id IN (161,164) ORDER BY course_id
            """))).scalars().all()
            print(f"161/164 всё ещё в дереве 112: {list(reachable)}")
            assert set(reachable) == {161, 164}, f"161/164 выпали из дерева 112: {reachable}"

        except Exception as exc:  # noqa: BLE001
            print(f"\nОШИБКА: {exc!r} — ROLLBACK")
            await db.rollback()
            return 1

        if apply:
            await db.commit()
            print("\nCOMMIT — перенос применён и закоммичен.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
