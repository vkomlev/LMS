"""tsk-530 — точечные структурные правки курса 112 (ЕГЭ по информатике) по
итогам ревью tsk-425/tsk-528.

ЧТО ДЕЛАЕТ (5 независимых шагов, каждый — свой блок read->plan->update->verify
внутри ОДНОЙ транзакции; при --apply коммит только если ВСЕ 5 шагов прошли
верификацию, иначе полный ROLLBACK):

1. course_parents: свап order_number подтем курса 160 (Excel) — базовый
   интерфейс (164) должен идти раньше формул (161).
2. materials курса 148 (Задание 2): свап order_position понятия (395,
   "Таблица истинности") и материала-инструмента (397, "Python-построение") —
   понятие должно идти первым.
3. materials курсов 142 (Задание 14) и 143 (Задание 15): в обоих узлах
   базовое видео должно идти раньше текстового "Разбора" — свап 379<->565
   (курс 142) и 383<->578 (курс 143).
4. materials: requirement_level='recommended' для 4 материалов Turtle-курса
   165 (313,315,316,317 — цвет/заливка/импорт/анимация, не отрабатываются
   заданиями задания 6) и материала 355 курса 139 (Python ipaddress-API).
5. materials 408 (курс 151, "Задание 24") и 410 (курс 152, "Задание 25"):
   is_active=false -> true (подлинно ценный контент, ранее скрытый).

БЕЗОПАСНОСТЬ ЗАПИСИ (проверено read-only через MCP learn_prod_db ДО написания
скрипта, 2026-08-02):
- Свапы order_number/order_position используют штатные БД-триггеры
  (`set_course_parent_order_number`, `set_material_order_position`) —
  один UPDATE на более позднюю позицию автоматически каскадно сдвигает
  соседнюю запись, эквивалентно ручному drag-and-drop реордеру в API.
  Порядок НЕ входит в фильтр compute_course_state (is_active +
  requirement_level IN required/skippable) — переставить местами уже
  пройденные учеником материалы невозможно откатить назад, меняется только
  последовательность показа следующего незавершённого элемента.
- П.4 (required -> recommended) исключает материалы из знаменателя
  compute_course_state (total_items) — может только УЛУЧШИТЬ отношение
  done/total, откат прогресса невозможен по конструкции.
- П.5 (is_active false -> true, requirement_level остаётся required)
  теоретически мог откатить прогресс (тот же паттерн, что и инцидент
  tsk-524) — проверено ДО этого скрипта: 0 записей task_results/
  student_material_progress по курсам 151/152/153/154 у ЛЮБОГО студента
  (ни один ученик ещё не дошёл до этой части курса) -> обходной путь
  manual_progress_service.grant_course_subtree НЕ требуется.
- П.6 декомпозиции tsk-530 (is_active для самопроверочных материалов
  пакета 3) НЕ входит в этот скрипт: tsk-528 п.2 установил, что отключение
  было осознанным решением методиста, а не техническим сбоем — условие
  выполнения п.6 не выполнено, пункт пропущен по декомпозиции задачи.

Запуск (на прод-сервере, sudo -u app, .env с прод DSN):
    python scripts/fix_course112_structure_tsk530.py              # dry-run
    python scripts/fix_course112_structure_tsk530.py --apply       # COMMIT
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

TURTLE_RECOMMENDED_IDS = (313, 315, 316, 317)
IPADDRESS_MATERIAL_ID = 355


async def _fetch_one(db, sql: str, params: dict) -> dict | None:
    r = await db.execute(text(sql), params)
    row = r.mappings().first()
    return dict(row) if row else None


async def step1_excel_order(db) -> None:
    print("\n--- Шаг 1: course_parents курса 160 (Excel) — свап order_number 161<->164 ---")
    before = (await db.execute(text(
        "SELECT course_id, order_number FROM course_parents "
        "WHERE parent_course_id=160 AND course_id IN (161,164) ORDER BY course_id"
    ))).mappings().all()
    print(f"BEFORE: {[dict(r) for r in before]}")
    before_map = {r["course_id"]: r["order_number"] for r in before}
    assert before_map == {161: 2, 164: 3}, f"неожиданное исходное состояние: {before_map}"

    await db.execute(text(
        "UPDATE course_parents SET order_number=2 "
        "WHERE parent_course_id=160 AND course_id=164"
    ))

    after = (await db.execute(text(
        "SELECT course_id, order_number FROM course_parents "
        "WHERE parent_course_id=160 AND course_id IN (161,164) ORDER BY course_id"
    ))).mappings().all()
    after_map = {r["course_id"]: r["order_number"] for r in after}
    print(f"AFTER:  {[dict(r) for r in after]}")
    assert after_map == {161: 3, 164: 2}, f"свап не применился как ожидалось: {after_map}"
    print("Шаг 1 OK: 164 (базовый интерфейс) теперь order_number=2, 161 (формулы) -> 3.")


async def step2_task2_material_order(db) -> None:
    print("\n--- Шаг 2: materials курса 148 (Задание 2) — свап order_position 395<->397 ---")
    before = (await db.execute(text(
        "SELECT id, order_position, title FROM materials WHERE id IN (395,397) ORDER BY id"
    ))).mappings().all()
    print(f"BEFORE: {[dict(r) for r in before]}")
    before_map = {r["id"]: r["order_position"] for r in before}
    assert before_map == {395: 5, 397: 4}, f"неожиданное исходное состояние: {before_map}"

    await db.execute(text("UPDATE materials SET order_position=4 WHERE id=395"))

    after = (await db.execute(text(
        "SELECT id, order_position, title FROM materials WHERE id IN (395,397) ORDER BY id"
    ))).mappings().all()
    after_map = {r["id"]: r["order_position"] for r in after}
    print(f"AFTER:  {[dict(r) for r in after]}")
    assert after_map == {395: 4, 397: 5}, f"свап не применился как ожидалось: {after_map}"
    print("Шаг 2 OK: 395 (понятие 'Таблица истинности') теперь перед 397 (инструмент Python).")


async def step3_tasks14_15_material_order(db) -> None:
    print("\n--- Шаг 3: materials курсов 142/143 — видео перед текстовым 'Разбором' ---")

    before142 = await _fetch_one(
        db, "SELECT id, order_position FROM materials WHERE id=565", {}
    )
    before142b = await _fetch_one(
        db, "SELECT id, order_position FROM materials WHERE id=379", {}
    )
    print(f"BEFORE 142: 379={before142b} 565={before142}")
    assert before142["order_position"] == 2 and before142b["order_position"] == 1, (
        f"неожиданное исходное состояние курса 142: 379={before142b} 565={before142}"
    )
    await db.execute(text("UPDATE materials SET order_position=1 WHERE id=565"))
    after142 = (await db.execute(text(
        "SELECT id, order_position FROM materials WHERE id IN (379,565) ORDER BY id"
    ))).mappings().all()
    after142_map = {r["id"]: r["order_position"] for r in after142}
    print(f"AFTER 142:  {after142_map}")
    assert after142_map == {379: 2, 565: 1}, f"свап курса 142 не применился: {after142_map}"

    before143 = (await db.execute(text(
        "SELECT id, order_position FROM materials WHERE id IN (383,384,578) ORDER BY id"
    ))).mappings().all()
    before143_map = {r["id"]: r["order_position"] for r in before143}
    print(f"BEFORE 143: {before143_map}")
    assert before143_map == {383: 1, 384: 3, 578: 5}, f"неожиданное исходное состояние курса 143: {before143_map}"
    await db.execute(text("UPDATE materials SET order_position=1 WHERE id=578"))
    after143 = (await db.execute(text(
        "SELECT id, order_position FROM materials WHERE id IN (383,384,578) ORDER BY id"
    ))).mappings().all()
    after143_map = {r["id"]: r["order_position"] for r in after143}
    print(f"AFTER 143:  {after143_map}")
    assert after143_map == {578: 1, 383: 2, 384: 4}, f"свап курса 143 не применился: {after143_map}"
    print("Шаг 3 OK: в обоих узлах базовое видео теперь идёт перед текстовым 'Разбором'.")


async def step4_requirement_level_recommended(db) -> None:
    print("\n--- Шаг 4: requirement_level='recommended' (Turtle x4 + ipaddress-материал) ---")
    ids = list(TURTLE_RECOMMENDED_IDS) + [IPADDRESS_MATERIAL_ID]
    before = (await db.execute(text(
        "SELECT id, course_id, requirement_level FROM materials WHERE id = ANY(:ids) ORDER BY id"
    ), {"ids": ids})).mappings().all()
    print(f"BEFORE: {[dict(r) for r in before]}")
    for r in before:
        assert r["requirement_level"] == "required", f"material {r['id']} уже не 'required': {dict(r)}"

    result = await db.execute(text(
        "UPDATE materials SET requirement_level='recommended' WHERE id = ANY(:ids)"
    ), {"ids": ids})
    assert result.rowcount == len(ids), f"обновлено {result.rowcount}, ожидали {len(ids)}"

    after = (await db.execute(text(
        "SELECT id, course_id, requirement_level FROM materials WHERE id = ANY(:ids) ORDER BY id"
    ), {"ids": ids})).mappings().all()
    print(f"AFTER:  {[dict(r) for r in after]}")
    for r in after:
        assert r["requirement_level"] == "recommended", f"material {r['id']} не обновился: {dict(r)}"
    print("Шаг 4 OK: 5 материалов помечены как 'для общего кругозора' (recommended, не блокируют зачёт).")


async def step5_activate_408_410(db) -> None:
    print("\n--- Шаг 5: is_active=true для материалов 408 (курс 151), 410 (курс 152) ---")
    before = (await db.execute(text(
        "SELECT id, course_id, is_active, requirement_level FROM materials WHERE id IN (408,410) ORDER BY id"
    ))).mappings().all()
    print(f"BEFORE: {[dict(r) for r in before]}")
    for r in before:
        assert r["is_active"] is False, f"material {r['id']} уже активен: {dict(r)}"

    # Ре-проверка гейта безопасности прямо перед записью (см. docstring):
    # ни один студент не должен иметь активности в курсах 151/152/153/154.
    tr_count = (await db.execute(text(
        "SELECT COUNT(*) FROM task_results tr JOIN tasks t ON t.id=tr.task_id "
        "WHERE t.course_id IN (151,152,153,154)"
    ))).scalar()
    smp_count = (await db.execute(text(
        "SELECT COUNT(*) FROM student_material_progress smp JOIN materials m ON m.id=smp.material_id "
        "WHERE m.course_id IN (151,152)"
    ))).scalar()
    print(f"Гейт безопасности: task_results в 151-154 = {tr_count}, student_material_progress в 151/152 = {smp_count}")
    if tr_count or smp_count:
        raise RuntimeError(
            "СТОП: обнаружена студенческая активность в курсах 151-154 — "
            "активация 408/410 может откатить прогресс (паттерн tsk-524). "
            "Нужен manual_progress_service.grant_course_subtree ПЕРЕД активацией, "
            "см. docstring этого скрипта."
        )

    result = await db.execute(text(
        "UPDATE materials SET is_active=true WHERE id IN (408,410)"
    ))
    assert result.rowcount == 2, f"обновлено {result.rowcount}, ожидали 2"

    after = (await db.execute(text(
        "SELECT id, course_id, is_active FROM materials WHERE id IN (408,410) ORDER BY id"
    ))).mappings().all()
    print(f"AFTER:  {[dict(r) for r in after]}")
    for r in after:
        assert r["is_active"] is True, f"material {r['id']} не активировался: {dict(r)}"
    print("Шаг 5 OK: материалы 408/410 активированы (риск отката прогресса подтверждён нулевым).")


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-530: структурные правки курса 112 — {mode} ===")

    async with async_session_factory() as db:
        try:
            await step1_excel_order(db)
            await step2_task2_material_order(db)
            await step3_tasks14_15_material_order(db)
            await step4_requirement_level_recommended(db)
            await step5_activate_408_410(db)
        except Exception as exc:  # noqa: BLE001
            print(f"\nОШИБКА: {exc!r} — ROLLBACK")
            await db.rollback()
            return 1

        if apply:
            await db.commit()
            print("\nCOMMIT — все 5 шагов применены и закоммичены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
