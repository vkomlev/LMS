"""tsk-832: снять третий слот у троих учеников и пересчитать сентябрь (прод).

Трое учеников осенью получили по три занятия в неделю вместо двух — и ступень
«Базового» подняла им сентябрь с 5 500 до 7 750 ₽. Третий слот приехал не из
сбоя вёрстки: 30–31.08 все трое сами указали в форме пожеланий «3 занятия в
неделю», а оператор 31.08 развёрстывал строго по ним (у остальных 34 учеников
число слотов совпадает с пожеланием один в один). Поэтому правится и форма:
иначе третий слот вернётся при следующей вёрстке.

Решения оператора 2026-09-08 (разведка и обоснование — `tsk-832` в трекере Root):

* **Газаров Богдан (4508)** — снять вт 16:00 (слот 27). Факт лишнего слота не
  показывал: 07.09 он был на ОБОИХ понедельниках (16:00 и 17:00 подряд —
  единственный на школу случай двух слотов в один день) и пропустил
  единственный вторник 01.09. Решение оператора — снять вторник, оставив
  сдвоенный понедельник.
* **Грабовский Владимир (4560)** — снять ср 16:00 (слот 31). Единственный слот,
  куда он не пришёл, и в тот же день 02.09 его в 17:05 добавили в занятие
  ср 17:00, где он и был. Весь август он ходил только на вечерние 17:00 и 18:00,
  игнорируя утренние 11:00 полностью (0 явок из 11).
* **Рахимжанов Вадим (4519)** — снять ср 16:00 (слот 31). Единственный пропуск;
  пн и чт посетил. Деньги у него и так верны — персональная цена 5 500 ₽
  перекрывает ступень, — правка нужна только расписанию.

Сумма сентября сведена ДО правки (порядок tsk-673): при снятии ЛЮБОГО из трёх
слотов у каждого выходит 5 500 ₽ — вычетов нет (`not_started`, `missing`,
`break` по нулям), доля месяца не режется. Август у всех троих закрыт, оплачен
по 5 500 ₽ и не пересчитывается: `recalculate_open_months_for_student` берёт
только текущий и будущие месяцы (tsk-756).

Почему пожелания правятся сырым SQL, а не `schedule_preference_service`:
валидатор сервиса принимает только часы, где занятие ЕСТЬ, а у Газарова в
анкете с 31.08 висит сб 09:00 — час, которого в сетке нет вовсе. Сохранение
через сервис упало бы на нём и потребовало выкинуть часы, трогать которые
оператор не просил. Здесь меняется ровно две вещи: число занятий в неделю и час
снятого слота; снимок уходит в `student_schedule_preference_revision`, как и
при обычном сохранении.

Протокол (`/db-check`, режим записи):
    python scripts/fix_third_slot_tsk832.py            # сухой прогон
    DBCHECK_OK=1 python scripts/fix_third_slot_tsk832.py --apply

Запускать на сервере под `app` (R-009 operator-runbook):
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/fix_third_slot_tsk832.py"'

Проверка после записи — поштучная, по каждому ученику отдельно, а не агрегатом
(урок `feedback_backfill_verify_every_row`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, time
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.services import charge_service, lesson_calendar_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk832.fix")

PERIOD = date(2026, 9, 1)

#: Кто правит. Оператор школы — он же завёл эти привязки 31.08.
OPERATOR_ID = 2

#: (student_id, ФИО как в базе на момент приёмки, slot_id, день недели, час).
#: ФИО и время — не для поиска, а чтобы расхождение было видно глазами до
#: записи: слот выбирал человек, а сверяет его строку скрипт.
DROP: tuple[tuple[int, str, int, int, time], ...] = (
    (4508, "Газаров Богдан Эрикович", 27, 1, time(16, 0)),
    (4560, "Грабовский Владимир Антонович", 31, 2, time(16, 0)),
    (4519, "Рахимжанов Вадим Маратович", 31, 2, time(16, 0)),
)

#: Сколько занятий в неделю ставим в анкете вместо тройки.
LESSONS_PER_WEEK = 2

#: Ожидаемый итог сентября после правки, в копейках. У Рахимжанова сумма и до
#: правки была верна — персональная цена перекрывает ступень.
EXPECTED_SEPTEMBER_MINOR = 550000


async def _slot_row(db, slot_id: int) -> dict:
    """Слот с его временем — сверка того, что снимаем именно названное."""
    row = (
        await db.execute(
            text(
                "SELECT id, weekday, start_time, is_active FROM lesson_slot "
                "WHERE id = :id"
            ),
            {"id": slot_id},
        )
    ).one()
    return {"id": row.id, "weekday": row.weekday, "start_time": row.start_time}


async def show_state(db, title: str) -> None:
    """Состояние всех троих одним куском: слоты, сентябрь, анкета."""
    logger.info("\n===== %s =====", title)
    for student_id, name, *_ in DROP:
        slots = (
            await db.execute(
                text(
                    """
                    SELECT ls.id, ls.weekday, ls.start_time
                      FROM lesson_slot_student lss
                      JOIN lesson_slot ls ON ls.id = lss.slot_id
                     WHERE lss.student_id = :s AND lss.is_active AND ls.is_active
                     ORDER BY ls.weekday, ls.start_time
                    """
                ),
                {"s": student_id},
            )
        ).all()
        charge = (
            await db.execute(
                text(
                    "SELECT calculated_minor, manual_minor, expected_lessons, "
                    "       missing_lessons, not_started_lessons, status "
                    "  FROM student_monthly_charge "
                    " WHERE student_id = :s AND period = :p"
                ),
                {"s": student_id, "p": PERIOD},
            )
        ).first()
        pref = (
            await db.execute(
                text(
                    "SELECT lessons_per_week, "
                    "       (SELECT count(*) FROM student_schedule_preference_hour h "
                    "         WHERE h.preference_id = p.id) AS hours "
                    "  FROM student_schedule_preference p WHERE p.student_id = :s"
                ),
                {"s": student_id},
            )
        ).first()
        logger.info(
            "%s (%s): слотов %d — %s",
            name,
            student_id,
            len(slots),
            ", ".join(f"{r.weekday}/{r.start_time:%H:%M} (id {r.id})" for r in slots),
        )
        if charge is not None:
            logger.info(
                "    сентябрь: расчёт %d руб, ручная %s, занятий %d, "
                "нет занятия %d, до прихода %d, статус %s",
                charge.calculated_minor // 100,
                "нет" if charge.manual_minor is None else f"{charge.manual_minor // 100} руб",
                charge.expected_lessons,
                charge.missing_lessons,
                charge.not_started_lessons,
                charge.status,
            )
        if pref is not None:
            logger.info(
                "    анкета: %d занятий в неделю, часов выбрано %d",
                pref.lessons_per_week,
                pref.hours,
            )


async def fix_preference(db, student_id: int, weekday: int, start_time: time) -> None:
    """Опустить анкету до двух занятий и убрать час снятого слота.

    Снимок уходит в историю ревизий тем же порядком, что и обычное сохранение
    из кабинета, — иначе правка сотрудника выпала бы из истории, по которой
    разбирают вёрстку.
    """
    pref_id = (
        await db.execute(
            text(
                "UPDATE student_schedule_preference "
                "   SET lessons_per_week = :lpw, updated_by = :by, updated_at = now() "
                " WHERE student_id = :s RETURNING id"
            ),
            {"lpw": LESSONS_PER_WEEK, "by": OPERATOR_ID, "s": student_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "DELETE FROM student_schedule_preference_hour "
            " WHERE preference_id = :pid AND weekday = :wd AND start_time = :st"
        ),
        {"pid": pref_id, "wd": weekday, "st": start_time},
    )
    hours = (
        await db.execute(
            text(
                "SELECT weekday, start_time, kind "
                "  FROM student_schedule_preference_hour "
                " WHERE preference_id = :pid ORDER BY weekday, start_time"
            ),
            {"pid": pref_id},
        )
    ).all()
    snapshot = [
        {"weekday": h.weekday, "start_time": h.start_time.strftime("%H:%M"), "kind": h.kind}
        for h in hours
    ]
    await db.execute(
        text(
            "INSERT INTO student_schedule_preference_revision "
            "       (student_id, lessons_per_week, hours, comment, source, changed_by) "
            "VALUES (:s, :lpw, CAST(:hours AS jsonb), :comment, 'staff', :by)"
        ),
        {
            "s": student_id,
            "lpw": LESSONS_PER_WEEK,
            "hours": json.dumps(snapshot, ensure_ascii=False),
            "comment": "tsk-832: возврат к двум занятиям в неделю по решению оператора",
            "by": OPERATOR_ID,
        },
    )


async def verify(db) -> bool:
    """Поштучная проверка каждого ученика. Агрегат сходится и при двух ошибках."""
    ok = True
    logger.info("\n===== Проверка после записи =====")
    for student_id, name, slot_id, weekday, start_time in DROP:
        slots = (
            await db.execute(
                text(
                    "SELECT count(*) FROM lesson_slot_student lss "
                    "  JOIN lesson_slot ls ON ls.id = lss.slot_id "
                    " WHERE lss.student_id = :s AND lss.is_active AND ls.is_active"
                ),
                {"s": student_id},
            )
        ).scalar_one()
        still_here = (
            await db.execute(
                text(
                    "SELECT is_active FROM lesson_slot_student "
                    " WHERE student_id = :s AND slot_id = :sl"
                ),
                {"s": student_id, "sl": slot_id},
            )
        ).scalar_one()
        charge = (
            await db.execute(
                text(
                    "SELECT calculated_minor, manual_minor FROM student_monthly_charge "
                    " WHERE student_id = :s AND period = :p"
                ),
                {"s": student_id, "p": PERIOD},
            )
        ).one()
        total = charge_service.charge_total_minor(
            calculated_minor=charge.calculated_minor,
            manual_minor=charge.manual_minor,
            adjustments_minor=0,
        )
        # tsk-756: день прихода ученика — то, что смена сетки уже роняла один
        # раз. Мягкое снятие оставляет строку привязки, а с ней и `created_at`,
        # но проверяется это по строке, а не на веру.
        started_on = (
            await db.execute(
                text(
                    "SELECT least("
                    "  (SELECT min((lss.created_at AT TIME ZONE 'Europe/Moscow')::date) "
                    "     FROM lesson_slot_student lss WHERE lss.student_id = :s), "
                    "  (SELECT min((o.scheduled_at AT TIME ZONE 'Europe/Moscow')::date) "
                    "     FROM lesson_occurrence_participant p "
                    "     JOIN lesson_occurrence o ON o.id = p.occurrence_id "
                    "    WHERE p.student_id = :s))"
                ),
                {"s": student_id},
            )
        ).scalar_one()
        august = (
            await db.execute(
                text(
                    "SELECT calculated_minor, manual_minor, status "
                    "  FROM student_monthly_charge "
                    " WHERE student_id = :s AND period = DATE '2026-08-01'"
                ),
                {"s": student_id},
            )
        ).one()
        pref = (
            await db.execute(
                text(
                    "SELECT lessons_per_week FROM student_schedule_preference "
                    " WHERE student_id = :s"
                ),
                {"s": student_id},
            )
        ).scalar_one()
        future = (
            await db.execute(
                text(
                    "SELECT count(*) FROM lesson_occurrence_participant p "
                    "  JOIN lesson_occurrence o ON o.id = p.occurrence_id "
                    " WHERE p.student_id = :s AND o.slot_id = :sl "
                    "   AND o.scheduled_at > now()"
                ),
                {"s": student_id, "sl": slot_id},
            )
        ).scalar_one()

        problems: list[str] = []
        if slots != 2:
            problems.append(f"слотов {slots}, ждали 2")
        if still_here:
            problems.append(f"привязка к слоту {slot_id} всё ещё активна")
        if total != EXPECTED_SEPTEMBER_MINOR:
            problems.append(
                f"сентябрь {total // 100} руб, ждали {EXPECTED_SEPTEMBER_MINOR // 100} руб"
            )
        if august.status != "closed" or august.manual_minor not in (None, 550000):
            problems.append("август тронут")
        if pref != LESSONS_PER_WEEK:
            problems.append(f"анкета {pref}, ждали {LESSONS_PER_WEEK}")
        if future:
            problems.append(f"осталось будущих занятий по снятому слоту: {future}")

        logger.info(
            "%s (%s): слотов %d, сентябрь %d руб, день прихода %s, август %s %s руб — %s",
            name,
            student_id,
            slots,
            total // 100,
            started_on,
            august.status,
            (august.manual_minor or august.calculated_minor) // 100,
            "ОК" if not problems else "ПРОБЛЕМА: " + "; ".join(problems),
        )
        ok = ok and not problems
    return ok


async def main(apply: bool) -> int:
    async with async_session_factory() as db:
        await show_state(db, "До правки")

        logger.info("\n===== План =====")
        for student_id, name, slot_id, weekday, start_time in DROP:
            slot = await _slot_row(db, slot_id)
            if slot["weekday"] != weekday or slot["start_time"] != start_time:
                logger.error(
                    "Слот %d в базе стоит на %d/%s, а в плане %d/%s — останов",
                    slot_id,
                    slot["weekday"],
                    slot["start_time"],
                    weekday,
                    start_time,
                )
                return 2
            logger.info(
                "%s (%s): снять слот %d (%d/%s), анкету — на %d занятия в неделю",
                name,
                student_id,
                slot_id,
                weekday,
                start_time.strftime("%H:%M"),
                LESSONS_PER_WEEK,
            )

        if not apply:
            logger.info("\nСухой прогон. Записи не было. Повторить с --apply.")
            return 0

        for student_id, name, slot_id, weekday, start_time in DROP:
            # Снятие идёт сервисом, а не UPDATE: он же убирает будущие занятия
            # со статусом `scheduled` и зовёт пересчёт открытых месяцев. Уже
            # отмеченная явка (`confirmed`, `no_show`) не трогается — это факт,
            # а не план.
            await lesson_calendar_service.remove_slot_participant(db, slot_id, student_id)
            logger.info("%s (%s): слот %d снят", name, student_id, slot_id)
            await fix_preference(db, student_id, weekday, start_time)
            await db.commit()
            logger.info("%s (%s): анкета опущена до %d", name, student_id, LESSONS_PER_WEEK)

        await show_state(db, "После правки")
        return 0 if await verify(db) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
