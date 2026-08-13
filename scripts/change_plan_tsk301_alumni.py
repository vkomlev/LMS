"""tsk-301: перевод выпускников на тариф «Выпускник» (прод).

Фаза 5 присвоила тарифы разом (`assign_subscriptions_tsk301.py`, 2026-08-08
10:48). Разбор tsk-596 в тот же день показал расхождение: Мурзагулов (4499)
получил `base` — «живой» тариф с тарифной группой, — хотя привязка к слоту у
него снята, будущих занятий нет, единственное занятие 29.07 отмечено пропуском.
Оператор 2026-08-08: «Сесюк, Мурзагулов закончили (есть соответствующий тариф)».
Сесюк (4521) на приёмке перевели, Мурзагулова пропустили — этот скрипт закрывает
пропуск.

Раскладка задана списком, а не выведена во время запуска, — по той же причине,
что в Фазе 5: записаться должно ровно то, что оператор посмотрел глазами.

Смена идёт через штатный путь `subscription_service.change_plan` (закрыть строку
+ открыть новую), а не прямым UPDATE: история тарифов держится строками.

Денег правка не двигает: у 4499 нет ни одного начисления и ни одного платежа, а
у «Выпускника» тарифной группы нет вовсе — начисления по нему не создаются.
Проверяется до и после записи, а не предполагается.

Протокол (`/db-check`, режим записи):
    python scripts/change_plan_tsk301_alumni.py            # сухой прогон
    DBCHECK_OK=1 python scripts/change_plan_tsk301_alumni.py --apply

Запускать на сервере под `app`:
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/change_plan_tsk301_alumni.py"'
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Iterable

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.services import subscription_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk301.change_plan")

#: (student_id, ФИО как в базе, тариф ИЗ которого, тариф В который, основание).
#: Исходный тариф в списке обязателен: если человека уже перевели вручную или
#: автоправило подняло его обратно, повторный перевод затрёт чужое решение.
CHANGES: tuple[tuple[int, str, str, str, str], ...] = (
    (
        4499,
        "Мурзагулов Достан Галимжанович",
        "base",
        "alumni",
        "закончил обучение (решение оператора 2026-08-08, разбор tsk-596)",
    ),
)


async def _state(db) -> dict[int, dict]:
    """Текущее состояние по каждому из списка: тариф, деньги, расписание."""
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id,
                       u.full_name,
                       u.is_active,
                       p.code                       AS plan_code,
                       s.id                         AS subscription_id,
                       s.pricing_group_id,
                       s.starts_on,
                       (SELECT count(*) FROM student_subscription s2
                         WHERE s2.student_id = u.id AND s2.ends_on IS NULL) AS active_subs,
                       (SELECT count(*) FROM student_monthly_charge ch
                         WHERE ch.student_id = u.id)                        AS charges,
                       (SELECT count(*) FROM student_payment pay
                         WHERE pay.student_id = u.id)                       AS payments,
                       (SELECT count(*) FROM lesson_slot_student lss
                          JOIN lesson_slot ls ON ls.id = lss.slot_id
                         WHERE lss.student_id = u.id
                           AND lss.is_active AND ls.is_active)              AS active_slots,
                       (SELECT count(*) FROM lesson_occurrence_participant lop
                          JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
                         WHERE lop.student_id = u.id
                           AND lo.scheduled_at >= now())                    AS future_lessons
                  FROM users u
                  LEFT JOIN student_subscription s
                         ON s.student_id = u.id AND s.ends_on IS NULL
                  LEFT JOIN subscription_plan p ON p.id = s.plan_id
                 WHERE u.id = ANY(:ids)
                """
            ),
            {"ids": [row[0] for row in CHANGES]},
        )
    ).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def _preflight(state: dict[int, dict]) -> list[str]:
    """Расхождения, при которых записывать нельзя."""
    problems: list[str] = []
    for student_id, full_name, plan_from, _plan_to, _reason in CHANGES:
        actual = state.get(student_id)
        if actual is None:
            problems.append(f"{student_id} ({full_name}): пользователя нет в базе")
            continue
        if not actual["is_active"]:
            problems.append(f"{student_id} ({full_name}): учётка неактивна")
        if actual["full_name"] != full_name:
            problems.append(
                f"{student_id}: в базе «{actual['full_name']}», "
                f"в раскладке «{full_name}» — список устарел"
            )
        if actual["active_subs"] != 1:
            problems.append(
                f"{student_id} ({full_name}): действующих подписок "
                f"{actual['active_subs']}, ожидалась ровно одна"
            )
        elif actual["plan_code"] != plan_from:
            problems.append(
                f"{student_id} ({full_name}): сейчас тариф «{actual['plan_code']}», "
                f"в раскладке исходным указан «{plan_from}» — решение уже меняли"
            )
        # Деньги реального человека: если начисления или платежи есть, перевод
        # перестаёт быть безобидным и решает его оператор, а не скрипт.
        if actual["charges"] or actual["payments"]:
            problems.append(
                f"{student_id} ({full_name}): есть начисления ({actual['charges']}) "
                f"или платежи ({actual['payments']}) — перевод затрагивает деньги, "
                f"нужно решение оператора"
            )
        # «Закончил обучение» и «ходит на занятия» — противоречие. Тариф без
        # занятий отбирает у человека преподавателя и наставника.
        if actual["active_slots"] or actual["future_lessons"]:
            problems.append(
                f"{student_id} ({full_name}): активных слотов "
                f"{actual['active_slots']}, будущих занятий {actual['future_lessons']} "
                f"— человек ещё учится, «Выпускник» отберёт у него права"
            )
    return problems


