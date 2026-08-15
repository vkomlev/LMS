"""tsk-615: внести деньги за уже случившиеся покупки пакетов задним числом.

Покупки, прошедшие ДО этой задачи, оставили след только в `student_ai_grant`:
пакет зачислен, платежа нет. На 16.08.2026 такая покупка одна — 500 ₽, ученик 2,
ЮKassa `3212eaa2-000f-5001-9000-172b9dbd5454`. Пока её нет в учёте, сверка с
приходом в шлюзе не сходится с самого первого платежа.

**Сумма и дата берутся у ЮKassa, а не с рук.** Это то же правило, на котором
стоит приём уведомлений (tsk-010): подтверждением платежа считается ответ API
на наш запрос, а не то, что кто-то ввёл или прислал. Здесь оно тем более важно,
что запись идёт задним числом и проверить её потом будет нечем, кроме этого же
шлюза.

Дата платежа — день захвата денег ПО МОСКВЕ: сверять придётся с чеками «Мой
налог», а они выбиваются по местному времени. Живая покупка это ровно тот
случай, где разница видна: захват 2026-08-15 21:15 UTC — это 16 августа в
Москве, и день в отчёте отличался бы на сутки.

Запуск (на сервере, под пользователем `app`, из `/opt/lms`):

    python scripts/backfill_tsk615_package_payments.py            # только показать
    DBCHECK_OK=1 python scripts/backfill_tsk615_package_payments.py --apply

Без `--apply` не пишет ничего. Повторный запуск безопасен: уникальность пары
«шлюз + номер платежа» не даст завести деньги дважды.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

# Настройки берём из `.env` рядом с проектом — как это делает сам сервис. Без
# этого скрипт падает на «нет DATABASE_URL» ещё на импорте: секреты в командную
# строку не передаём (они уходят в аудит sudo и в `ps`, урок tsk-595).
from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.services import payment_service, yookassa_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk615")

#: Время школы. Дата платежа для сверки считается по нему, а не по UTC.
_SCHOOL_TZ = ZoneInfo("Europe/Moscow")

#: Гранты, купленные картой, у которых нет строки платежа. Ручные выдачи
#: персоналом (`gateway_payment_id IS NULL`) сюда не попадают — за ними денег и
#: не было.
_ORPHAN_GRANTS = """
    SELECT g.id, g.student_id, g.granted, g.purchased_at, g.gateway_payment_id
      FROM student_ai_grant g
     WHERE g.gateway_payment_id IS NOT NULL
       AND NOT EXISTS (
             SELECT 1 FROM student_payment p
              WHERE p.gateway_payment_id = g.gateway_payment_id
           )
     ORDER BY g.purchased_at
"""


async def _run(apply: bool) -> int:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    written = 0
    async with session_factory() as db:
        orphans = (await db.execute(text(_ORPHAN_GRANTS))).all()
        if not orphans:
            logger.info("Пакетов без учтённых денег нет — сверять нечего.")
            await engine.dispose()
            return 0

        logger.info("Пакетов без учтённых денег: %s", len(orphans))
        for row in orphans:
            txn = row.gateway_payment_id
            try:
                payment = await yookassa_service.fetch_payment(txn)
            except yookassa_service.GatewayError as exc:
                logger.error("  %s — шлюз не ответил (%s). Пропускаю.", txn, exc)
                continue
            except yookassa_service.GatewayDisabledError as exc:
                logger.error("Оплата картой выключена в этом окружении: %s", exc)
                break

            if payment.status != "succeeded" or not payment.paid:
                # Пакет есть, а денег у шлюза нет — это не пропущенный учёт, а
                # расхождение, которое должен разобрать человек.
                logger.error(
                    "  %s — у шлюза статус %s, оплачен=%s. НЕ ВНОШУ, нужен разбор.",
                    txn, payment.status, payment.paid,
                )
                continue

            paid_on = (
                payment.captured_at.astimezone(_SCHOOL_TZ).date()
                if payment.captured_at is not None
                # Запасной вариант: день покупки пакета из нашей же базы. Хуже
                # даты шлюза, но всё равно ближе к правде, чем «сегодня».
                else row.purchased_at.astimezone(_SCHOOL_TZ).date()
            )
            logger.info(
                "  ученик %s, %s ₽, %s обращений, день платежа %s, платёж %s",
                row.student_id, payment.amount_minor / 100, row.granted, paid_on, txn,
            )
            if not apply:
                continue

            recorded = await payment_service.record_gateway_payment(
                db,
                student_id=int(row.student_id),
                group_id=None,
                period=None,
                amount_minor=payment.amount_minor,
                gateway="yookassa",
                gateway_payment_id=txn,
                paid_on=paid_on,
                purpose=payment_service.PURPOSE_AI_PACKAGE,
                review_note="tsk-615: внесено задним числом, сверено с ЮKassa",
            )
            written += int(recorded)

        if apply:
            await _verify(db)

    await engine.dispose()
    return written


async def _verify(db) -> None:
    """Проверка ПОСЛЕ записи: каждый пакет получил свои деньги.

    Проверяется поштучно, а не «стало столько-то строк»: совпадение количеств
    ничего не доказывает, если одна покупка учтена дважды, а другая не учтена.
    """
    left = (await db.execute(text(_ORPHAN_GRANTS))).all()
    if left:
        logger.error(
            "ОСТАЛИСЬ БЕЗ ДЕНЕГ: %s — %s",
            len(left), ", ".join(r.gateway_payment_id for r in left),
        )
    else:
        logger.info("Проверка: у каждого купленного пакета есть строка платежа.")

    rows = (
        await db.execute(
            text(
                "SELECT p.id, p.student_id, p.amount_minor, p.paid_on, p.status, "
                "       p.gateway_payment_id "
                "  FROM student_payment p WHERE p.purpose = :pu ORDER BY p.id"
            ),
            {"pu": payment_service.PURPOSE_AI_PACKAGE},
        )
    ).all()
    logger.info("Разовых покупок в учёте: %s", len(rows))
    for r in rows:
        logger.info(
            "  #%s ученик %s %s ₽ %s %s (%s)",
            r.id, r.student_id, r.amount_minor / 100, r.paid_on, r.status,
            r.gateway_payment_id,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="записать платежи (без флага — только показать, что будет сделано)",
    )
    args = parser.parse_args()

    written = asyncio.run(_run(args.apply))
    if args.apply:
        logger.info("Записано платежей: %s (сегодня %s)", written, date.today())
    else:
        logger.info("Пробный прогон. Для записи — тот же запуск с --apply.")


if __name__ == "__main__":
    main()
