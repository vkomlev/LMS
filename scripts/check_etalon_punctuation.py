# -*- coding: utf-8 -*-
"""Регулярный чек: эталон короткого ответа начинается с мусорной пунктуации (tsk-787).

Зачем. Такой эталон НЕ отказывает верному ответу — нормализация (`strip_punctuation`
превращает знак в пробел, `collapse_spaces` схлопывает) стирает разницу между
«— 2640» и «2640». Поэтому дефект невидим и для тестов, и для ученика: он проходит
проверку. Цена в другом — эталон видит преподаватель, и «— 2640» читается как
ОТРИЦАТЕЛЬНОЕ количество строк, то есть бессмыслица. 03.09.2026 по заданию 2223
преподаватель решил, что эталон битый, поверил ученику и зачёл неверный ответ 563
при верном 2640 (tsk-787). До этого тот же мусор («— 163») правили в tsk-687.

Почему чек нужен именно регулярный. Оба раза класс нашёл живой человек, севший
решать: ни один тест и ни один прежний чек его не видят. Хуже того, в августе чистка
была ЧАСТИЧНОЙ — правили только те задания, что нашёл детектор ручных зачётов, то
есть 1 из 33, — и класс вернулся через неделю. Чек закрывает именно это: он смотрит
класс целиком, а не выборку по жалобам.

Что считается мусором и почему НЕ «любая пунктуация». Ведущий знак сам по себе
законен у сотен эталонов: `['C++', 'Python']` (список), `{'Alice': 90}` (словарь),
`= A1 + B1` (формула Excel), `.env`, `#include <DHT.h>`, `<=`, `+=`, `%`, `?`,
`*` (рисунок звёздочками), `//` (комментарий). Отдельно законен ЗНАК ЧИСЛА:
`-8` («Округление числа вниз»), `-1000000` («Начальное значение максимума»),
`−392` («Минимальная сумма пути ладьи»). Поэтому предикат узкий, из двух форм:

  * длинное (U+2014) или среднее (U+2013) тире в начале — знаком отрицательного
    числа они не бывают никогда, минус пишут дефисом или U+2212;
  * дефис/минус, за которым ПРОБЕЛ, — «- 42»: у отрицательного числа пробела
    после знака не бывает, значит это пунктуация фразы, попавшая в значение;
  * ведущий пробел, двоеточие, точка с запятой, запятая — остаток фразы
    («: 42», «, 42»), тот же класс, другой знак.

Откуда мусор берётся. Парсер sdamgia в ContentBackbone
(`monolith/external_tasks/parsers/html/sdamgia.py`) вытягивал ответ регуляркой
`\\bответ\\s*:?\\s*([^\\.;]+)` из блока `div.solution`, который в `_ANSWER_SELECTORS`
стоял РАНЬШЕ чистого `div.answer`. Текст решения у sdamgia кончается фразой
«...получим ответ  — 2640.», и тире фразы попадало в значение. Источник закрыт в
tsk-787, но чек нужен и после: следующий источник (другой сайт, разбор PDF,
пересказ моделью) принесёт тот же класс своим путём.

Что делает. Считает и перечисляет активные задания с таким эталоном. Read-only:
ни одного UPDATE. Чинит не этот скрипт — `scripts/tsk787_strip_leading_dash_etalons.py`.

Куда смотрит. В базу из `DATABASE_URL`; по умолчанию это dev (прод от скриптов
закрыт, tsk-246). Прод — явным override:
    DATABASE_URL=<прод-dsn> python scripts/check_etalon_punctuation.py
Скрипт всегда печатает хост и базу, которую проверил.

Запуск из корня проекта:
    python scripts/check_etalon_punctuation.py            # полный отчёт
    python scripts/check_etalon_punctuation.py --quiet    # только находки
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# tsk-641: под планировщиком чек идёт через pythonw.exe, где консоли нет вовсе.
if sys.platform == "win32" and not os.environ.get("LMS_CHECK_NO_CONSOLE"):
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

#: Мусорный ведущий знак. Формы и обоснование каждой — в модульной документации.
#: Дефис/минус БЕЗ пробела намеренно не ловятся: это законный знак числа.
GARBAGE_LEAD_REGEX = r"^([\s—–:;,]|[-−]\s)"

SQL_GARBAGE_ETALONS = f"""
SELECT t.id, t.course_id, t.external_uid,
       t.task_content->>'type' AS task_type,
       t.task_content->>'title' AS title,
       ae.ord - 1 AS answer_index,
       ae.val->>'value' AS value
FROM tasks t
CROSS JOIN LATERAL jsonb_array_elements(t.solution_rules #> '{{short_answer,accepted_answers}}')
     WITH ORDINALITY AS ae(val, ord)
WHERE t.is_active
  AND jsonb_typeof(t.solution_rules) = 'object'
  AND jsonb_typeof(t.solution_rules #> '{{short_answer,accepted_answers}}') = 'array'
  AND ae.val->>'value' ~ '{GARBAGE_LEAD_REGEX}'
ORDER BY t.course_id, t.id, ae.ord
"""


async def main(quiet: bool) -> int:
    """Найти эталоны с мусорным ведущим знаком. 1 — есть находки, 0 — чисто."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL (ни в окружении, ни в .env)", file=sys.stderr)
        return 2

    # Куда сходили — печатаем всегда: чек читает то dev, то прод, и без этой строки
    # «находок нет» ничего не значит.
    safe = dsn.split("@")[-1] if "@" in dsn else dsn
    print(f"База: {safe}")

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(SQL_GARBAGE_ETALONS))).mappings().all()
    finally:
        await engine.dispose()

    if not rows:
        if not quiet:
            print("\nOK: эталонов с мусорным ведущим знаком нет.")
        return 0

    print(f"\nНАЙДЕНЫ эталоны с мусорным ведущим знаком: {len(rows)}")
    for row in rows:
        title = row["title"] or "(без названия)"
        print(
            f"  [{row['id']}] курс {row['course_id']} {row['task_type']} "
            f"«{title[:50]}» эталон #{row['answer_index']}: {row['value'][:60]!r}"
        )
    print(
        "\n  Чем это опасно: приёму ответа такой эталон не мешает, но преподаватель "
        "видит бессмыслицу, решает, что эталон битый, и засчитывает неверный ответ "
        "(tsk-787, задание 2223)."
    )
    print(
        "  Как чинить: сверить значение с источником (у партий sdamgia чистый ответ "
        "лежит в том же сыром HTML, блок div.answer в external_tasks.task.payload_data), "
        "затем scripts/tsk787_strip_leading_dash_etalons.py — сухой прогон, потом "
        "DBCHECK_OK=1 ... --apply."
    )
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="печатать только находки")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(quiet=args.quiet)))
    except Exception as exc:  # noqa: BLE001 — чек под планировщиком, причина обязана попасть в лог
        print(f"ОШИБКА выполнения чека: {exc}", file=sys.stderr)
        sys.exit(2)