def _log_state(title: str, state: dict[int, dict]) -> None:
    logger.info("=== %s ===", title)
    for student_id, full_name, _plan_from, _plan_to, _reason in CHANGES:
        actual = state.get(student_id)
        if actual is None:
            logger.info("  %s %s — в базе нет", student_id, full_name)
            continue
        logger.info(
            "  %s %-38s тариф %-12s группа %-4s начислений %s, платежей %s, "
            "слотов %s, будущих занятий %s",
            student_id,
            full_name,
            actual["plan_code"],
            actual["pricing_group_id"] if actual["pricing_group_id"] is not None else "нет",
            actual["charges"],
            actual["payments"],
            actual["active_slots"],
            actual["future_lessons"],
        )


async def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перевод выпускников (tsk-301)")
    parser.add_argument(
        "--apply", action="store_true",
        help="выполнить запись; без флага — только сухой прогон",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    async with async_session_factory() as db:
        before = await _state(db)
        _log_state("Состояние ДО", before)

        for student_id, full_name, plan_from, plan_to, reason in CHANGES:
            logger.info(
                "Перевод: %s %s — %s → %s (%s)",
                student_id, full_name, plan_from, plan_to, reason,
            )

        problems = _preflight(before)
        if problems:
            logger.error("=== Расхождения, запись запрещена (%d) ===", len(problems))
            for problem in problems:
                logger.error("  %s", problem)
            return 3

        logger.info("Расхождений нет.")
        if not args.apply:
            logger.info("Сухой прогон. Для записи: --apply (с префиксом DBCHECK_OK=1).")
            return 0

        for student_id, full_name, _plan_from, plan_to, reason in CHANGES:
            changed = await subscription_service.change_plan(
                db, student_id, plan_to, reason=f"tsk-301: {reason}"
            )
            if not changed:
                logger.error(
                    "%s (%s): смена на «%s» не выполнена — откатываю всё",
                    student_id, full_name, plan_to,
                )
                await db.rollback()
                return 4
        await db.commit()

        # Верификация поштучная, а не агрегатом: совпадение количества прячет
        # перепутанные пары (урок tsk-317).
        after = await _state(db)
        _log_state("Состояние ПОСЛЕ", after)

        wrong: list[str] = []
        for student_id, full_name, _plan_from, plan_to, _reason in CHANGES:
            actual = after.get(student_id)
            if actual is None or actual["active_subs"] != 1:
                wrong.append(f"{student_id} ({full_name}): действующая подписка не одна")
                continue
            if actual["plan_code"] != plan_to:
                wrong.append(
                    f"{student_id} ({full_name}): тариф «{actual['plan_code']}», "
                    f"ожидался «{plan_to}»"
                )
            if actual["charges"] != before[student_id]["charges"]:
                wrong.append(
                    f"{student_id} ({full_name}): начислений было "
                    f"{before[student_id]['charges']}, стало {actual['charges']} — "
                    f"перевод тронул деньги"
                )
            history = (
                await db.execute(
                    text(
                        "SELECT p.code, s.ends_on FROM student_subscription s "
                        "  JOIN subscription_plan p ON p.id = s.plan_id "
                        " WHERE s.student_id = :s AND s.ends_on IS NOT NULL "
                        " ORDER BY s.id DESC LIMIT 1"
                    ),
                    {"s": student_id},
                )
            ).first()
            if history is None:
                wrong.append(
                    f"{student_id} ({full_name}): прежняя подписка не закрыта — "
                    f"история потеряна"
                )

        if wrong:
            logger.error("=== ВЕРИФИКАЦИЯ НЕ ПРОШЛА (%d) ===", len(wrong))
            for item in wrong:
                logger.error("  %s", item)
            return 5

        logger.info("Переведено и поштучно проверено: %d", len(CHANGES))
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
