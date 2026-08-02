# -*- coding: utf-8 -*-
"""tsk-533: курс 88 «Python для ЕГЭ» — QA часть 3 (Серебрякова) + калибровка
глубины лямбда/декораторов.

ЗАЧЕМ
Оператор принял решения 2026-08-02 (см. tsk-533 в D:\\Work\\Root\\tasks):
1. Курс 104 «Функции»: 3 материала о лямбде (221 text, 531/532 video)
   свести в один компактный блок (соседние order_position), содержание
   материала 221 сделать поверхностным (контекст sort/min/max), плюс общая
   перекластеризация раздела (видео рядом с текстовым двойником той же темы:
   215<->528, 216<->529/530).
2. Курс 1451 «Рекурсия в Python»: добавить материал «Кэш и мемоизация»
   (декоратор в общем виде + @lru_cache + приём предрасчёта кэша) после
   3 существующих материалов (222/533/534).
3. Курс 109 «Списки»: свести «Двумерные массивы» (278 text, 510 video)
   рядом; «Списки в Python (обзор)» (495) — в начало раздела; плюс
   доп. кластеризация двух других точных текст/видео-двойников
   (274<->504 «Перебор», 276<->505 «Функции для списков»), найденных по
   тому же принципу.
4. Задание id=235 (tsikly-v-python:23, курс 110): в стем — явное
   объяснение значения столбца 1 и столбца 2 таблицы TBL_COM.
5. Задание id=266 (spiski-massivy-v-python:14, курс 109): дублирует по
   вычислению задание 261 (тот же срез с шагом 2) — дифференцировано:
   теперь просит срез, начиная СО ВТОРОГО элемента (индекс 1), другой
   верный ответ. Оба задания остаются активны (не задваивают, а
   дополняют друг друга: чётные/нечётные позиции среза).

ПОРЯДОК/ТРИГГЕР
Для курсов 104 и 109 order_position переставляется ПОЛНОЙ плотной
перенумерацией 1..N по новому целевому порядку (не точечный своп) —
триггер `trg_set_material_order_position` глушится на время транзакции
через `app.skip_material_order_trigger` (паттерн tsk524_move_theory_materials.py),
иначе каскадный пересчёт триггера мешал бы присвоить произвольную целевую
перестановку. Материал в курс 1451 — обычный INSERT с order_position=4
(триггер отрабатывает штатно, сдвигать нечего — курс кончается на 3).

РИСК ОТКАТА ПРОГРЕССА
Ни один материал/задание не меняет is_active/requirement_level — только
order_position (навигация) и content/stem (текст). Это НЕ тот риск-класс,
что в tsk-524/tsk-530 (там activate false->true расширяло знаменатель
compute_course_state). Единственная безопасная по конструкции смена
is_active в этой задаче не производится вовсе (261/266 оба остаются
active, отличаются текстом+ответом, не количеством в знаменателе).

Запуск: dry-run по умолчанию;
  python scripts/tsk533_qa3_kurs88.py
  DBCHECK_OK=1 python scripts/tsk533_qa3_kurs88.py --apply
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

project_root = Path(__file__).resolve().parents[1]

COURSE_104 = 104
COURSE_109 = 109
COURSE_1451 = 1451
COURSE_110 = 110  # для задания 235 (проверка course_id)

# ── Курс 104 «Функции» — целевой плотный порядок 1..15 ─────────────────────
COURSE_104_ORDER: dict[int, int] = {
    212: 1, 524: 2, 214: 3, 525: 4, 213: 5, 527: 6, 215: 7, 528: 8,
    216: 9, 529: 10, 530: 11, 526: 12, 221: 13, 531: 14, 532: 15,
}

MATERIAL_221_NEW_CONTENT_HTML = (
    "<p>Лямбда-функция — короткая безымянная функция для одного выражения. "
    "Синтаксис: <code>lambda аргументы: выражение</code>.</p>\r\n"
    "<p>В заданиях ЕГЭ лямбда почти всегда встречается как аргумент "
    "<code>key</code> в <code>sorted()</code>, <code>min()</code>, "
    "<code>max()</code> — задаёт правило сравнения без отдельной именованной "
    "функции:</p>\r\n"
    "<pre><code class=\"language-python\">students = [('Аня', 16), ('Боря', 14), ('Вера', 18)]\r\n\r\n"
    "# сортировка по возрасту (второй элемент кортежа)\r\n"
    "sorted(students, key=lambda x: x[1])\r\n\r\n"
    "# студент с минимальным возрастом\r\n"
    "min(students, key=lambda x: x[1])\r\n"
    "</code></pre>\r\n"
    "<p>Для ЕГЭ этого объёма достаточно — глубже лямбду как отдельную тему "
    "разбирать не нужно.</p>"
)

# ── Курс 1451 «Рекурсия в Python» — новый материал ──────────────────────────
NEW_MATERIAL_1451_TITLE = "Кэш и мемоизация"
NEW_MATERIAL_1451_ORDER = 4
NEW_MATERIAL_1451_HTML = (
    "<p>Декоратор — функция, которая оборачивает другую функцию и добавляет "
    "ей новое поведение, не меняя её код. Синтаксис — строка "
    "<code>@имя_декоратора</code> прямо над определением функции.</p>\r\n"
    "<p><code>@lru_cache</code> из модуля <code>functools</code> — готовый "
    "декоратор для мемоизации: результат функции запоминается по входным "
    "аргументам, и при повторном вызове с теми же аргументами Python "
    "возвращает сохранённый результат вместо повторного вычисления:</p>\r\n"
    "<pre><code class=\"language-python\">from functools import lru_cache\r\n\r\n"
    "@lru_cache(maxsize=None)\r\n"
    "def fib(n):\r\n"
    "    if n &lt;= 1:\r\n"
    "        return n\r\n"
    "    return fib(n - 1) + fib(n - 2)\r\n\r\n"
    "print(fib(50))  # без кеша стек рекурсии не выдержал бы такую глубину\r\n"
    "</code></pre>\r\n"
    "<p>Приём полезен, когда рекурсивная функция вызывается с одними и теми "
    "же аргументами много раз (классика — числа Фибоначчи) или когда нужно "
    "вычислить значение при большом n: без кеша дерево рекурсивных вызовов "
    "растёт экспоненциально и упирается в лимит глубины стека.</p>"
)

# ── Курс 109 «Списки» — целевой плотный порядок 1..29 ───────────────────────
COURSE_109_ORDER: dict[int, int] = {
    269: 1, 495: 2, 496: 3, 497: 4, 270: 5, 509: 6, 498: 7, 500: 8,
    501: 9, 502: 10, 503: 11, 508: 12, 273: 13, 499: 14, 274: 15,
    504: 16, 507: 17, 271: 18, 275: 19, 276: 20, 505: 21, 511: 22,
    272: 23, 506: 24, 277: 25, 279: 26, 278: 27, 510: 28, 280: 29,
}

# ── Задание 235 (курс 110, tsikly-v-python:23) — явное объяснение столбцов ──
TASK_235_ID = 235
TASK_235_NEW_STEM = (
    "Программа в цикле считывает неотрицательные целые числа до тех\n"
    "пор, пока не будет введено отрицательное число (`break`).\n"
    "Найдите максимальное число последовательности и его позицию\n"
    "(нумерация с 0). **Если максимальных чисел несколько — выведите\n"
    "позицию последнего из них.**\n\n"
    "Ответ оформите в виде таблицы из двух столбцов: в первый столбец\n"
    "впишите само максимальное число, во второй столбец — его позицию.\n\n"
    "Запустите программу со следующей последовательностью:\n"
    "```\n5\n3\n8\n1\n8\n4\n2\n-1\n```\n"
    "Введите результат работы программы в таблицу «Ответ».\n"
)

# ── Задание 266 (курс 109, spiski-massivy-v-python:14) — дифференциация ─────
TASK_266_ID = 266
TASK_266_NEW_STEM = (
    "Дан фиксированный список\n"
    "`lst = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]`.\n"
    "Получите и выведите список, состоящий из каждого второго элемента,\n"
    "начиная СО ВТОРОГО (индекс 1), то есть `lst[1], lst[3], lst[5], ...`\n\n"
    "Поместите вывод в поле «Ответ».\n"
)
TASK_266_NEW_ANSWER = "[20, 40, 60, 80, 100]"


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (паттерн tsk-362/366/373)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def _apply_dense_order(conn: asyncpg.Connection, course_id: int, order: dict[int, int]) -> None:
    rows = await conn.fetch(
        "SELECT id, order_position FROM materials WHERE course_id = $1", course_id
    )
    have_ids = {r["id"] for r in rows}
    if have_ids != set(order):
        raise RuntimeError(
            f"course_id={course_id}: набор материалов разошёлся с планом. "
            f"В БД: {sorted(have_ids)}; в плане: {sorted(order)}"
        )
    for material_id, pos in order.items():
        await conn.execute(
            "UPDATE materials SET order_position = $1 WHERE id = $2", pos, material_id
        )


async def _preview(conn: asyncpg.Connection) -> None:
    print("=" * 78)
    print("tsk-533 · курс 88 QA часть 3 + калибровка глубины · DRY-RUN")
    print("=" * 78)

    for course_id, order in ((COURSE_104, COURSE_104_ORDER), (COURSE_109, COURSE_109_ORDER)):
        rows = await conn.fetch(
            "SELECT id, title, order_position FROM materials WHERE course_id = $1 "
            "ORDER BY order_position", course_id,
        )
        print(f"\nКурс {course_id}: текущий порядок ({len(rows)} материалов):")
        for r in rows:
            new_pos = order.get(r["id"], "?")
            marker = "  <-- move" if new_pos != r["order_position"] else ""
            print(f"  {r['order_position']:>3} -> {new_pos:>3}  id={r['id']:>5}  «{r['title']}»{marker}")

    m1451 = await conn.fetch(
        "SELECT id, title, order_position FROM materials WHERE course_id = $1 ORDER BY order_position",
        COURSE_1451,
    )
    print(f"\nКурс {COURSE_1451}: текущие материалы:")
    for r in m1451:
        print(f"  {r['order_position']:>3}  id={r['id']:>5}  «{r['title']}»")
    print(f"  -> добавить: order={NEW_MATERIAL_1451_ORDER}  «{NEW_MATERIAL_1451_TITLE}»")

    for task_id in (TASK_235_ID, TASK_266_ID):
        row = await conn.fetchrow(
            "SELECT id, course_id, external_uid, task_content, solution_rules FROM tasks WHERE id = $1",
            task_id,
        )
        print(f"\nЗадание {task_id} (course_id={row['course_id']}, {row['external_uid']}):")
        content = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
        print(f"  текущий стем: {content['stem'][:120]!r}...")

    print("\nDRY-RUN: ничего не записано. Повтор с --apply.")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        if not apply:
            await _preview(conn)
            return

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'true', true)")
            await _apply_dense_order(conn, COURSE_104, COURSE_104_ORDER)
            await _apply_dense_order(conn, COURSE_109, COURSE_109_ORDER)
            await conn.execute("SELECT set_config('app.skip_material_order_trigger', 'false', true)")

            await conn.execute(
                "UPDATE materials SET content = $1::jsonb WHERE id = 221",
                json.dumps({"text": MATERIAL_221_NEW_CONTENT_HTML, "format": "html"}),
            )

            n_1451 = await conn.fetchval("SELECT count(*) FROM materials WHERE course_id = $1", COURSE_1451)
            already = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE course_id = $1 AND title = $2",
                COURSE_1451, NEW_MATERIAL_1451_TITLE,
            )
            if already == 0:
                await conn.execute(
                    "INSERT INTO materials (course_id, type, content, order_position, "
                    "title, is_active, requirement_level) "
                    "VALUES ($1, 'text'::content_type, $2::jsonb, $3, $4, true, 'required')",
                    COURSE_1451,
                    json.dumps({"text": NEW_MATERIAL_1451_HTML, "format": "html"}),
                    NEW_MATERIAL_1451_ORDER,
                    NEW_MATERIAL_1451_TITLE,
                )
                print(f"Курс {COURSE_1451}: материал «{NEW_MATERIAL_1451_TITLE}» создан")
            else:
                print(f"Курс {COURSE_1451}: материал «{NEW_MATERIAL_1451_TITLE}» уже есть — пропуск INSERT")

            row_235 = await conn.fetchrow("SELECT task_content FROM tasks WHERE id = $1", TASK_235_ID)
            content_235 = json.loads(row_235["task_content"]) if isinstance(row_235["task_content"], str) else row_235["task_content"]
            content_235["stem"] = TASK_235_NEW_STEM
            await conn.execute(
                "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                json.dumps(content_235), TASK_235_ID,
            )

            row_266 = await conn.fetchrow(
                "SELECT task_content, solution_rules FROM tasks WHERE id = $1", TASK_266_ID
            )
            content_266 = json.loads(row_266["task_content"]) if isinstance(row_266["task_content"], str) else row_266["task_content"]
            rules_266 = json.loads(row_266["solution_rules"]) if isinstance(row_266["solution_rules"], str) else row_266["solution_rules"]
            content_266["stem"] = TASK_266_NEW_STEM
            rules_266["short_answer"]["accepted_answers"][0]["value"] = TASK_266_NEW_ANSWER
            await conn.execute(
                "UPDATE tasks SET task_content = $1::jsonb, solution_rules = $2::jsonb WHERE id = $3",
                json.dumps(content_266), json.dumps(rules_266), TASK_266_ID,
            )

            # ── Верификация ДО COMMIT ──────────────────────────────────────
            print("\nВерификация в транзакции:")
            for course_id in (COURSE_104, COURSE_109, COURSE_1451):
                dup = await conn.fetchval(
                    "SELECT COALESCE(sum(c), 0) FROM ("
                    "  SELECT count(*) - 1 AS c FROM materials WHERE course_id = $1 "
                    "  AND order_position IS NOT NULL GROUP BY order_position HAVING count(*) > 1"
                    ") x", course_id,
                )
                total = await conn.fetchval("SELECT count(*) FROM materials WHERE course_id = $1", course_id)
                print(f"  курс {course_id}: материалов={total}, коллизий order_position={dup} (ожидание 0)")
                if dup != 0:
                    raise RuntimeError(f"course_id={course_id}: коллизии order_position — ROLLBACK.")

            new_221 = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id = 221")
            print(f"  материал 221 обновлён: {'lambda' in new_221 or 'sorted' in new_221}")

            new_material_1451 = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE course_id = $1 AND title = $2",
                COURSE_1451, NEW_MATERIAL_1451_TITLE,
            )
            print(f"  курс {COURSE_1451}: материал «{NEW_MATERIAL_1451_TITLE}» присутствует: {new_material_1451 == 1}")

            stem_235 = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id = $1", TASK_235_ID)
            print(f"  задание 235: столбцы объяснены в стеме: {'столбец' in stem_235.lower()}")

            answer_266 = await conn.fetchval(
                "SELECT solution_rules->'short_answer'->'accepted_answers'->0->>'value' FROM tasks WHERE id = $1",
                TASK_266_ID,
            )
            print(f"  задание 266: новый ответ = {answer_266!r} (ожидание {TASK_266_NEW_ANSWER!r})")
            if answer_266 != TASK_266_NEW_ANSWER:
                raise RuntimeError("задание 266: ответ не сошёлся — ROLLBACK.")

        print("\nCOMMIT выполнен.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-533: курс 88 QA часть 3 + калибровка глубины")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
