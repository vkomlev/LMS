# -*- coding: utf-8 -*-
"""tsk-373, добор: развести пару 2080 / 3309 — задача kpolyakov 7239 попала не в тот курс.

ЧТО ВСКРЫЛОСЬ (решение оператора, 2026-07-22)

Задание 3309 (`tg:ege:543`) было единственным из 74 расхождений, где ответ не сходился ни с
чем: в условии функция от a, b, c (задание 2), а ответ `xzwy` — из четырёх переменных
w, x, y, z. Разбор показал: ошибочен не ответ, а САМО УСЛОВИЕ. Пост в Telegram нёс текст
чужого задания 2, тогда как прикреплённое к нему видео называется `7239.webm` и разбирает
задачу kpolyakov 7239. Оператор посмотрел видео и подтвердил: это задание 16 (рекурсивная
функция), верный ответ 20155393. Сам сайт источника это и пишет: «Задача № 7239 · Задание
КИМ № 16: Вычисление значения рекурсивной функции». Ответ `xzwy` пришёл от третьей задачи —
он дословно совпадает с ответом задания 4233 (`wp_nav:2:00b4a5b9`), у которого переменные
как раз w, x, y, z: сработала общая преамбула заданий этого типа.

Настоящая задача при этом в LMS уже есть — задание **2080** (`ext:d4:polyakov:20260602:7239`)
с верным условием, ответом 20155393 и тем же видео-разбором. Но неверный номер увёл в курс
«Задание 2 ЕГЭ. Таблицы истинности» обе копии.

ЧТО ДЕЛАЕТСЯ (вариант оператора)
  1. **2080** переносится в курс 144 «Задание 16 ЕГЭ. Рекурсивные функции», проставляются
     проверенные `source_kind`/`source_task_id` = polyakov/7239.
  2. **3309** деактивируется как испорченный дубль: своего условия у него нет, а настоящая
     задача остаётся в 2080. Попыток учеников нет ни по одному из двух.

ПРО ТРИГГЕР НУМЕРАЦИИ (важно)
`trg_set_task_order_position` рассчитан на перестановку ВНУТРИ курса: при смене
`course_id` вместе с `order_position` он пересчитает позиции в курсе-ПРИЁМНИКЕ, сдвинув
чужие задания. Поэтому обе правки идут при выключенном триггере
(`app.skip_task_order_trigger`, тот же механизм, что в `app/repos/tasks_repo.py`), а
позиция задаётся явно — в конец курса 144. Дырка в нумерации курса 148 остаётся намеренно:
порядок считается по `(order_position, id)`, пропуск на выдачу не влияет, а сплошная
перенумерация тронула бы 70 чужих строк.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Бэкап — до записи, проверка — после COMMIT.

Запуск: python scripts/tsk373_fix_3309.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import dsn  # noqa: E402

MOVE_ID = 2080          # настоящая задача polyakov 7239
TARGET_COURSE = 144     # «Задание 16 ЕГЭ по информатике. Рекурсивные функции»
SOURCE_KIND = "polyakov"
SOURCE_TASK_ID = "7239"
DEACTIVATE_ID = 3309    # испорченный дубль из ТГ-партии


async def main(backup_path: Path, apply: bool) -> None:
    ids = [MOVE_ID, DEACTIVATE_ID]
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, course_id, order_position, is_active, task_content "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        if set(rows) != set(ids):
            raise RuntimeError(f"не нашёл заданий: {sorted(set(ids) - set(rows))}")
        if not rows[MOVE_ID]["is_active"]:
            raise RuntimeError(f"{MOVE_ID} неактивно — переносить нечего")
        if not rows[DEACTIVATE_ID]["is_active"]:
            raise RuntimeError(f"{DEACTIVATE_ID} уже неактивно")

        used = await conn.fetchval(
            "SELECT count(*) FROM task_results WHERE task_id = ANY($1::int[])", ids)
        if used:
            raise RuntimeError(f"по этим заданиям есть {used} попыток — нужен разбор "
                               f"результатов до правки, автоматически не трогаю")

        target_max = await conn.fetchval(
            "SELECT coalesce(max(order_position), 0) FROM tasks WHERE course_id = $1",
            TARGET_COURSE)
        new_pos = target_max + 1

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"],
              "course_id": rows[i]["course_id"], "order_position": rows[i]["order_position"],
              "is_active": rows[i]["is_active"],
              "task_content": json.loads(rows[i]["task_content"])} for i in ids],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап: {backup_path}")
        print(f"  {MOVE_ID}: курс {rows[MOVE_ID]['course_id']} поз."
              f"{rows[MOVE_ID]['order_position']} → курс {TARGET_COURSE} поз.{new_pos}, "
              f"источник {SOURCE_KIND}:{SOURCE_TASK_ID}")
        print(f"  {DEACTIVATE_ID}: активно → неактивно (испорченный дубль)")

        async with conn.transaction():
            # Триггер нумерации выключается на транзакцию: при смене курса он пересчитал бы
            # позиции в курсе-приёмнике, сдвинув чужие задания.
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute(
                "UPDATE tasks SET course_id = $2, order_position = $3, "
                "  task_content = jsonb_set(jsonb_set(task_content, "
                "    '{source_kind}', to_jsonb($4::text)), "
                "    '{source_task_id}', to_jsonb($5::text)) "
                "WHERE id = $1",
                MOVE_ID, TARGET_COURSE, new_pos, SOURCE_KIND, SOURCE_TASK_ID)
            await conn.execute(
                "UPDATE tasks SET is_active = false WHERE id = $1", DEACTIVATE_ID)

            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, course_id, order_position, is_active, "
                "       task_content->>'source_kind' AS kind, "
                "       task_content->>'source_task_id' AS sid "
                "FROM tasks WHERE id = ANY($1::int[])", ids)}
            problems = []
            m = check[MOVE_ID]
            if (m["course_id"] != TARGET_COURSE or m["order_position"] != new_pos
                    or m["kind"] != SOURCE_KIND or m["sid"] != SOURCE_TASK_ID
                    or not m["is_active"]):
                problems.append(MOVE_ID)
            if check[DEACTIVATE_ID]["is_active"]:
                problems.append(DEACTIVATE_ID)
            # чужие задания курса-приёмника не должны были сдвинуться
            dup = await conn.fetchval(
                "SELECT count(*) FROM (SELECT order_position FROM tasks "
                "WHERE course_id = $1 GROUP BY order_position HAVING count(*) > 1) x",
                TARGET_COURSE)
            if dup:
                problems.append(f"в курсе {TARGET_COURSE} повторяются позиции: {dup}")
            if problems:
                raise AssertionError(f"проверка внутри транзакции не прошла: {problems}")
            print("Внутри транзакции: перенос и деактивация проверены, "
                  f"повторов позиций в курсе {TARGET_COURSE} нет.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetch(
            "SELECT id, course_id, order_position, is_active, "
            "       task_content->>'source_kind' AS kind, "
            "       task_content->>'source_task_id' AS sid, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS ans "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", ids)
        for r in after:
            print(f"  id={r['id']}: курс={r['course_id']} поз.={r['order_position']} "
                  f"активно={r['is_active']} источник={r['kind']}:{r['sid']} ответ={r['ans']!r}")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
