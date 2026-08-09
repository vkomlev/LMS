"""tsk-596: доначисление за август 2026 по решению оператора (прод).

**Список задан явно, а не выведен во время запуска** — по той же причине, что и
в `assign_subscriptions_tsk301.py`: оператор принимает глазами конкретные пары
«человек → сумма», и записаться должно ровно то, что он посмотрел. Правило,
считающее сумму на лету, дало бы другой результат при любом сдвиге данных между
приёмкой и запуском.

Решения оператора 2026-08-08 (разбор в `reviews/2026-08-08-tsk596-recon.md`):

* **Терехов Илья (4510)** — ходит с 27.07, курса нет, подписка Base даёт расчёт
  6000 ₽. Договорённость на лето — **2750 ₽, только за август**: осенью он
  переходит на ЕГЭ и тариф меняется. Поэтому ставится `manual_minor` на строку
  августа, а НЕ бессрочная личная цена: иначе 2750 молча уехали бы в сентябрь.
* **Сундуков Максим (4527)** — в августе не занимается, выйдет в сентябре:
  ставится перерыв на весь август. Начисления у него нет и не появится (слотов
  нет), перерыв фиксирует причину, чтобы месяц не выглядел потерянным.
* **Пряхин Михаил (4498)** — оставлен на тарифе Test, денег не берём.
* **Сесюк (4521), Мурзагулов (4499)** — закончили. Смена их тарифа на
  «Выпускник» — зона tsk-301, здесь не трогаем.

Протокол (`/db-check`, режим записи):
    python scripts/fix_august_charges_tsk596.py            # сухой прогон
    DBCHECK_OK=1 python scripts/fix_august_charges_tsk596.py --apply

Запускать на сервере под `app` (R-009 operator-runbook):
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/fix_august_charges_tsk596.py"'

Проверка после записи — поштучная, по каждой строке отдельно, а не агрегатом
(урок `feedback_backfill_verify_every_row`): агрегат сходится и тогда, когда две
ошибки погасили друг друга.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.services import charge_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk596.fix")

PERIOD = date(2026, 8, 1)

#: (student_id, ФИО как в базе на момент приёмки, сумма месяца в копейках).
#: ФИО — не для поиска, а чтобы расхождение было видно глазами до записи.
MANUAL_AMOUNTS: tuple[tuple[int, str, int], ...] = (
    (4510, "Терехов Илья", 275000),
)

#: (student_id, ФИО, начало, конец) — перерыв на весь август.
BREAKS: tuple[tuple[int, str, date, date], ...] = (
    (4527, "Максим Сундуков", date(2026, 8, 1), date(2026, 8, 31)),
)

BREAK_NOTE = "tsk-596: в августе не занимается, выходит в сентябре"


async def _check_names(db) -> bool:
    """Сверить ФИО из списка с базой. Расхождение — стоп до записи."""
    ok = True
    wanted = {sid: name for sid, name, *_ in MANUAL_AMOUNTS}
    wanted.update({sid: name for sid, name, *_ in BREAKS})
    rows = (
        await db.execute(
            text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
            {"ids": list(wanted)},
        )
    ).all()
    found = {int(r.id): r.full_name for r in rows}
    for sid, name in wanted.items():
        actual = found.get(sid)
        if actual is None:
            logger.error("  СТОП: ученика %s в базе нет", sid)
            ok = False
        elif actual != name:
            logger.error("  СТОП: %s в базе «%s», в списке «%s»", sid, actual, name)
            ok = False
    return ok


async def _report_state(db, title: str) -> None:
    """Показать текущее состояние по затронутым людям — до и после записи."""
    logger.info("%s", title)
    ids = [sid for sid, *_ in MANUAL_AMOUNTS] + [sid for sid, *_ in BREAKS]
    rows = (
        await db.execute(
            text(
                "SELECT u.id, u.full_name, ch.group_id, ch.status, "
                "       ch.calculated_minor, ch.manual_minor, "
                "       ch.expected_lessons, ch.break_lessons, "
                "       (SELECT count(*) FROM student_break b "
                "         WHERE b.student_id = u.id AND b.starts_on <= :end "
                "           AND b.ends_on >= :start) AS breaks_in_period "
                "  FROM users u "
                "  LEFT JOIN student_monthly_charge ch "
                "         ON ch.student_id = u.id AND ch.period = :p "
                " WHERE u.id = ANY(:ids) ORDER BY u.id"
            ),
            {
                "ids": ids,
                "p": PERIOD,
                "start": PERIOD,
                "end": date(2026, 8, 31),
            },
        )
    ).all()
    for r in rows:
        if r.group_id is None:
            logger.info("  %s %s: строки августа нет, перерывов в августе %s",
                        r.id, r.full_name, r.breaks_in_period)
        else:
            logger.info(
                "  %s %s: группа %s, %s, расчёт %s, ручная %s, занятий %s (в перерыве %s), "
                "перерывов в августе %s",
                r.id, r.full_name, r.group_id, r.status,
                r.calculated_minor, r.manual_minor,
                r.expected_lessons, r.break_lessons, r.breaks_in_period,
            )


async def _apply(db) -> bool:
    """Записать правки. Возвращает False, если хоть одна строка не сошлась.

    **Общего отката тут нет и быть не может**, и об этом сказано прямо:
    `charge_service.recalculate_for_student` коммитит внутри себя. Значит после
    неудачи второго шага строка месяца уже существует — с расчётной суммой,
    которая БОЛЬШЕ согласованной. Молчаливый `rollback()` создавал бы
    впечатление, что ничего не записано, и человек остался бы с завышенным
    начислением, не зная об этом. Поэтому каждый шаг подтверждается вслух, а
    несошедшийся — называет, что именно осталось в базе и что править руками.
    """
    ok = True

    for student_id, name, amount in MANUAL_AMOUNTS:
        # Строки месяца может ещё не быть: у Терехова её и нет. Пересчёт заводит
        # её по расчёту, а ручная сумма ложится сверху — так на экране видно и
        # расчёт, и договорённость, а не одно число неизвестного происхождения.
        await charge_service.recalculate_for_student(
            db, student_id=student_id, period=PERIOD
        )
        created = (
            await db.execute(
                text(
                    "SELECT id, calculated_minor FROM student_monthly_charge "
                    " WHERE student_id = :s AND period = :p"
                ),
                {"s": student_id, "p": PERIOD},
            )
        ).first()
        if created is None:
            logger.error(
                "  СТОП: %s %s — пересчёт строку августа не завёл (нет тарифной "
                "группы или цена не разрешилась). Ручную сумму ставить не на что.",
                student_id, name,
            )
            ok = False
            continue
        logger.info(
            "  шаг 1: %s %s — строка августа есть (расчёт %.2f ₽)",
            student_id, name, int(created.calculated_minor) / 100,
        )

        res = await db.execute(
            text(
                "UPDATE student_monthly_charge SET manual_minor = :amt, updated_at = now() "
                " WHERE student_id = :s AND period = :p AND status = 'open'"
            ),
            {"s": student_id, "p": PERIOD, "amt": amount},
        )
        if res.rowcount != 1:
            logger.error(
                "  СТОП: %s %s — правок строк %s, ожидалась ровно одна. "
                "ВНИМАНИЕ: строка августа уже создана с расчётом %.2f ₽ — это "
                "больше согласованных %.2f ₽, поправить руками.",
                student_id, name, res.rowcount,
                int(created.calculated_minor) / 100, amount / 100,
            )
            ok = False
        else:
            logger.info("  шаг 2: %s %s — сумма месяца %.2f ₽", student_id, name, amount / 100)

    for student_id, name, starts_on, ends_on in BREAKS:
        # Повторный запуск не должен плодить перерывы: проверяем пересечение, а
        # не точное совпадение дат — перекрывающийся перерыв считается тем же.
        exists = (
            await db.execute(
                text(
                    "SELECT id FROM student_break "
                    " WHERE student_id = :s AND starts_on <= :e AND ends_on >= :b"
                ),
                {"s": student_id, "b": starts_on, "e": ends_on},
            )
        ).first()
        if exists is not None:
            logger.info("  %s %s: перерыв на август уже есть (id %s) — пропуск",
                        student_id, name, exists.id)
            continue
        await db.execute(
            text(
                "INSERT INTO student_break (student_id, starts_on, ends_on, note) "
                "VALUES (:s, :b, :e, :n)"
            ),
            {"s": student_id, "b": starts_on, "e": ends_on, "n": BREAK_NOTE},
        )

    return ok


async def _verify(db) -> bool:
    """Поштучная проверка результата: по каждой строке отдельно, не агрегатом."""
    ok = True

    for student_id, name, amount in MANUAL_AMOUNTS:
        row = (
            await db.execute(
                text(
                    "SELECT manual_minor, calculated_minor, status "
                    "  FROM student_monthly_charge "
                    " WHERE student_id = :s AND period = :p"
                ),
                {"s": student_id, "p": PERIOD},
            )
        ).first()
        if row is None:
            logger.error("  ПРОВЕРКА: %s %s — строки августа нет", student_id, name)
            ok = False
        elif int(row.manual_minor or -1) != amount:
            logger.error(
                "  ПРОВЕРКА: %s %s — ручная сумма %s, ожидалась %s",
                student_id, name, row.manual_minor, amount,
            )
            ok = False
        else:
            logger.info(
                "  ПРОВЕРКА ok: %s %s — к оплате %.2f ₽ (расчёт был %.2f ₽)",
                student_id, name, amount / 100, int(row.calculated_minor) / 100,
            )

    for student_id, name, starts_on, ends_on in BREAKS:
        cnt = int(
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM student_break "
                        " WHERE student_id = :s AND starts_on <= :e AND ends_on >= :b"
                    ),
                    {"s": student_id, "b": starts_on, "e": ends_on},
                )
            ).scalar()
        )
        if cnt != 1:
            logger.error(
                "  ПРОВЕРКА: %s %s — перерывов на август %s, ожидался ровно один",
                student_id, name, cnt,
            )
            ok = False
        else:
            logger.info("  ПРОВЕРКА ok: %s %s — перерыв на август один", student_id, name)

    return ok


async def main(apply: bool) -> int:
    async with async_session_factory() as db:
        logger.info("tsk-596: правка августа %s", "(ЗАПИСЬ)" if apply else "(сухой прогон)")
        if not await _check_names(db):
            return 2
        await _report_state(db, "Состояние ДО:")

        logger.info("План:")
        for student_id, name, amount in MANUAL_AMOUNTS:
            logger.info("  %s %s — сумма месяца руками %.2f ₽", student_id, name, amount / 100)
        for student_id, name, starts_on, ends_on in BREAKS:
            logger.info("  %s %s — перерыв %s … %s", student_id, name, starts_on, ends_on)

        if not apply:
            logger.info("Сухой прогон: ничего не записано. Для записи — --apply.")
            return 0

        applied_ok = await _apply(db)
        await db.commit()

        await _report_state(db, "Состояние ПОСЛЕ:")
        if not applied_ok:
            logger.error(
                "План сошёлся не полностью — см. СТОП выше. Часть правок уже в базе "
                "(общего отката здесь нет), разбирать руками."
            )
            return 3
        if not await _verify(db):
            logger.error("Проверка после записи не сошлась — разбирать руками.")
            return 4
        logger.info("Готово.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — сухой прогон)")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
