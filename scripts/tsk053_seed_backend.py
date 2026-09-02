"""Наполнение проверки «Готов ли ты к Backend?» (tsk-053, фаза 3).

Третий лид-магнит. Механизм тот же, что у ЕГЭ-диагностики (фаза 2): публичный
демо-курс с короткими задачами-зондами, по три варианта на тему, выбор варианта — по
хешу гостевой сессии. Меняется только содержание; кода в LMS почти не потребовалось —
зашитость под ЕГЭ снята реестром ``MAGNETS`` в ``guest_diagnostic_service``.

Запуск:

    python scripts/tsk053_seed_backend.py
    python scripts/tsk053_seed_backend.py --apply

**Почему темы не «ещё раз Python».** На сайте уже лежат три теста по Python на плагине
QSM (в том числе «Большой тест Python» на 230 вопросов). Повторять их значило бы завести
четвёртый тест про то же самое. Здесь проверяется именно готовность к backend-работе:
половина тем — язык (типы, строки, коллекции, функции), половина — то, без чего backend
не бывает: HTTP, API и JSON, база и SQL, git. Человек, знающий только синтаксис, увидит
ровно ту половину, которой ему не хватает.

**Почему ответы короткие.** Зонд должен занимать минуту: восемь тем — это десять минут
вместе с чтением. Поэтому «что выведет код» и «каким кодом отвечает сервер», а не
«напишите функцию»: проверка кода у гостя означала бы песочницу на публичной ручке.
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
logger = logging.getLogger("tsk053_seed_backend")

BACKEND_UID = "wp:backend-gotovnost"
BACKEND_TITLE = "Готов ли ты к Backend?"
BACKEND_DESCRIPTION = (
    "Восемь коротких задач: язык, протокол, данные, git. Не экзамен, а честная картина — "
    "что уже есть для работы бэкендером, а что стоит добрать в первую очередь."
)

#: Темы. `course_uid` — куда вести человека, если тема просела.
TOPICS: List[Dict[str, str]] = [
    {"code": "b1", "title": "Типы данных и операции",
     "course_uid": "wp:pervaya-programma-na-python-osnovnye-konstruktsii"},
    {"code": "b2", "title": "Строки",
     "course_uid": "wp:rabota-so-strokami-v-python"},
    {"code": "b3", "title": "Списки и словари",
     "course_uid": "wp:rabota-so-slovaryami-v-python"},
    {"code": "b4", "title": "Функции",
     "course_uid": "wp:funktsii-v-python-sozdanie-sobstvennyh-funktsij"},
    {"code": "b5", "title": "HTTP и коды ответов",
     "course_uid": "wp:qa-manual-g3-l3"},
    {"code": "b6", "title": "API и JSON",
     "course_uid": "wp:chat-boty-tg-vk-max:chat-bot-api-dannye"},
    {"code": "b7", "title": "Базы данных и SQL",
     "course_uid": "wp:chat-boty-tg-vk-max:chat-bot-sqlite"},
    {"code": "b8", "title": "Git и командная строка",
     "course_uid": "wp:chat-boty-tg-vk-max:chat-bot-github-portfolio"},
]

#: Зонды: тема → варианты (условие, ответ). Ответы пересчитаны вручную при составлении.
PROBES: Dict[str, List[Dict[str, str]]] = {
    "b1": [
        {"stem": "Что выведет этот код?\nprint(type(7 / 2).__name__)", "answer": "float"},
        {"stem": "Что выведет этот код?\nprint(7 // 2)", "answer": "3"},
        {"stem": "Что выведет этот код?\nprint(len([1, 2, 3] + [4]))", "answer": "4"},
    ],
    "b2": [
        {"stem": "Что выведет этот код?\nprint(\"backend\"[0:3])", "answer": "bac"},
        {"stem": "Что выведет этот код?\nprint(\"a,b,c\".split(\",\")[1])", "answer": "b"},
        {"stem": "Что выведет этот код?\nprint(len(\"Python\".upper()))", "answer": "6"},
    ],
    "b3": [
        {"stem": "Что выведет этот код?\nuser = {\"id\": 7, \"name\": \"Аня\"}\n"
                 "print(user[\"id\"])", "answer": "7"},
        {"stem": "Что выведет этот код?\nprint(len({\"a\": 1, \"b\": 2, \"a\": 3}))",
         "answer": "2"},
        {"stem": "Что выведет этот код?\nprint([1, 2, 3][-1])", "answer": "3"},
    ],
    "b4": [
        {"stem": "Что выведет этот код?\ndef f(a, b=2):\n    return a * b\nprint(f(3))",
         "answer": "6"},
        {"stem": "Что выведет этот код?\ndef f(x):\n    x = x + 1\nprint(f(5))",
         "answer": "None"},
        {"stem": "Что выведет этот код?\ndef f(*args):\n    return len(args)\n"
                 "print(f(1, 2, 3))", "answer": "3"},
    ],
    "b5": [
        {"stem": "Каким числовым кодом сервер отвечает, если запрошенной страницы нет?",
         "answer": "404"},
        {"stem": "Каким числовым кодом сервер отвечает, когда запрос выполнен успешно?",
         "answer": "200"},
        {"stem": "Каким числовым кодом сервер сообщает о своей внутренней ошибке?",
         "answer": "500"},
    ],
    "b6": [
        {"stem": "API вернуло такой ответ:\n{\"user\": {\"id\": 12, \"city\": \"Казань\"}}\n"
                 "Что вернёт data[\"user\"][\"city\"]?", "answer": "Казань"},
        {"stem": "API вернуло такой ответ:\n{\"items\": [10, 20, 30]}\n"
                 "Что вернёт data[\"items\"][2]?", "answer": "30"},
        {"stem": "Каким HTTP-методом обычно создают новую запись через API?\n"
                 "Ответьте одним словом.", "answer": "POST"},
    ],
    "b7": [
        {"stem": "В таблице users 100 строк, id — первичный ключ.\n"
                 "Сколько строк вернёт SELECT * FROM users WHERE id = 1?", "answer": "1"},
        {"stem": "Каким словом в SQL задают условие отбора строк?\n"
                 "Ответьте одним словом.", "answer": "WHERE"},
        {"stem": "Каким словом в SQL добавляют новую строку в таблицу?\n"
                 "Ответьте одним словом.", "answer": "INSERT"},
    ],
    "b8": [
        {"stem": "Какой командой git сохраняют изменения в историю проекта?\n"
                 "Ответьте одним словом, без слова git.", "answer": "commit"},
        {"stem": "Какой командой git отправляют свои коммиты на сервер?\n"
                 "Ответьте одним словом, без слова git.", "answer": "push"},
        {"stem": "Какой командой git забирают изменения с сервера к себе?\n"
                 "Ответьте одним словом, без слова git.", "answer": "pull"},
    ],
}


def _task_content(topic: Dict[str, str], stem: str) -> Dict[str, Any]:
    """Содержимое зонда: тема лежит прямо в задаче, метку видит воронка."""
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
    """Сверка после обрезки пробелов и приведения к нижнему регистру.

    Регистр не важен в обе стороны: `POST` и `post`, `None` и `none` засчитываются
    одинаково — человек проверяет знание, а не аккуратность нажатия Shift.
    """
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
    """Создать/обновить курс проверки и зонды."""
    missing = []
    for topic in TOPICS:
        exists = await conn.fetchval(
            "SELECT count(*) FROM courses WHERE course_uid = $1", topic["course_uid"]
        )
        if not exists:
            missing.append(topic["course_uid"])
    if missing:
        # Тема без курса — рекомендация в никуда: «подтяните HTTP» и некуда идти.
        logger.error("нет курсов-тем, ведущих из проверки: %s", missing)
        sys.exit(1)

    course_id = await conn.fetchval(
        "SELECT id FROM courses WHERE course_uid = $1", BACKEND_UID
    )
    if course_id is None:
        logger.info("создаём курс проверки %s", BACKEND_UID)
        if apply:
            course_id = await conn.fetchval(
                "INSERT INTO courses (title, description, access_level, course_uid, "
                "is_public_demo) VALUES ($1, $2, 'auto_check', $3, TRUE) RETURNING id",
                BACKEND_TITLE, BACKEND_DESCRIPTION, BACKEND_UID,
            )
    else:
        logger.info("курс проверки уже есть: id=%s — обновим описание", course_id)
        if apply:
            await conn.execute(
                "UPDATE courses SET title = $1, description = $2, is_public_demo = TRUE "
                "WHERE id = $3",
                BACKEND_TITLE, BACKEND_DESCRIPTION, course_id,
            )

    if course_id is None:
        logger.info("разбор без записи: курса нет, дальше показывать нечего — нужен --apply")
        return

    difficulty_id = await conn.fetchval("SELECT id FROM difficulties ORDER BY id LIMIT 1")

    position = 0
    for topic in TOPICS:
        for variant, probe in enumerate(PROBES[topic["code"]], start=1):
            position += 1
            external_uid = f"{BACKEND_UID}:{topic['code']}:v{variant}"
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
    parser = argparse.ArgumentParser(
        description="Наполнение проверки «Готов ли ты к Backend?» (tsk-053, фаза 3)"
    )
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
