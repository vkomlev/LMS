# -*- coding: utf-8 -*-
"""Регулярный чек: активные задания, которые невозможно проверить (tsk-361).

Зачем. Ученик отвечает, а система не может признать ответ верным: правила проверки
либо нет вовсе, либо оно есть, но пустое. Задание молча становится «всегда неверно».
Наружу это не всплывает — ни ошибки, ни лога; ловилось только точечными разборами
(tsk-325 → 790 заданий, tsk-100 → 280, tsk-361 → 10).

Почему чек именно такой. `tasks.solution_rules` — JSONB, и «пусто» там принимает ДВЕ
разные формы:
  * SQL NULL — колонка пустая;
  * JSON-null — в колонке лежит валидный JSON `null`.
Предикат `solution_rules IS NULL` ловит только первую. Именно на нём в июле 2026
получилось ложное «на платформе чисто», хотя 10 заданий были сломаны (tsk-361).
Правильный предикат: `solution_rules IS NULL OR jsonb_typeof(solution_rules) = 'null'`.

Третья форма — правило-объект, но пустое: `auto_check=true`, ни accepted_answers, ни
correct_options, при этом `manual_review_required=false`. Структурно правило есть,
проверить ответ нечем и в ручную оно не уйдёт. Тот же дефект, другая маскировка, и
на момент написания чека таких заданий 231 (SA_COM/SC_Qw в 28 курсах).

Обе формы ищутся БЕЗ привязки к типу задания, поэтому пустой `TBL_COM` (табличный
ответ, tsk-366) ловится теми же запросами, что и пустой `SA_COM`: эталон таблицы
лежит в том же `short_answer.accepted_answers`.

Отдельно, БЕЗ влияния на код выхода, печатается раздел «кандидаты в TBL_COM»:
активные SA/SA_COM, чей эталон — несколько значений через пробельные символы. Такой
ответ ученик вводит вслепую: поле одно, разделитель приходится угадывать — ровно та
боль, ради которой заведён тип TBL_COM. Это НЕ дефект: тот же вид ответа честно
бывает у SA_COM, где вывод программы обязан быть ОДНОЙ строкой («выведите числа через
пробел в одну строку»), и автоперевод таких заданий в таблицу был бы ошибкой
(принцип tsk-325/tsk-370 — не додумывать за источник). Поэтому раздел — сигнал на
разбор, а не красный свет; разобранное задание снимается с учёта пометкой
`task_content.tbl_com_not_applicable = true`. Без этого раздела класс вернулся бы
следующей партией импорта и всплыл бы только жалобой ученика (tsk-366, пункт 5).

Что делает. Считает и перечисляет активные задания обеих форм. Read-only: ни одного
UPDATE. Чинит не этот скрипт — он только сообщает.

Куда смотрит. В базу из `DATABASE_URL`; по умолчанию это dev (прод от скриптов закрыт,
tsk-246). Прод — явным override:
    DATABASE_URL=<прод-dsn> python scripts/check_ungradable_tasks.py
Скрипт всегда печатает хост и базу, которую проверил.

Запуск из корня проекта:
    python scripts/check_ungradable_tasks.py            # полный отчёт
    python scripts/check_ungradable_tasks.py --quiet     # только находки (для планировщика)

Коды выхода: 0 — непроверяемых заданий нет; 1 — найдены; 2 — ошибка выполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# Форма 1: правила проверки нет вовсе — SQL NULL или JSON-null.
SQL_EMPTY_RULES = """
SELECT t.id, t.course_id, t.task_content->>'type' AS task_type,
       jsonb_typeof(t.solution_rules) AS rules_type,
       left(coalesce(t.task_content->>'stem', ''), 70) AS stem
FROM tasks t
WHERE t.is_active
  AND (t.solution_rules IS NULL OR jsonb_typeof(t.solution_rules) = 'null')
ORDER BY t.course_id, t.id
"""

# Форма 2: правило-объект есть, но проверить им нечего и в ручную задание не уйдёт.
SQL_HOLLOW_RULES = """
SELECT t.id, t.course_id, t.task_content->>'type' AS task_type,
       left(coalesce(t.task_content->>'stem', ''), 70) AS stem
