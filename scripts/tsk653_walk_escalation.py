# scripts/tsk653_walk_escalation.py
"""
tsk-653, шаг 1-2: провести признак ИИ-авторства по контуру эскалации руками.

**Зачем руками.** Контур «сигнал → преподаватель → методист → мини-курс» уже
построен ([[tsk-572]] фаза 7, [[tsk-231]], [[tsk-631]]), но по нему ни разу не
проходил сигнал ЭТОГО рода. Автоматизировать путь, которым никто не ходил, —
верный способ зафиксировать в коде пороги и формулировки, которые окажутся не
теми. Поэтому сперва один живой проход, и только потом триггер в коде.

**Почему существующий датчик эту ученицу не поймает никогда.** Он считает долю
ОШИБОК (`learning_gap_signals_service.find_student_gaps`: не меньше 8 сдач по
курсу и не меньше 50 % неверных). У ученицы 4538 неверных нет вовсе — все
работы приняты преподавателем. Ровно поэтому дыра и не видна из цифр: ученик,
сдающий чужое, выглядит как отличник.

**Почему сигнал вешается на КОРНЕВОЙ курс.** Её 12 разобранных работ лежат в 12
разных подкурсах по одной в каждом, а сигнал ключуется парой «курс + ученик».
По подкурсам вышло бы 11 отдельных карточек, каждая по одной работе, — это не
сигнал, а шум. По корню (964 «Информатика. 8 класс») выходит одна карточка,
и охват совпадает с тем, на что методист может собрать мини-курс.

**Что этот скрипт НЕ делает.** Не меняет ни баллов, ни зачётов, не пишет ничего
ученице и не создаёт ей курсов. Он заводит один сигнал и передаёт его методисту
теми же функциями сервиса, которыми это делает кнопка в кабинете, — чтобы
пройденный путь был настоящим, а не имитацией через прямой INSERT.

Запуск (по умолчанию — предпросмотр):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk653_walk_escalation.py
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk653_walk_escalation.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict

from sqlalchemy import text

from app.db.session import async_session_factory
from app.services.learning_gap_signals_service import acknowledge_signal, upsert_signal

logger = logging.getLogger("tsk653.walk")

#: Кого и по какому курсу. Корень, а не подкурс — см. докстринг.
STUDENT_ID = 4538
ROOT_COURSE_ID = 964
#: От чьего имени идёт эскалация. Аккаунт оператора (роли teacher+methodist+admin):
#: подписывать решение именем преподавателя, который его не принимал, нельзя.
TEACHER_ID = 2

#: Текст, который методист прочитает в карточке. Он важнее цифры и написан так,
#: чтобы из него нельзя было сделать вывод «ученица списывает»: признак —
#: эвристика, а нужное действие от этого не зависит. Работа, сделанная машиной,
#: не говорит, что ученица темы НЕ знает; она говорит, что мы этого НЕ ЗНАЕМ, —
#: и закрывается это повторением, а не разбирательством.
ESCALATION_COMMENT = (
    "Развёрнутые работы по курсу не дают судить о знаниях: у 11 из 12 машинный "
    "признак ИИ-авторства, в 9 из них — механические следы вставки из окна чата. "
    "Это не доказательство и не обвинение. Прошу мини-курс повторения по главам "
    "2-4 (логика, алгоритмизация, начала Python), чтобы темы можно было закрыть "
    "по-настоящему — без разговора о списывании."
)

#: Сколько работ разобрано и сколько из них с признаком. Считается запросом, а не
#: вписывается руками: цифра в карточке обязана сходиться с базой.
_STATS_SQL = """
    SELECT count(*) AS reviewed,
           count(*) FILTER (
               WHERE jsonb_array_length(coalesce(tr.code_review->'signals', '[]'::jsonb)) > 0
                  OR tr.code_review->'ai_authorship'->>'verdict' = 'ai_likely'
           ) AS flagged,
           count(*) FILTER (
               WHERE jsonb_array_length(coalesce(tr.code_review->'signals', '[]'::jsonb)) > 0
           ) AS with_paste_traces,
           count(*) FILTER (WHERE tr.is_correct) AS accepted
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.user_id = :sid
      AND t.task_content->>'type' = 'TA'
      AND tr.code_review->>'status' = 'done'
