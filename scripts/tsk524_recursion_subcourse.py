# -*- coding: utf-8 -*-
"""tsk-524: подкурс «Рекурсия в Python» после курса 104 «Функции» в курсе 88.

ЗАЧЕМ
tsk-424 (naive-learner-review + expert-course-review курса 88) нашёл пробел:
материал 222 курса 104 подробно разбирает рекурсию (базовый/рекурсивный случай,
стек вызовов, факториал, Фибоначчи; плюс видео 533/534), но ни одно из 43
заданий курса не требует написать рекурсивную функцию самому.

Решение оператора (2026-08-02): не добавлять пару заданий в курс 104, а вынести
рекурсию в отдельный подкурс, проходимый ПОСЛЕ курса 104. Задачи уже готовы на
WP (https://victor-komlev.ru/funktsii-v-python-sozdanie-sobstvennyh-funktsij/,
раздел «Контрольные вопросы», задания 37-45) — перенесены и адаптированы под
LMS-формат (SA_COM: написать функцию, вывести print(...), ответ — вывод
программы), не сочинены заново. Точные формулировки и решения сайта (только
словесное описание алгоритма, без кода) сверены живым браузером 2026-08-02.

ТЕОРИЯ НЕ ЗАДВАИВАЕТСЯ
Полная теория рекурсии уже живёт в курсе 104 (материал 222 + видео 533/534).
Новый подкурс получает один короткий материал-мостик (напоминание базового и
рекурсивного случая + ссылка вернуться к материалу 222), а не копию теории.

МЕСТО В ГРАФЕ
course_parents: (новый_id, parent=88, order_number=9) — сразу после курса 104
(order_number=8), перед курсом 105 (был order_number=9). Триггер
trg_set_course_parent_order_number сам сдвигает 105/107 на +1 при INSERT с
явным order_number — ручной UPDATE не нужен.

ГЕЙТ ДОСТУПА
course_dependencies: (новый_id, required=104). Паттерн уже используется в проде
для НЕ-корневых пар внутри одного дерева (пример: курсы 1373→1363/1307, все три
— дети курса 1283) — `_BLOCKED_COURSES_SQL` в me_service.py блокирует courses.id
из tree_ids, пока required_course_id не COMPLETED в student_course_state, вне
зависимости от того, корневой курс или нет. Автоназначение зависимостей
(course_dependencies_enrollment_service.py, tsk-261) при назначении курса
специально пропускает НЕ-корневые required-курсы — это не задевает наш случай:
подкурс 104 уже назначен всем, кто зачислен на корень 88.

ПОРЯДОК ЗАДАНИЙ / ТРИГГЕР
tasks/materials вставляются с явным order_position 1..N в НОВЫЙ (пустой)
course_id — trg_set_task_order_position и trg_set_material_order_position
отрабатывают штатно (при INSERT с явным order_position сдвигают только то, что
>= этого значения в ЭТОМ course_id; при первой вставке сдвигать нечего).
Скип-флаги (как в tsk347_hard_subcourses.py) не нужны — там их использовали для
UPDATE course_id у уже существующих строк, здесь только INSERT новых.

ИДЕМПОТЕНТНОСТЬ
courses.course_uid = 'lms:tsk524:recursion' — повторный запуск находит курс по
uid и не создаёт дубль; materials/tasks вставляются только если у курса их пока
нет (count == 0).

Запуск: dry-run по умолчанию;
  python scripts/tsk524_recursion_subcourse.py
  DBCHECK_OK=1 python scripts/tsk524_recursion_subcourse.py --apply
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

PARENT_COURSE_ID = 88          # Python для ЕГЭ
REQUIRED_COURSE_ID = 104       # Функции в Python. Создание собственных функций.
NEW_COURSE_UID = "lms:tsk524:recursion"
NEW_COURSE_TITLE = "Рекурсия в Python"
NEW_ORDER_NUMBER = 9           # сразу после курса 104 (order_number=8)

INTRO_MATERIAL_TITLE = "От теории к практике: рекурсия"
INTRO_MATERIAL_HTML = (
    "<p>В курсе «Функции в Python» вы уже разобрали, что такое рекурсия: "
    "базовый и рекурсивный случай, факториал, числа Фибоначчи и стек вызовов "
    "функций (материал «Рекурсия», видео «Рекурсия (основы)» и «Рекурсивные "
    "функции: практика»). Здесь теорию не повторяем — сразу практика: девять "
    "задач на самостоятельное написание рекурсивных функций.</p>"
    "<blockquote class=\"warning\"><strong>Напоминание.</strong> У рекурсивной "
    "функции всегда есть <strong>базовый случай</strong> (функция возвращает "
    "результат без нового вызова самой себя) и <strong>рекурсивный случай</strong> "
    "(функция вызывает саму себя с аргументами, которые приближают её к "
    "базовому случаю). Если что-то забылось — вернитесь к материалу «Рекурсия» "
    "в курсе «Функции в Python».</blockquote>"
)

# 9 заданий = WP «Контрольные вопросы», задания 37-45 (адаптация, не новый
# контент). Все — SA_COM: написать функцию, затем print(вызов), в ответ —
# вывод программы (тот же паттерн, что и у всех 43 заданий курса 104).
TASKS: list[dict] = [
    {
        "wp_task": 37,
        "difficulty_id": 3,
        "stem": (
            "Алгоритм вычисления значения функции F(n), где n — целое "
            "неотрицательное число, задан следующими соотношениями:\n\n"
            "F(0) = 0;\n"
            "F(n) = F(n / 2), если n > 0 и при этом чётно;\n"
            "F(n) = 1 + F(n − 1), если n нечётно.\n\n"
            "Сколько существует таких чисел n, что 1 ≤ n ≤ 1000 и F(n) = 3?\n\n"
            "В ответе запишите только целое число."
        ),
        "answer": "120",
    },
    {
        "wp_task": 38,
        "difficulty_id": 1,
        "stem": (
            "Напишите рекурсивную функцию `new_pow(a, n)` для вычисления `a` "
            "в степени `n` (`n` — неотрицательное целое число). Базовый "
            "случай: `n == 0` → результат `1`. Рекурсивный случай: "
            "`new_pow(a, n) = a * new_pow(a, n - 1)`.\n\n"
            "Затем выведите результат вызова `print(new_pow(3, 5))`.\n"
            "Введите вывод программы в поле «Ответ»."
        ),
        "answer": "243",
    },
    {
        "wp_task": 39,
        "difficulty_id": 2,
        "stem": (
            "Напишите рекурсивную функцию `fibonacci(n)`, вычисляющую "
            "n-ное число Фибоначчи:\n\n"
            "F(0) = 0\n"
            "F(1) = 1\n"
            "F(n) = F(n-1) + F(n-2), для n > 1\n\n"
            "Затем выведите результат вызова `print(fibonacci(10))`.\n"
            "Введите вывод программы в поле «Ответ»."
        ),
        "answer": "55",
    },
    {
        "wp_task": 40,
        "difficulty_id": 3,
        "stem": (
            "Положительные числа вводятся с клавиатуры по одному. Окончание "
            "ввода — число 0. Напишите рекурсивную функцию для вывода этих "
            "чисел в обратном порядке.\n\n"
            "Нельзя использовать списки и другие структуры данных для "
            "хранения промежуточных значений — только рекурсивные вызовы, "
            "`input()` и `print()`.\n\n"
            "Проверьте работу функции на вводе: 5, 3, 8, 1, 0 (по одному "
            "числу на вызов `input()`, последним вводится 0 — сигнал конца "
            "ввода, само число 0 не выводится). Какая последовательность "
            "чисел будет выведена (в обратном порядке относительно ввода)? "
            "Введите вывод через пробел в поле «Ответ»."
        ),
        "answer": "1 8 3 5",
    },
    {
        "wp_task": 41,
        "difficulty_id": 1,
        "stem": (
            "Дано натуральное число `n`. Напишите рекурсивную функцию, "
            "которая выводит все числа от 1 до `n` (каждое через `print`). "
            "Циклы использовать запрещено — только рекурсивные вызовы.\n\n"
            "Проверьте работу функции при `n = 6`.\n"
            "Введите вывод программы через пробел в поле «Ответ»."
        ),
        "answer": "1 2 3 4 5 6",
    },
    {
        "wp_task": 42,
        "difficulty_id": 2,
        "stem": (
            "Даны два целых числа `A` и `B`. Напишите рекурсивную функцию, "
            "которая выводит все числа от `A` до `B` включительно: в порядке "
            "возрастания, если `A < B`, или в порядке убывания, если "
            "`A ≥ B`. Циклы использовать запрещено — только рекурсивные "
            "вызовы.\n\n"
            "Проверьте работу функции при `A = 7`, `B = 2`.\n"
            "Введите вывод программы через пробел в поле «Ответ»."
        ),
        "answer": "7 6 5 4 3 2",
    },
    {
        "wp_task": 43,
        "difficulty_id": 3,
        "stem": (
            "Напишите рекурсивную функцию перевода числа из десятичной "
            "системы счисления в двоичную. Затем обобщите функцию так, чтобы "
            "она переводила число в любую систему счисления с основанием до "
            "10 включительно (например, `convert(n, base)`).\n\n"
            "Затем выведите результаты вызовов `print(convert(45, 2))` и "
            "`print(convert(45, 5))`.\n"
            "Введите оба вывода через пробел в поле «Ответ» (сначала перевод "
            "в двоичную, затем в пятеричную)."
        ),
        "answer": "101101 140",
    },
    {
        "wp_task": 44,
        "difficulty_id": 2,
        "stem": (
            "Даны два числа `m` и `n`. Напишите рекурсивную функцию, "
            "находящую наибольший общий делитель двух чисел по алгоритму "
            "Евклида (базовый случай: если `n == 0`, НОД равен `m`; иначе "
            "НОД(`m`, `n`) = НОД(`n`, `m % n`)).\n\n"
            "Затем выведите результат вызова `print(gcd(252, 105))`.\n"
            "Введите вывод программы в поле «Ответ»."
        ),
        "answer": "21",
    },
    {
        "wp_task": 45,
        "difficulty_id": 3,
        "stem": (
            "С помощью рекурсивных функций найдите сумму, произведение и "
            "максимальное значение элементов числовой последовательности. "
            "Стандартные агрегатные функции (`sum`, `max` и т. п.) "
            "использовать запрещается — реализуйте `rec_sum`, `rec_product`, "
            "`rec_max` рекурсивно самостоятельно.\n\n"
            "Затем выведите результаты вызовов для последовательности "
            "`[4, 9, 2, 7, 5]`:\n"
            "`print(rec_sum([4, 9, 2, 7, 5]))`\n"
            "`print(rec_product([4, 9, 2, 7, 5]))`\n"
            "`print(rec_max([4, 9, 2, 7, 5]))`\n\n"
            "Введите три вывода через пробел (сумма, произведение, "
            "максимум) в поле «Ответ»."
        ),
        "answer": "27 2520 9",
    },
]


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


def _task_content(stem: str) -> dict:
    return {
        "code": None,
        "stem": stem,
        "tags": None,
        "type": "SA_COM",
        "media": None,
        "title": None,
        "prompt": None,
        "options": None,
        "course_uid": None,
        "difficulty_code": None,
    }


def _solution_rules(answer: str) -> dict:
    return {
        "max_score": 1,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "auto_check": True,
        "text_answer": None,
        "scoring_mode": "all_or_nothing",
        "short_answer": {
            "regex": None,
            "use_regex": False,
            "normalization": ["trim", "strip_punctuation", "collapse_spaces"],
            "accepted_answers": [{"score": 1, "value": answer}],
        },
        "partial_rules": [],
        "correct_options": [],
        "custom_scoring_config": None,
        "manual_review_required": False,
    }


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        # ── Разведка ДО любых изменений ─────────────────────────────────
        siblings = await conn.fetch(
            "SELECT cp.course_id, cp.order_number, c.title "
            "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
            "WHERE cp.parent_course_id = $1 ORDER BY cp.order_number NULLS LAST",
            PARENT_COURSE_ID,
        )
        required_row = await conn.fetchrow(
            "SELECT id, title FROM courses WHERE id = $1", REQUIRED_COURSE_ID
        )
        existing_course = await conn.fetchrow(
            "SELECT id FROM courses WHERE course_uid = $1", NEW_COURSE_UID
        )
        dup_check = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks "
            "WHERE course_id = $1", REQUIRED_COURSE_ID
        )
        recursion_kw = ("рекурс", "фибоначчи", "факториал", "gcd", "нод")
        possible_dupes = [
            r for r in dup_check
            if r["stem"] and any(kw in r["stem"].lower() for kw in recursion_kw)
        ]

        print("=" * 78)
        print(f"tsk-524 · подкурс «{NEW_COURSE_TITLE}» в курс {PARENT_COURSE_ID} · "
              f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)
        print(f"Требуемый курс: {REQUIRED_COURSE_ID} «{required_row['title']}»")
        print(f"Дети курса {PARENT_COURSE_ID} сейчас:")
        for s in siblings:
            print(f"  order={s['order_number']:>2}  id={s['course_id']:>4}  {s['title']}")
        print(f"Новый order_number: {NEW_ORDER_NUMBER} (сдвинет всех с order_number >= "
              f"{NEW_ORDER_NUMBER} на +1 — триггер БД, не ручной UPDATE)")
        print(f"Курс-маркер course_uid={NEW_COURSE_UID!r} "
              f"{'уже существует id=' + str(existing_course['id']) if existing_course else 'ещё не создан'}")
        print(f"Проверка на дубли темы рекурсии среди 43 заданий курса {REQUIRED_COURSE_ID}: "
              f"{len(possible_dupes)} потенциальных совпадений")
        for r in possible_dupes:
            print(f"    id={r['id']}: {r['stem'][:80]!r}")
        print("-" * 78)
        print(f"Заданий к вставке: {len(TASKS)} (WP 37-45)")
        for i, t in enumerate(TASKS, start=1):
            print(f"  order={i}  wp_task={t['wp_task']}  difficulty_id={t['difficulty_id']}  "
                  f"answer={t['answer']!r}")

        if not apply:
            print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
            return

        async with conn.transaction():
            course_id = existing_course["id"] if existing_course else await conn.fetchval(
                "INSERT INTO courses (title, access_level, description, is_required, "
                "course_uid, is_public_demo) "
                "VALUES ($1, 'self_guided'::access_level_type, NULL, false, $2, false) "
                "RETURNING id",
                NEW_COURSE_TITLE,
                NEW_COURSE_UID,
            )
            print(f"\nКурс: id={course_id} "
                  f"({'уже был' if existing_course else 'создан'})")

            await conn.execute(
                "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
                "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                course_id, PARENT_COURSE_ID, NEW_ORDER_NUMBER,
            )
            await conn.execute(
                "INSERT INTO course_dependencies (course_id, required_course_id) "
                "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                course_id, REQUIRED_COURSE_ID,
            )

            n_materials = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE course_id = $1", course_id
            )
            if n_materials == 0:
                await conn.execute(
                    "INSERT INTO materials (course_id, type, content, order_position, "
                    "title, is_active, requirement_level) "
                    "VALUES ($1, 'text'::content_type, $2::jsonb, 1, $3, true, 'required')",
                    course_id,
                    json.dumps({"text": INTRO_MATERIAL_HTML, "format": "html"}),
                    INTRO_MATERIAL_TITLE,
                )
                print("Материал-мостик: создан (order_position=1)")
            else:
                print(f"Материалы уже есть ({n_materials}) — пропускаю вставку")

            n_tasks = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1", course_id
            )
            if n_tasks == 0:
                for i, t in enumerate(TASKS, start=1):
                    await conn.execute(
                        "INSERT INTO tasks (course_id, max_score, task_content, "
                        "difficulty_id, solution_rules, order_position, is_active, "
                        "requirement_level) "
                        "VALUES ($1, 1, $2::jsonb, $3, $4::jsonb, $5, true, 'required')",
                        course_id,
                        json.dumps(_task_content(t["stem"])),
                        t["difficulty_id"],
                        json.dumps(_solution_rules(t["answer"])),
                        i,
                    )
                print(f"Задания: создано {len(TASKS)} (order_position 1..{len(TASKS)})")
            else:
                print(f"Задания уже есть ({n_tasks}) — пропускаю вставку")

            # ── Верификация ДО COMMIT ────────────────────────────────────
            print("\nВерификация в транзакции:")
            cp_row = await conn.fetchrow(
                "SELECT order_number FROM course_parents WHERE course_id = $1 "
                "AND parent_course_id = $2", course_id, PARENT_COURSE_ID,
            )
            dep_row = await conn.fetchrow(
                "SELECT 1 FROM course_dependencies WHERE course_id = $1 "
                "AND required_course_id = $2", course_id, REQUIRED_COURSE_ID,
            )
            materials_cnt = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE course_id = $1", course_id
            )
            tasks_cnt = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1", course_id
            )
            siblings_after = await conn.fetch(
                "SELECT cp.course_id, cp.order_number, c.title "
                "FROM course_parents cp JOIN courses c ON c.id = cp.course_id "
                "WHERE cp.parent_course_id = $1 ORDER BY cp.order_number NULLS LAST",
                PARENT_COURSE_ID,
            )
            dup_order = await conn.fetchval(
                "SELECT COALESCE(sum(c), 0) FROM ("
                "  SELECT count(*) - 1 AS c FROM course_parents "
                "  WHERE parent_course_id = $1 GROUP BY order_number HAVING count(*) > 1"
                ") x",
                PARENT_COURSE_ID,
            )
            task_order_dupes = await conn.fetchval(
                "SELECT COALESCE(sum(c), 0) FROM ("
                "  SELECT count(*) - 1 AS c FROM tasks WHERE course_id = $1 "
                "  GROUP BY order_position HAVING count(*) > 1"
                ") x",
                course_id,
            )

            print(f"  course_parents order_number={cp_row['order_number'] if cp_row else None} "
                  f"(ожидание {NEW_ORDER_NUMBER})")
            print(f"  course_dependencies -> {REQUIRED_COURSE_ID}: "
                  f"{'есть' if dep_row else 'НЕТ'} (ожидание есть)")
            print(f"  материалов в новом курсе: {materials_cnt} (ожидание 1)")
            print(f"  заданий в новом курсе: {tasks_cnt} (ожидание {len(TASKS)})")
            print(f"  коллизий order_number среди детей {PARENT_COURSE_ID}: {dup_order} (ожидание 0)")
            print(f"  коллизий order_position внутри нового курса: {task_order_dupes} (ожидание 0)")
            print("  дети курса 88 после вставки:")
            for s in siblings_after:
                print(f"    order={s['order_number']:>2}  id={s['course_id']:>4}  {s['title']}")

            ok = (
                cp_row is not None and cp_row["order_number"] == NEW_ORDER_NUMBER
                and dep_row is not None
                and materials_cnt == 1
                and tasks_cnt == len(TASKS)
                and dup_order == 0
                and task_order_dupes == 0
            )
            if not ok:
                raise RuntimeError("Верификация не сошлась — ROLLBACK.")

        print(f"\nCOMMIT выполнен. course_id={course_id}. "
              "Независимую проверку делать через MCP learn_prod_db.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-524: подкурс «Рекурсия в Python»")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
