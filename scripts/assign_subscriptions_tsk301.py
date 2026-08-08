"""tsk-301 Фаза 5: присвоение тарифов действующим ученикам (прод).

**Раскладка задана списком, а не выведена во время запуска.** Это намеренно:
оператор принимает глазами конкретный список пар «человек → тариф», и записаться
должно ровно то, что он посмотрел. Правило, вычисляющее тариф на лету, изменило бы
результат при любом сдвиге данных между приёмкой и запуском — а сдвигаются они
постоянно (кто-то получил расписание, кого-то отчислили).

Источник раскладки: решение 2A брифа CEO-ревью + механическое правило «есть
начисление по группе «Базовый» → тариф base_legacy (старая цена)».

Протокол (`/db-check`, режим записи):
    python scripts/assign_subscriptions_tsk301.py            # сухой прогон
    DBCHECK_OK=1 python scripts/assign_subscriptions_tsk301.py --apply

Запускать на сервере под `app`:
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/assign_subscriptions_tsk301.py"'
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Iterable

from sqlalchemy import text

from app.db.session import async_session_factory

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk301.assign")

#: (student_id, ФИО как в базе на момент приёмки, код тарифа, основание).
#: ФИО здесь — не для поиска, а чтобы расхождение с базой было видно глазами:
#: если человек переименовался или id уехал, сверка это покажет до записи.
ASSIGNMENT: tuple[tuple[int, str, str, str], ...] = (
    # ── Сотрудники и свои: тариф Test (решение 2A) ──────────────────────────
    (2,    "Виктор Комлев",                  "test", "сотрудник"),
    (142,  "Комлев Виктор",                  "test", "сотрудник"),
    (3,    "Серебрякова Екатерина",          "test", "сотрудник"),
    (4495, "Коротких Светлана",              "test", "сотрудник"),
    (4496, "Ладесов Кирилл",                 "test", "сотрудник"),
    (4498, "Пряхин Михаил",                  "test", "решение 2A"),
    (4555, "Борисов Тимофей Александрович",  "test", "решение 2A"),
    (4556, "Сопова Ольга",                   "test", "решение 2A"),
    (4554, "Иван Крынин",                    "test", "решение 2A"),
    (4541, "Тестовский Тестер",              "test", "приёмка 2026-08-08"),

    # ── Без начислений, но учатся: тариф Base (решение 2A) ──────────────────
    (4499, "Мурзагулов Достан Галимжанович", "base", "решение 2A"),
    (4527, "Максим Сундуков",                "base", "решение 2A"),
    # Оператор на приёмке: «Терехов живой ученик». Тариф — по тому же образцу,
    # что и остальные учащиеся без начислений (решение 2A).
    (4510, "Терехов Илья",                   "base", "приёмка 2026-08-08: живой ученик"),

    # ── Выпускники: доступ к материалам без преподавателя и ИИ ──────────────
    # Сесюк переведена сюда на приёмке: решение 2A назначало ей Base, но там же
    # она приведена как «закончила обучение», а тариф «Выпускник» (решение 3B)
    # заведён ровно под этот случай.
    (4521, "Юлия Сесюк",                     "alumni", "приёмка 2026-08-08"),
    (4523, "Кирилл Несскофи",                "alumni", "приёмка 2026-08-08"),

    # ── Действующие плательщики: Base (старая цена) ─────────────────────────
    # Механическое правило: есть начисление по группе «Базовый» (id 1) →
    # base_legacy. Цену им намеренно не пересматриваем (§7 брифа).
    (4497, "Рита Харькова",                    "base_legacy", "есть начисление"),
    (4500, "Захар Грязнов",                    "base_legacy", "есть начисление"),
    (4501, "Денис Ильин",                      "base_legacy", "есть начисление"),
    (4502, "Емельяненко Софья Артемовна",      "base_legacy", "есть начисление"),
    (4503, "Крук Анастасия",                   "base_legacy", "есть начисление"),
    (4504, "Гальцов Дмитрий",                  "base_legacy", "есть начисление"),
    (4505, "Мамедов Джемаль Рафаил оглы",      "base_legacy", "есть начисление"),
    (4506, "Гребнева Полина",                  "base_legacy", "есть начисление"),
    (4507, "Селин Егор",                       "base_legacy", "есть начисление"),
    (4508, "Газаров Богдан Эрикович",          "base_legacy", "есть начисление"),
    (4509, "Миша Поскребышев",                 "base_legacy", "есть начисление"),
    (4511, "Влад Литовкин",                    "base_legacy", "есть начисление"),
    (4512, "Глеб Анфалов",                     "base_legacy", "есть начисление"),
    (4513, "Илья Михайленко",                  "base_legacy", "есть начисление"),
    (4514, "Галимов Эмиль Альбертович",        "base_legacy", "есть начисление"),
    (4515, "Лашков Андрей",                    "base_legacy", "есть начисление"),
    (4516, "Костенков Матвей Алексеевич",      "base_legacy", "есть начисление"),
    (4517, "Артемий Нуженко",                  "base_legacy", "есть начисление"),
    (4518, "Иван Мочалов",                     "base_legacy", "есть начисление"),
    (4519, "Рахимжанов Вадим Маратович",       "base_legacy", "есть начисление"),
    (4520, "Денис Белов",                      "base_legacy", "есть начисление"),
    (4522, "Курунов Кирилл Владимирович",      "base_legacy", "есть начисление"),
    (4524, "Якунина Елена",                    "base_legacy", "есть начисление"),
    (4525, "Владимир Грабовский",              "base_legacy", "есть начисление"),
    (4526, "Астафьев Данил Алексеевич",        "base_legacy", "есть начисление"),
    (4530, "Андрей Залетов",                   "base_legacy", "есть начисление"),
    (4533, "Ангелина Аникина",                 "base_legacy", "есть начисление"),
    (4536, "Елисей Ястребцов",                 "base_legacy", "есть начисление"),
    (4538, "Оля Омельченко",                   "base_legacy", "есть начисление"),
    (4539, "Кузнецкий Кирилл Александрович",   "base_legacy", "есть начисление"),
    (4540, "Илья Рвачёв",                      "base_legacy", "есть начисление"),
    (4543, "Четверенко Илья Никитич",          "base_legacy", "есть начисление"),
    (4547, "Ратмир Дущенко",                   "base_legacy", "есть начисление"),
    (4548, "Дегтярев Лазарь Сергеевич",        "base_legacy", "есть начисление"),
    (4549, "Ундасынова Василина Евгеньевна",   "base_legacy", "есть начисление"),
    (4551, "Шестаев Владислав Сергеевич",      "base_legacy", "есть начисление"),
    (4552, "Королева Екатерина",               "base_legacy", "есть начисление"),
)

#: Ученики без тарифа. Пусто: приёмка 2026-08-08 закрыла всех троих, которых не
#: покрывал бриф. Список оставлен как место для будущих таких случаев — молча
#: пропущенный человек это человек без прав, и обнаружится он жалобой, а не
#: отчётом, поэтому пропуск должен быть виден в выводе.
UNCOVERED: tuple[tuple[int, str, str], ...] = ()

#: Расхождение в данных, к тарифу отношения не имеющее: у Терехова (4510) есть
#: занятие в расписании, но нет ни платного курса, ни начисления — то есть он
#: ходит, а денег с него не берут. Тариф это не чинит; разбирается отдельно.
DATA_DISCREPANCIES: tuple[tuple[int, str, str], ...] = (
    (4510, "Терехов Илья",
     "есть активное занятие, но нет зачисления на платный курс → начисление не создаётся"),
)


async def _fetch_state(db) -> dict[int, dict]:
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id, u.full_name, u.is_active,
                       (SELECT count(*) FROM student_subscription s
                         WHERE s.student_id = u.id AND s.ends_on IS NULL) AS active_subs
                  FROM users u
                 WHERE u.id = ANY(:ids)
                """
            ),
            {"ids": [row[0] for row in ASSIGNMENT]},
        )
    ).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def _preflight(state: dict[int, dict]) -> list[str]:
    """Расхождения, при которых записывать нельзя."""
    problems: list[str] = []
    for student_id, full_name, plan_code, _reason in ASSIGNMENT:
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
        if actual["active_subs"]:
            problems.append(
                f"{student_id} ({full_name}): подписка уже есть — "
                f"повторное присвоение не задумано"
            )
    return problems


