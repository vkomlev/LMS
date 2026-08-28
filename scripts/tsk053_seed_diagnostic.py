"""Наполнение ЕГЭ-диагностики (tsk-053, фаза 2).

Заводит публичный демо-курс с короткими задачами-зондами: по три варианта на каждую
из восьми тем ЕГЭ. Скрипт идемпотентный — повторный прогон обновляет тексты и эталоны,
но не плодит дубли (задачи опознаются по ``external_uid``).

Запуск:

    python scripts/tsk053_seed_diagnostic.py
    python scripts/tsk053_seed_diagnostic.py --apply

**Почему зонды, а не настоящие задания ЕГЭ** (решение оператора 2026-08-28). В банке
лежит 793 разобранных задания ЕГЭ, и первым побуждением было собрать диагностику из них.
Но настоящее задание ЕГЭ — это 3-5 минут даже на уровне «легко»: восемь таких заданий
превращают «диагностику за 15 минут» в получасовую контрольную, до конца которой
посетитель с рекламы не дойдёт. Зонд проверяет тот же навык за минуту: не «решите
задание 5 целиком», а «сколько единиц в двоичной записи числа 2345».

Побочная выгода важнее исходной: платные курсы ЕГЭ остаются закрытыми. Диагностика
живёт в своём публичном курсе и ничего из платного банка наружу не открывает — ни
условий, ни ответов.

**Почему краткий ответ, а не выполнение кода.** Тема «Исполнитель Черепаха» просится
на «нарисуйте треугольник», но проверка кода у гостя означала бы песочницу на публичной
ручке. Зонд спрашивает результат готовой программы («какая фигура получится») — тот же
навык чтения алгоритма, мгновенная проверка, никакого исполнения чужого кода.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("tsk053_seed_diagnostic")

DIAGNOSTIC_UID = "wp:ege-diagnostika"
DIAGNOSTIC_TITLE = "ЕГЭ по информатике: диагностика за 15 минут"
DIAGNOSTIC_DESCRIPTION = (
    "Восемь коротких задач по ключевым темам экзамена. Это не пробник, а быстрая "
    "проверка: покажем, какие темы уже держатся, а какие стоит подтянуть в первую очередь."
)

#: Темы диагностики. `course_uid` — куда вести человека, если тема просела.
TOPICS: List[Dict[str, str]] = [
    {"code": "z2", "title": "Задание 2. Логика и таблицы истинности",
     "course_uid": "wp:zadanie-2-ege-po-informatike-tablitsy-istinnosti"},
    {"code": "z5", "title": "Задание 5. Анализ алгоритмов",
     "course_uid": "wp:zadanie-5-ege-analiz-algoritmov-dlya-ispolnitelej"},
    {"code": "z6", "title": "Задание 6. Исполнитель Черепаха",
     "course_uid": "wp:zadanie-6-ege-po-informatike-ispolnitel-cherepaha"},
    {"code": "z7", "title": "Задание 7. Кодирование информации",
     "course_uid": "wp:zadanie-7-ege-kodirovanie-razlichnyh-vidov-informatsii-peredacha-informatsii"},
    {"code": "z8", "title": "Задание 8. Комбинаторика",
     "course_uid": "wp:zadanie-8-ege-po-informatike-kombinatorika"},
    {"code": "z11", "title": "Задание 11. Объём информации",
     "course_uid": "wp:zadanie-11-ege-po-informatike-vychislenie-obema-informatsii"},
    {"code": "z14", "title": "Задание 14. Системы счисления",
     "course_uid": "wp:zadanie-14-ege-po-informatike-pozitsionnye-sistemy-schisleniya"},
    {"code": "z16", "title": "Задание 16. Рекурсивные функции",
     "course_uid": "wp:zadanie-16-ege-po-informatike-rekursivnye-funktsii"},
]

#: Зонды: тема → варианты (условие, ответ). Три варианта на тему, чтобы отбор был
#: случайным и готовые ответы не расходились одним списком по чатам.
PROBES: Dict[str, List[Dict[str, str]]] = {
    "z2": [
        {"stem": "Сколько существует наборов значений X и Y, при которых выражение "
                 "X И (НЕ Y) истинно?", "answer": "1"},
        {"stem": "Сколько существует наборов значений X, Y и Z, при которых выражение "
                 "X ИЛИ Y ИЛИ Z ложно?", "answer": "1"},
        {"stem": "Сколько существует наборов значений X и Y, при которых выражение "
                 "НЕ (X И Y) истинно?", "answer": "3"},
    ],
    "z5": [
        {"stem": "Сколько единиц в двоичной записи числа 2345?", "answer": "5"},
        {"stem": "Сколько единиц в двоичной записи числа 255?", "answer": "8"},
        {"stem": "Сколько цифр в двоичной записи числа 100?", "answer": "7"},
    ],
    "z6": [
        {"stem": "Черепаха выполняет программу: Повтори 4 [Вперёд 10; Направо 90].\n"
                 "Какая фигура получится? Ответьте одним словом.", "answer": "квадрат"},
        {"stem": "Черепаха выполняет программу: Повтори 3 [Вперёд 20; Направо 120].\n"
                 "Сколько сторон у получившейся фигуры?", "answer": "3"},
        {"stem": "Черепаха выполняет программу: Повтори 6 [Вперёд 10; Направо 60].\n"
                 "Чему равна сумма всех поворотов в градусах?", "answer": "360"},
    ],
    "z7": [
        {"stem": "Изображение размером 100 на 200 пикселей, на каждый пиксель отводится "
                 "8 бит. Сколько байт займёт файл без сжатия?", "answer": "20000"},
        {"stem": "Текст состоит из 512 символов, каждый символ кодируется 16 битами. "
                 "Сколько байт занимает текст?", "answer": "1024"},
        {"stem": "Сколько бит нужно, чтобы закодировать 32 различных символа?",
         "answer": "5"},
    ],
    "z8": [
        {"stem": "Сколько трёхбуквенных слов можно составить из букв А, Б, В, если буквы "
                 "могут повторяться?", "answer": "27"},
        {"stem": "Сколько четырёхзначных чисел можно составить из цифр 1 и 2, если цифры "
                 "могут повторяться?", "answer": "16"},
        {"stem": "Сколько двухбуквенных кодов можно составить из букв А, Б, В, Г, если "
                 "буквы повторяться не могут?", "answer": "12"},
    ],
    "z11": [
        {"stem": "В системе регистрации используется 60 различных символов. Сколько бит "
                 "минимально нужно отвести на один символ?", "answer": "6"},
        {"stem": "Сколько байт занимают 1024 бита?", "answer": "128"},
        {"stem": "Сколько бит минимально нужно, чтобы закодировать 100 различных "
                 "сообщений?", "answer": "7"},
    ],
    "z14": [
        {"stem": "Запишите число 27 в двоичной системе счисления.", "answer": "11011"},
        {"stem": "Чему равно значение выражения 1010₂ + 101₂ в десятичной системе?",
         "answer": "15"},
        {"stem": "Сколько нулей в двоичной записи числа 40?", "answer": "4"},
    ],
    "z16": [
        {"stem": "Функция задана так: F(n) = F(n−1) + 2, F(1) = 1.\nЧему равно F(5)?",
         "answer": "9"},
        {"stem": "Функция задана так: F(n) = n × F(n−1), F(1) = 1.\nЧему равно F(4)?",
         "answer": "24"},
        {"stem": "Функция задана так: F(n) = F(n−1) + F(n−2), F(1) = 1, F(2) = 1.\n"
                 "Чему равно F(6)?", "answer": "8"},
    ],
}


def _task_content(topic: Dict[str, str], stem: str) -> Dict[str, Any]:
    """Содержимое зонда.

    Тема лежит прямо в задаче (`TaskContent` разрешает свои поля): разбор по темам
    и рекомендация курса собираются из неё, без отдельной таблицы соответствий.
    Метка `lead_magnet` нужна воронке — по ней она отличает лид-магнит от обычного
    демо-курса, не завися от типа задания.
    """
    return {
        "type": "SA",
        "stem": stem,
        "lead_magnet": True,
        "diagnostic_topic": {
            "code": topic["code"],
            "title": topic["title"],
            "course_uid": topic["course_uid"],
        },
    }


def _solution_rules(answer: str) -> Dict[str, Any]:
    """Правила сверки: точное совпадение после обрезки пробелов и приведения к нижнему
    регистру. Ответы здесь — число или одно слово, ничего сложнее не нужно."""
    return {
        "max_score": 1,
        "auto_check": True,
        "scoring_mode": "all_or_nothing",
        "manual_review_required": False,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "short_answer": {
            "use_regex": False,
            "regex": None,
            "normalization": ["trim", "lower"],
            "accepted_answers": [{"value": answer, "score": 1}],
        },
    }


async def seed(conn: asyncpg.Connection, apply: bool) -> None:
    """Создать/обновить курс диагностики и зонды."""
    missing = []
    for topic in TOPICS:
        exists = await conn.fetchval(
            "SELECT count(*) FROM courses WHERE course_uid = $1", topic["course_uid"]
        )
        if not exists:
            missing.append(topic["course_uid"])
    if missing:
        # Тема без курса — это рекомендация в никуда: человек увидит «подтяните
        # задание 14» и не получит, куда идти.
        logger.error("нет курсов-тем, ведущих из диагностики: %s", missing)
        sys.exit(1)

    course_id = await conn.fetchval(
        "SELECT id FROM courses WHERE course_uid = $1", DIAGNOSTIC_UID
    )
    if course_id is None:
        logger.info("создаём курс диагностики %s", DIAGNOSTIC_UID)
        if apply:
            course_id = await conn.fetchval(
                "INSERT INTO courses (title, description, access_level, course_uid, "
                "is_public_demo) VALUES ($1, $2, 'auto_check', $3, TRUE) RETURNING id",
                DIAGNOSTIC_TITLE, DIAGNOSTIC_DESCRIPTION, DIAGNOSTIC_UID,
            )
    else:
        logger.info("курс диагностики уже есть: id=%s — обновим описание", course_id)
        if apply:
            await conn.execute(
                "UPDATE courses SET title = $1, description = $2, is_public_demo = TRUE "
                "WHERE id = $3",
                DIAGNOSTIC_TITLE, DIAGNOSTIC_DESCRIPTION, course_id,
            )

    if course_id is None:
        logger.info("разбор без записи: курса нет, дальше показывать нечего — нужен --apply")
        return

    difficulty_id = await conn.fetchval("SELECT id FROM difficulties ORDER BY id LIMIT 1")

    position = 0
    for topic in TOPICS:
        for variant, probe in enumerate(PROBES[topic["code"]], start=1):
            position += 1
            external_uid = f"{DIAGNOSTIC_UID}:{topic['code']}:v{variant}"
            content = json.dumps(_task_content(topic, probe["stem"]), ensure_ascii=False)
            rules = json.dumps(_solution_rules(probe["answer"]), ensure_ascii=False)
            existing = await conn.fetchval(
                "SELECT id FROM tasks WHERE external_uid = $1", external_uid
            )
            logger.info(
                "%s вариант %s (%s): %s",
                topic["title"], variant, "обновление" if existing else "создание",
                probe["stem"].split("\n")[0][:70],
            )
            if not apply:
                continue
            if existing:
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb, solution_rules = $2::jsonb, "
                    "order_position = $3, is_active = TRUE, course_id = $4 WHERE id = $5",
                    content, rules, position, course_id, existing,
                )
            else:
                await conn.execute(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, order_position, is_active) "
                    "VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb, $6, TRUE)",
                    external_uid, content, course_id, difficulty_id, rules, position,
                )

    if apply:
        total = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active", course_id
        )
        topics = await conn.fetchval(
            "SELECT count(DISTINCT task_content->'diagnostic_topic'->>'code') FROM tasks "
            "WHERE course_id = $1 AND is_active",
            course_id,
        )
        expected = sum(len(v) for v in PROBES.values())
        logger.info("готово: курс id=%s, зондов %s, тем %s", course_id, total, topics)
        if total != expected or topics != len(TOPICS):
            logger.error("ожидалось зондов %s и тем %s", expected, len(TOPICS))
            sys.exit(1)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Наполнение ЕГЭ-диагностики (tsk-053)")
    parser.add_argument(
        "--apply", action="store_true", help="выполнить запись; без флага только разбор"
    )
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.error("не задан DATABASE_URL")
        sys.exit(2)
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(dsn)
    try:
        if args.apply:
            async with conn.transaction():
                await seed(conn, apply=True)
        else:
            await seed(conn, apply=False)
            logger.info("это был разбор без записи; чтобы применить — добавьте --apply")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
