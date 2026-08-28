"""Наполнение квиза-лид-магнита «Какой курс мне подходит» (tsk-053, фаза 1).

Заводит курс-квиз, шесть вопросов и правила подбора программы. Скрипт
идемпотентный: повторный прогон обновляет содержимое вопросов и правил, но не
плодит дубли — вопросы опознаются по ``external_uid``, правила по ``code``.

Запуск (по умолчанию только показывает, что сделает):

    python scripts/tsk053_seed_quiz.py
    python scripts/tsk053_seed_quiz.py --apply

Против прода — с загруженным прод-DSN и после протокола /db-check:

    DBCHECK_OK=1 python scripts/tsk053_seed_quiz.py --apply

Голос текстов — на «вы» (решение оператора 2026-08-28): квиз часто проходит
родитель за ребёнка, и он же потом платит.
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
logger = logging.getLogger("tsk053_seed_quiz")

QUIZ_UID = "wp:kviz-podbor-kursa"
QUIZ_TITLE = "Какой курс мне подходит"
QUIZ_DESCRIPTION = (
    "Шесть вопросов — и мы подскажем, с какой программы начать в осеннем наборе. "
    "Правильных ответов здесь нет."
)

#: Курс «ЕГЭ по информатике» продаётся, но машинного имени у него не было, и
#: правило подбора сослаться на него не могло. Поле пустое — заполняем, а не
#: перезаписываем (решение оператора 2026-08-28).
EGE_COURSE_ID = 112
EGE_COURSE_UID = "wp:ege-informatika"

#: Шкалы подбора. Ключ — имя шкалы, значение — курс, куда ведёт победа шкалы.
SCALE_TARGETS: Dict[str, str] = {
    "школа": "wp:informatika-5-11",
    "огэ": "wp:oge-informatika",
    "егэ": EGE_COURSE_UID,
    "питон": "wp:python-podrostki-11-14",
    "техника": "wp:mehatronika-arduino-monitoring",
    "работа": "wp:ruchnoe-testirovanie",
    "бизнес": "wp:ai-predprinimatel",
}

SCALES: List[str] = list(SCALE_TARGETS)

#: Вопросы. Первый вопрос весит больше остальных: возраст и роль разводят
#: направления сильнее любого предпочтения, и без этого веса девятиклассник с
#: одиннадцатиклассником получали бы одинаковый итог.
QUESTIONS: List[Dict[str, Any]] = [
    {
        "stem": "Кто будет учиться?",
        "options": [
            # Возраст говорит только о ступени, а не об интересе: приписка
            # «питон» здесь перевешивала два осознанных выбора в пользу техники
            # дальше по опросу (поймано прогоном профилей до выката).
            ("A", "Школьник 5–8 класса", {"школа": 3}),
            ("B", "Девятиклассник", {"огэ": 3, "школа": 1}),
            ("C", "Десяти- или одиннадцатиклассник", {"егэ": 3, "школа": 1}),
            ("D", "Взрослый — для себя или для работы", {"работа": 3, "бизнес": 1}),
        ],
    },
    {
        "stem": "Что сейчас важнее всего?",
        "options": [
            ("A", "Подтянуть информатику в школе", {"школа": 3}),
            ("B", "Сдать экзамен на нужный балл", {"огэ": 2, "егэ": 2}),
            ("C", "Научиться делать своё — игры, боты, проекты", {"питон": 3}),
            ("D", "Получить профессию или поменять работу", {"работа": 3}),
        ],
    },
    {
        "stem": "Что интереснее попробовать первым?",
        "options": [
            ("A", "Написать программу на Python", {"питон": 2}),
            ("B", "Собрать устройство и оживить его кодом", {"техника": 3}),
            ("C", "Разобрать задачи из экзамена", {"огэ": 1, "егэ": 1}),
            ("D", "Навести порядок в таблицах и данных", {"работа": 2}),
        ],
    },
    {
        "stem": "Сколько уже знаете про программирование?",
        "options": [
            ("A", "Совсем ничего, начинаем с нуля", {"школа": 2, "работа": 1}),
            ("B", "Пробовали сами, кое-что получалось", {"питон": 2}),
            ("C", "Пишете программы уверенно", {"егэ": 1, "техника": 1, "бизнес": 1}),
        ],
    },
    {
        "stem": "Что хочется получить в конце?",
        "options": [
            ("A", "Оценки в школе лучше", {"школа": 3}),
            ("B", "Нужный балл на экзамене", {"огэ": 2, "егэ": 2}),
            ("C", "Свой проект, который работает", {"питон": 2, "техника": 1}),
            ("D", "Новую работу или своё дело", {"работа": 2, "бизнес": 2}),
        ],
    },
    {
        "stem": "Если бы можно было сделать что-то одно — что именно?",
        "options": [
            ("A", "Telegram-бота", {"питон": 3}),
            ("B", "Устройство, которое работает само", {"техника": 4}),
            ("C", "Разобраться с заданиями экзамена до конца", {"огэ": 2, "егэ": 2}),
            ("D", "Запустить свой IT-продукт", {"бизнес": 3}),
        ],
    },
]


def _task_content(question: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "SC_Qw",
        "stem": question["stem"],
        "scales": SCALES,
        "options": [
            {"id": oid, "text": text, "scores": scores}
            for oid, text, scores in question["options"]
        ],
    }


def _solution_rules() -> Dict[str, Any]:
    return {"max_score": 1, "quiz": {"scales": SCALES, "mode": "single"}}


async def seed(conn: asyncpg.Connection, apply: bool) -> None:
    """Создать/обновить квиз. Все проверки — до записи, запись — одной транзакцией."""
    # ── 1. Машинное имя курсу ЕГЭ ──────────────────────────────────────────
    ege_uid = await conn.fetchval("SELECT course_uid FROM courses WHERE id = $1", EGE_COURSE_ID)
    if ege_uid is None:
        logger.info("курсу %s проставим course_uid=%s", EGE_COURSE_ID, EGE_COURSE_UID)
        if apply:
            # Условие `IS NULL` — страховка от гонки: чужое имя не перезаписываем.
            await conn.execute(
                "UPDATE courses SET course_uid = $1 WHERE id = $2 AND course_uid IS NULL",
                EGE_COURSE_UID,
                EGE_COURSE_ID,
            )
    elif ege_uid != EGE_COURSE_UID:
        logger.warning(
            "у курса %s уже есть имя %s — не трогаем, но правило 'егэ' будет вести на %s",
            EGE_COURSE_ID, ege_uid, ege_uid,
        )
        SCALE_TARGETS["егэ"] = ege_uid
    else:
        logger.info("курс %s уже имеет нужное имя", EGE_COURSE_ID)

    # ── 2. Курс-квиз ───────────────────────────────────────────────────────
    course_id = await conn.fetchval("SELECT id FROM courses WHERE course_uid = $1", QUIZ_UID)
    if course_id is None:
        logger.info("создаём курс-квиз %s", QUIZ_UID)
        if apply:
            course_id = await conn.fetchval(
                "INSERT INTO courses (title, description, access_level, course_uid, is_public_demo) "
                "VALUES ($1, $2, 'auto_check', $3, TRUE) RETURNING id",
                QUIZ_TITLE, QUIZ_DESCRIPTION, QUIZ_UID,
            )
    else:
        logger.info("курс-квиз уже есть: id=%s — обновим название и описание", course_id)
        if apply:
            await conn.execute(
                "UPDATE courses SET title = $1, description = $2, is_public_demo = TRUE "
                "WHERE id = $3",
                QUIZ_TITLE, QUIZ_DESCRIPTION, course_id,
            )

    if course_id is None:
        logger.info("разбор без записи: курса нет, дальше показать нечего — запустите с --apply")
        return

    difficulty_id = await conn.fetchval("SELECT id FROM difficulties ORDER BY id LIMIT 1")

    # ── 3. Вопросы ─────────────────────────────────────────────────────────
    for order, question in enumerate(QUESTIONS, start=1):
        external_uid = f"{QUIZ_UID}:q{order}"
        content = json.dumps(_task_content(question), ensure_ascii=False)
        rules = json.dumps(_solution_rules(), ensure_ascii=False)
        existing = await conn.fetchval("SELECT id FROM tasks WHERE external_uid = $1", external_uid)
        logger.info(
            "вопрос %s (%s): %s", order, "обновление" if existing else "создание", question["stem"]
        )
        if not apply:
            continue
        if existing:
            await conn.execute(
                "UPDATE tasks SET task_content = $1::jsonb, solution_rules = $2::jsonb, "
                "order_position = $3, is_active = TRUE, course_id = $4 WHERE id = $5",
                content, rules, order, course_id, existing,
            )
        else:
            await conn.execute(
                "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                "difficulty_id, solution_rules, order_position, is_active) "
                "VALUES ($1, 1, $2::jsonb, $3, $4, $5::jsonb, $6, TRUE)",
                external_uid, content, course_id, difficulty_id, rules, order,
            )

    # ── 4. Правила подбора ─────────────────────────────────────────────────
    for scale, target_uid in SCALE_TARGETS.items():
        target_exists = await conn.fetchval(
            "SELECT count(*) FROM courses WHERE course_uid = $1", target_uid
        )
        if not target_exists and not (scale == "егэ" and not apply):
            # Цель, которой нет, дала бы посетителю пустой экран вместо программы.
            logger.warning("шкала «%s»: курс %s не найден — правило пропущено", scale, target_uid)
            continue

        code = f"tsk053-quiz-{scale}"
        condition = json.dumps({"scale": scale, "mode": "argmax"}, ensure_ascii=False)
        existing = await conn.fetchval("SELECT id FROM assignment_rule WHERE code = $1", code)
        logger.info(
            "правило «%s» → %s (%s)", scale, target_uid, "обновление" if existing else "создание"
        )
        if not apply:
            continue
        if existing:
            await conn.execute(
                "UPDATE assignment_rule SET condition = $1::jsonb, target_course_uid = $2, "
                "course_id = $3, is_active = TRUE WHERE id = $4",
                condition, target_uid, course_id, existing,
            )
        else:
            await conn.execute(
                "INSERT INTO assignment_rule (code, title, course_id, trigger_event, condition, "
                "target_course_uid, is_active) "
                "VALUES ($1, $2, $3, 'quiz_scale', $4::jsonb, $5, TRUE)",
                code, f"Квиз подбора → {scale}", course_id, condition, target_uid,
            )

    # ── 5. Верификация ─────────────────────────────────────────────────────
    if apply:
        questions = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active "
            "AND task_content->>'type' = 'SC_Qw'",
            course_id,
        )
        rules = await conn.fetchval(
            "SELECT count(*) FROM assignment_rule WHERE course_id = $1 "
            "AND trigger_event = 'quiz_scale' AND is_active",
            course_id,
        )
        logger.info("готово: курс id=%s, вопросов %s, правил %s", course_id, questions, rules)
        if questions != len(QUESTIONS):
            logger.error("ожидалось вопросов %s, в базе %s", len(QUESTIONS), questions)
            sys.exit(1)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Наполнение квиза-лид-магнита (tsk-053)")
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
            # Одна транзакция на всё: половина квиза хуже, чем его отсутствие.
            async with conn.transaction():
                await seed(conn, apply=True)
        else:
            await seed(conn, apply=False)
            logger.info("это был разбор без записи; чтобы применить — добавьте --apply")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