async def _plans(db) -> dict[str, tuple[int, int | None]]:
    rows = (
        await db.execute(text("SELECT code, id, pricing_group_id FROM subscription_plan"))
    ).all()
    return {r.code: (int(r.id), r.pricing_group_id) for r in rows}


async def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Присвоение тарифов (tsk-301 Фаза 5)")
    parser.add_argument(
        "--apply", action="store_true",
        help="выполнить запись; без флага — только сухой прогон",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    async with async_session_factory() as db:
        plans = await _plans(db)
        missing_plans = {code for _, _, code, _ in ASSIGNMENT} - set(plans)
        if missing_plans:
            logger.error("Нет таких тарифов в базе: %s", sorted(missing_plans))
            return 2

        state = await _fetch_state(db)
        problems = _preflight(state)

        logger.info("=== Раскладка (%d человек) ===", len(ASSIGNMENT))
        by_plan: dict[str, int] = {}
        for _sid, _name, code, _reason in ASSIGNMENT:
            by_plan[code] = by_plan.get(code, 0) + 1
        for code in sorted(by_plan):
            group_id = plans[code][1]
            logger.info(
                "  %-12s %2d чел.  тарифная группа: %s",
                code, by_plan[code], group_id if group_id is not None else "нет (денег нет)",
            )

        if UNCOVERED:
            logger.warning("=== Вне раскладки (%d человек, НЕ трогаем) ===", len(UNCOVERED))
            for sid, name, why in UNCOVERED:
                logger.warning("  %s %s — %s", sid, name, why)
        else:
            logger.info("Вне раскладки никого нет.")

        for sid, name, why in DATA_DISCREPANCIES:
            logger.warning("Расхождение в данных: %s %s — %s", sid, name, why)

        if problems:
            logger.error("=== Расхождения, запись запрещена (%d) ===", len(problems))
            for problem in problems:
                logger.error("  %s", problem)
            return 3

        logger.info("Расхождений нет.")
        if not args.apply:
            logger.info("Сухой прогон. Для записи: --apply (с префиксом DBCHECK_OK=1).")
            return 0

        written = 0
        async with db.begin():
            for student_id, full_name, plan_code, reason in ASSIGNMENT:
                plan_id, group_id = plans[plan_code]
                await db.execute(
                    text(
                        "INSERT INTO student_subscription "
                        "  (student_id, plan_id, pricing_group_id, starts_on, reason) "
                        "VALUES (:s, :p, :g, CURRENT_DATE, :r)"
                    ),
                    {"s": student_id, "p": plan_id, "g": group_id,
                     "r": f"tsk-301 Фаза 5: {reason}"},
                )
                written += 1

        # Поштучная верификация всего затронутого множества, не агрегатом:
        # совпадение количества и суммы прячет перепутанные пары (урок tsk-317).
        wrong: list[str] = []
        for student_id, full_name, plan_code, _reason in ASSIGNMENT:
            row = (
                await db.execute(
                    text(
                        "SELECT p.code, s.pricing_group_id FROM student_subscription s "
                        "  JOIN subscription_plan p ON p.id = s.plan_id "
                        " WHERE s.student_id = :s AND s.ends_on IS NULL"
                    ),
                    {"s": student_id},
                )
            ).first()
            if row is None:
                wrong.append(f"{student_id} ({full_name}): подписка не создана")
            elif row.code != plan_code:
                wrong.append(
                    f"{student_id} ({full_name}): тариф {row.code}, ожидался {plan_code}"
                )
            elif row.pricing_group_id != plans[plan_code][1]:
                wrong.append(
                    f"{student_id} ({full_name}): группа {row.pricing_group_id}, "
                    f"ожидалась {plans[plan_code][1]}"
                )
        if wrong:
            logger.error("=== ВЕРИФИКАЦИЯ НЕ ПРОШЛА (%d) ===", len(wrong))
            for item in wrong:
                logger.error("  %s", item)
            return 4

        logger.info("Записано и поштучно проверено: %d из %d", written, len(ASSIGNMENT))
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