FROM tasks t
WHERE t.is_active
  AND jsonb_typeof(t.solution_rules) = 'object'
  AND (t.solution_rules->>'manual_review_required')::bool IS NOT TRUE
  AND coalesce(jsonb_array_length(t.solution_rules#>'{short_answer,accepted_answers}'), 0) = 0
  AND coalesce(jsonb_array_length(t.solution_rules->'correct_options'), 0) = 0
  AND coalesce(t.solution_rules->>'text_answer', '') = ''
  AND t.solution_rules->'custom_scoring_config' IS NOT DISTINCT FROM 'null'::jsonb
  -- Опросники-профилировщики (тип SC_Qw, блок `quiz` со шкалами) верного ответа не
  -- имеют по замыслу: они подбирают ученику курс, а не проверяют знание. Для них
  -- пустое правило — норма, а не дефект (tsk-362).
  AND jsonb_typeof(t.solution_rules->'quiz') IS DISTINCT FROM 'object'
ORDER BY t.course_id, t.id
"""


# Сигнал (не дефект): краткий ответ из нескольких значений через пробельные символы —
# кандидат в TBL_COM. Разобранные снимаются пометкой `tbl_com_not_applicable`.
SQL_TBL_COM_CANDIDATES = """
SELECT t.id, t.course_id, t.task_content->>'type' AS task_type,
       coalesce(t.solution_rules#>>'{short_answer,accepted_answers,0,value}',
                t.task_content->>'answer_raw') AS answer
FROM tasks t
WHERE t.is_active
  AND t.task_content->>'type' IN ('SA', 'SA_COM')
  AND (t.task_content->>'tbl_com_not_applicable')::bool IS NOT TRUE
  -- Критерий тот же, что у разметки грунта (scripts/tsk366_mark_pending_tbl_com.py):
  -- ВСЕ значения — числа. Иначе в раздел попадёт честный текстовый ответ из двух слов
  -- («Три встречи»), и сигнал утонет в шуме.
  AND coalesce(t.solution_rules#>>'{short_answer,accepted_answers,0,value}',
               t.task_content->>'answer_raw') ~ '^\\d+([ \\n\\r\\t]+\\d+)+$'
ORDER BY t.course_id, t.id
"""


async def main(quiet: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL (ни в окружении, ни в .env)", file=sys.stderr)
        return 2
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            where = (await conn.execute(text(
                "SELECT current_database() AS db, inet_server_addr()::text AS host"
            ))).mappings().first()
            print(f"Проверяю базу: {where['db']} на {where['host'] or 'localhost'}")

            empty = (await conn.execute(text(SQL_EMPTY_RULES))).mappings().all()
            hollow = (await conn.execute(text(SQL_HOLLOW_RULES))).mappings().all()
            candidates = (await conn.execute(text(SQL_TBL_COM_CANDIDATES))).mappings().all()
    finally:
        await engine.dispose()

    total = len(empty) + len(hollow)

    if empty:
        print(f"\nБЕЗ ПРАВИЛА ПРОВЕРКИ (solution_rules пуст): {len(empty)}")
        for r in empty[:50]:
            print(f"  id={r['id']} курс={r['course_id']} тип={r['task_type']} "
                  f"({r['rules_type'] or 'SQL NULL'}): {r['stem']}")
        if len(empty) > 50:
            print(f"  … и ещё {len(empty) - 50}")
    elif not quiet:
        print("\nБЕЗ ПРАВИЛА ПРОВЕРКИ: 0 — OK")

    if hollow:
        print(f"\nПРАВИЛО ЕСТЬ, НО ПУСТОЕ (нечем проверить, в ручную не уйдёт): {len(hollow)}")
        by_course: dict[int, int] = {}
        for r in hollow:
            by_course[r["course_id"]] = by_course.get(r["course_id"], 0) + 1
        for course_id, n in sorted(by_course.items(), key=lambda kv: -kv[1]):
            print(f"  курс {course_id}: {n}")
        print("  примеры: " + ", ".join(str(r["id"]) for r in hollow[:15]))
    elif not quiet:
        print("\nПРАВИЛО ЕСТЬ, НО ПУСТОЕ: 0 — OK")

    # Раздел-сигнал: код выхода НЕ меняет (см. модульную докстроку).
    if candidates:
        print(
            f"\nК СВЕДЕНИЮ — кандидаты в TBL_COM (ответ из нескольких значений "
            f"в одном поле): {len(candidates)}"
        )
        by_course: dict[int, int] = {}
        for r in candidates:
            by_course[r["course_id"]] = by_course.get(r["course_id"], 0) + 1
        for course_id, n in sorted(by_course.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  курс {course_id}: {n}")
        print("  примеры: " + ", ".join(str(r["id"]) for r in candidates[:15]))
        print(
            "  Разобрать: табличный ответ → тип TBL_COM (tsk-366); ответ обязан быть "
            "одной строкой → пометить task_content.tbl_com_not_applicable = true."
        )
    elif not quiet:
        print("\nКАНДИДАТЫ В TBL_COM: 0 — OK")

    if total:
        print(f"\nИТОГО непроверяемых активных заданий: {total}")
        return 1

    print("\nOK: все активные задания проверяемы.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="печатать только находки")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(quiet=args.quiet)))
    except Exception as exc:  # noqa: BLE001 — чек под планировщиком, причина обязана попасть в лог
        print(f"ОШИБКА выполнения чека: {exc}", file=sys.stderr)
        sys.exit(2)
