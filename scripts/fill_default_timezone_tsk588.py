"""tsk-588: разовое заполнение `users.timezone` тем, у кого он пуст (прод).

Зачем. Расписание школы ведётся по Москве, а пояс человека до этой задачи надо
было вписывать руками — он стоял у 3 из 68 активных. Преподаватель не знал, на
сколько ученик сдвинут, а двое учеников пришли на занятие мимо ровно на своё
смещение.

Что ставим. `Europe/Moscow` **с источником `auto`**, а не `manual`. Причина
прямая: план задачи предполагал «по городу, где он есть», но read-аудит прода
2026-08-13 показал, что город не заполнен НИ У ОДНОГО из 65 человек без пояса —
выводить не из чего. Значение `Europe/Moscow` здесь — заглушка «пока считаем,
что как у школы», и источник `auto` оставляет её открытой для уточнения: при
первом же входе браузер пришлёт настоящий пояс, и `apply_browser_timezone` его
перезапишет. Пометка `manual` заблокировала бы автозахват навсегда и закрепила
бы догадку как выбор человека.

Кого НЕ трогаем: у кого пояс уже стоит (в том числе выбранный вручную) и
заблокированных (`blocked_at IS NOT NULL`) — им ничего не показывается.

Протокол (`/db-check`, режим записи):
    python scripts/fill_default_timezone_tsk588.py              # сухой прогон
    DBCHECK_OK=1 python scripts/fill_default_timezone_tsk588.py --apply

Запускать на сервере под `app` (после применения миграции
`tsk588_timezone_source`):
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/fill_default_timezone_tsk588.py"'
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk588.timezone")

DEFAULT_TIMEZONE = "Europe/Moscow"

#: Кого затрагиваем: активные люди без пояса. Один и тот же предикат для
#: выборки и для записи — чтобы показанное глазами и записанное совпадали.
TARGET_WHERE = "blocked_at IS NULL AND timezone IS NULL"


async def main(apply: bool) -> None:
    async with async_session_factory() as session:
        has_column = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='timezone_source'"
            )
        )
        if not has_column:
            logger.error(
                "Колонки users.timezone_source нет — сначала примените миграцию "
                "tsk588_timezone_source, иначе заглушку нечем будет пометить."
            )
            raise SystemExit(1)

        rows = (
            await session.execute(
                text(
                    f"SELECT id, full_name, city FROM users WHERE {TARGET_WHERE} "  # nosec B608 — предикат из литерала модуля
                    "ORDER BY id"
                )
            )
        ).fetchall()

        logger.info("Под заполнение попадает людей: %s", len(rows))
        for r in rows[:20]:
            logger.info("  #%s %s (город: %s)", r[0], r[1] or "—", r[2] or "не указан")
        if len(rows) > 20:
            logger.info("  … и ещё %s", len(rows) - 20)

        with_city = [r for r in rows if (r[2] or "").strip()]
        logger.info(
            "Из них с заполненным городом: %s — по ним пояс можно уточнить точнее, "
            "чем значением по умолчанию.",
            len(with_city),
        )

        if not apply:
            logger.info(
                "\nСухой прогон. Запись не выполнялась. Для записи: "
                "DBCHECK_OK=1 python %s --apply",
                Path(__file__).name,
            )
            return

        result = await session.execute(
            text(
                f"UPDATE users SET timezone = :tz, timezone_source = 'auto' "  # nosec B608 — предикат из литерала модуля
                f"WHERE {TARGET_WHERE}"
            ),
            {"tz": DEFAULT_TIMEZONE},
        )
        await session.commit()
        logger.info("Записано строк: %s", result.rowcount)

        # Верификация после записи: пустых поясов у активных остаться не должно.
        left = await session.scalar(
            text(f"SELECT count(*) FROM users WHERE {TARGET_WHERE}")  # nosec B608
        )
        by_source = (
            await session.execute(
                text(
                    "SELECT coalesce(timezone_source, '(нет)') AS src, count(*) "
                    "FROM users WHERE blocked_at IS NULL GROUP BY 1 ORDER BY 2 DESC"
                )
            )
        ).fetchall()
        logger.info("Осталось активных без пояса: %s", left)
        for src, n in by_source:
            logger.info("  источник %s: %s", src, n)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="выполнить запись (без флага — только показать выборку)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.apply))
