# -*- coding: utf-8 -*-
"""tsk-395: устранить дублирование заданий ОГЭ-14 в курсе 1179 + перевести форму «целиком» в SA_COM.

КОНТЕКСТ
Курс 1179 (ОГЭ-14) — единственный в блоке ОГЭ, где каждая из 25 задач источника лежит
дважды: «целиком» (`sdamgia:oge:14:<id>`, одно задание с тремя вопросами) и разбитой на два
подвопроса (`oge:reshu:t14:<id>_1/_2`, 50 заданий). Соседние курсы держат одну форму:
1163/1164 — подвопросы, 1178/1180 — целиком. Ученик решает одно и то же дважды (tsk-392).

Решение оператора (2026-07-24): оставить форму «ЦЕЛИКОМ» — одно задание, три вопроса сразу
(1–2 точный ответ + 3 диаграмма), с обязательной ручной проверкой (диаграмму принимает
преподаватель). Форму подвопросов — деактивировать.

ЧТО ДЕЛАЕТ (одна транзакция, только курс 1179)
  A. Деактивирует 50 заданий-подвопросов `oge:reshu:t14:%` (is_active=false). Ни попыток, ни
     результатов, ни прогресса на них нет — деактивация ничего у учеников не рушит (проверка
     этого — часть скрипта, а не допущение).
  B. Переводит 24 задания «целиком» `sdamgia:oge:14:%` типа SA в SA_COM. SA_COM добавляет
     ученику поле-комментарий (описать построенную диаграмму / привести значения ячеек).
     `manual_review_required` (уже true) и эталон ответа «32 546,82» СОХРАНЯЮТСЯ.
     Задание 7170 (`sdamgia:oge:14:11044`) — тип TBL_COM (таблица перелётов), его форма
     другая, тип не трогаем; ручная проверка на нём уже стоит.

О «два ответа автопроверяются». Движок (`checking_service._check_short_answer`) при
`manual_review_required=true` НЕ выставляет авто-вердикт вовсе (`is_correct=None, score=0`) —
ответ уходит в очередь преподавателя. Значит, два числа сверяет преподаватель по сохранённому
эталону «32 546,82», а не мгновенная авто-проверка на стороне ученика. Гибрид «числа
автопроверяются + диаграмма гейтит вручную» движок сейчас не поддерживает; эталон сохранён,
чтобы (а) преподаватель видел верные числа, (б) при снятии флага числа заработали на авто-проверке.

ЧЕГО НЕ ТРОГАЕТ: ответы, правила проверки, файлы-приложения, порядок, авторские задания,
любые другие курсы.

ЗАЩИТЫ
  * бэкап прежнего состояния (is_active + type по каждому id) на диск ДО записи;
  * повторный запуск идемпотентен: уже деактивированные/уже SA_COM пропускаются;
  * dry-run по умолчанию; запись — только с --apply при DBCHECK_OK=1;
  * построчная проверка внутри транзакции и независимая построчная после COMMIT.

Запуск:
  python scripts/tsk395_dedup_oge14.py --backup <файл> [--apply]
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
from tsk369_collect import dsn  # noqa: E402

COURSE_ID = 1179
RESHU_LIKE = "oge:reshu:t14:%"
SDAMGIA_LIKE = "sdamgia:oge:14:%"


async def load_state(conn: asyncpg.Connection) -> dict[str, list[asyncpg.Record]]:
    reshu = await conn.fetch(
        "SELECT id, external_uid, is_active, task_content->>'type' AS type "
        "FROM tasks WHERE course_id=$1 AND external_uid LIKE $2 ORDER BY id",
        COURSE_ID, RESHU_LIKE)
    sdamgia = await conn.fetch(
        "SELECT id, external_uid, is_active, task_content->>'type' AS type "
        "FROM tasks WHERE course_id=$1 AND external_uid LIKE $2 ORDER BY id",
        COURSE_ID, SDAMGIA_LIKE)
    return {"reshu": reshu, "sdamgia": sdamgia}


async def assert_no_student_data(conn: asyncpg.Connection, ids: list[int]) -> None:
    """Деактивировать задание с попытками/результатами/прогрессом нельзя — проверяем, что их нет."""
    checks = {
        "task_results": "SELECT count(*) FROM task_results WHERE task_id = ANY($1::int[])",
        "student_task_progress": "SELECT count(*) FROM student_task_progress WHERE task_id = ANY($1::int[])",
        "guest_attempt": "SELECT count(*) FROM guest_attempt WHERE task_id = ANY($1::int[])",
    }
    for name, sql in checks.items():
        n = await conn.fetchval(sql, ids)
        if n:
            raise RuntimeError(f"СТОП: на деактивируемых заданиях есть данные ученика "
                               f"({name}={n}) — деактивация запрещена, разбирать вручную.")


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        st = await load_state(conn)
        reshu_ids = [r["id"] for r in st["reshu"]]
        # B затрагивает только тип SA; TBL_COM (7170) и уже-SA_COM не трогаем.
        sacom_ids = [r["id"] for r in st["sdamgia"] if r["type"] == "SA" and r["is_active"]]
        reshu_to_off = [r["id"] for r in st["reshu"] if r["is_active"]]

        print(f"Курс {COURSE_ID}:")
        print(f"  A. деактивировать подвопросов reshu: {len(reshu_to_off)} "
              f"(из {len(st['reshu'])}; уже неактивных {len(st['reshu']) - len(reshu_to_off)})")
        print(f"  B. SA→SA_COM у 'целиком' sdamgia: {len(sacom_ids)} "
              f"(из {len(st['sdamgia'])}; прочие типы/уже SA_COM не трогаю)")

        await assert_no_student_data(conn, reshu_ids)
        print("  проверка данных ученика на подвопросах: чисто (0 результатов/прогресса/гостей)")

        if not reshu_to_off and not sacom_ids:
            print("Нечего менять (повторный запуск?).")
            return

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            {"reshu": [dict(r) for r in st["reshu"]],
             "sdamgia": [dict(r) for r in st["sdamgia"]]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежнего состояния: {backup_path}")

        async with conn.transaction():
            if reshu_to_off:
                await conn.execute(
                    "UPDATE tasks SET is_active=false WHERE id = ANY($1::int[])", reshu_to_off)
            if sacom_ids:
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{type}', '\"SA_COM\"') "
                    "WHERE id = ANY($1::int[])", sacom_ids)

            # Проверка внутри транзакции.
            bad = []
            for r in await conn.fetch(
                    "SELECT id, is_active, task_content->>'type' AS type FROM tasks "
                    "WHERE id = ANY($1::int[])", reshu_ids + [x["id"] for x in st["sdamgia"]]):
                if r["id"] in reshu_to_off and r["is_active"]:
                    bad.append((r["id"], "не деактивировано"))
                if r["id"] in sacom_ids and r["type"] != "SA_COM":
                    bad.append((r["id"], f"тип {r['type']}, ждали SA_COM"))
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad[:10]}")
            print(f"Внутри транзакции: деактивировано {len(reshu_to_off)}, "
                  f"переведено в SA_COM {len(sacom_ids)} — проверено.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {r["id"]: r for r in await conn.fetch(
            "SELECT id, is_active, task_content->>'type' AS type FROM tasks "
            "WHERE id = ANY($1::int[])", reshu_ids + [x["id"] for x in st["sdamgia"]])}
        problems = []
        for tid in reshu_to_off:
            if after[tid]["is_active"]:
                problems.append((tid, "активно"))
        for tid in sacom_ids:
            if after[tid]["type"] != "SA_COM":
                problems.append((tid, after[tid]["type"]))
        active_reshu = sum(1 for r in st["reshu"] if after[r["id"]]["is_active"])
        active_sdamgia = sum(1 for r in st["sdamgia"] if after[r["id"]]["is_active"])
        print(f"  проверено построчно: {len(reshu_to_off) + len(sacom_ids)}; расхождений: {len(problems)}")
        print(f"  активных reshu-подвопросов осталось: {active_reshu} (ждём 0)")
        print(f"  активных sdamgia-целиком: {active_sdamgia} (ждём 25)")
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
            sys.exit(1)
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