"""

#: Открытый сигнал по этой паре уже есть? Частичный уникальный индекс всё равно
#: не даст завести второй, но молча пропущенная вставка выглядела бы как успех.
_EXISTING_SQL = """
    SELECT id, status, escalated_at
    FROM learning_gap_signal
    WHERE course_id = :cid AND student_id = :sid
    ORDER BY created_at DESC LIMIT 1
"""


async def collect() -> Dict[str, Any]:
    """Что сейчас в базе. Ничего не меняет."""
    async with async_session_factory() as db:
        stats = (await db.execute(text(_STATS_SQL), {"sid": STUDENT_ID})).mappings().one()
        existing = (await db.execute(
            text(_EXISTING_SQL), {"cid": ROOT_COURSE_ID, "sid": STUDENT_ID}
        )).mappings().first()
        course = (await db.execute(
            text("SELECT title FROM courses WHERE id = :cid"), {"cid": ROOT_COURSE_ID}
        )).scalar_one()
        student = (await db.execute(
            text("SELECT full_name FROM users WHERE id = :sid"), {"sid": STUDENT_ID}
        )).scalar_one()
    return {
        "stats": dict(stats),
        "existing": dict(existing) if existing else None,
        "course": course,
        "student": student,
    }


async def apply(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Завести сигнал и передать методисту — теми же функциями, что и кнопка."""
    async with async_session_factory() as db:
        signal_id = await upsert_signal(
            db,
            course_id=ROOT_COURSE_ID,
            student_id=STUDENT_ID,
            submissions=int(stats["reviewed"]),
            students=1,
            # НЕ доля работ с признаком, а именно доля ОШИБОК — то, что означает
            # это поле и что покажет карточка («N% ошибок»). У неё ошибок нет, и
            # написать сюда 92 % значило бы соврать методисту в цифре, которую он
            # читает первой. Смысл сигнала несёт комментарий и `meta`.
            # Это же — первая находка прохода: у карточки нет места для сигнала,
            # который не про ошибки.
            wrong_rate=0.0,
        )
        if signal_id is None:
            return {"created": False, "signal_id": None}

        # `meta` до сих пор не использовалась ни одним сигналом (на 2026-08-23
        # во всех 64 строках NULL). Кладём сюда основание — иначе через месяц
        # по строке нельзя будет понять, откуда она взялась.
        await db.execute(
            text("UPDATE learning_gap_signal SET meta = CAST(:m AS jsonb) WHERE id = :sid"),
            {"m": json.dumps({
                "reason": "ai_authorship",
                "task_type": "TA",
                "reviewed": int(stats["reviewed"]),
                "flagged": int(stats["flagged"]),
                "with_paste_traces": int(stats["with_paste_traces"]),
                "source": "tsk-646",
            }, ensure_ascii=False), "sid": signal_id},
        )
        await db.commit()

        escalated = await acknowledge_signal(
            db, signal_id=signal_id, teacher_id=TEACHER_ID,
            comment=ESCALATION_COMMENT, escalate=True,
        )
    return {"created": True, "signal_id": signal_id, "escalated": escalated}


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-653: живой проход контура эскалации")
    parser.add_argument("--apply", action="store_true", help="завести сигнал и эскалировать")
    args = parser.parse_args()

    report = await collect()
    s = report["stats"]
    print(f"Ученица: {report['student']} (id {STUDENT_ID})")
    print(f"Курс:    {report['course']} (id {ROOT_COURSE_ID}, корневой)")
    print(f"Развёрнутых работ разобрано: {s['reviewed']}, из них принято: {s['accepted']}")
    print(f"С признаком ИИ-авторства:    {s['flagged']}")
    print(f"Из них со следами вставки:   {s['with_paste_traces']}")
    print(f"Ошибок (то, что видит нынешний датчик): {int(s['reviewed']) - int(s['accepted'])}")
    if report["existing"]:
        print(f"\nСигнал по этой паре уже есть: #{report['existing']['id']} "
              f"({report['existing']['status']})")

    if not args.apply:
        print("\nЭто предпросмотр. Для записи добавьте --apply")
        return 0

    result = await apply(s)
    if not result["created"]:
        print("\nСигнал НЕ заведён: открытый по этой паре уже существует.")
        return 1
    print(f"\nСигнал #{result['signal_id']} заведён и "
          f"{'передан методисту' if result['escalated'] else 'НЕ передан (проверьте статус)'}.")
    print("Баллы, зачёты и курсы ученицы не менялись.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
