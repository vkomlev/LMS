# -*- coding: utf-8 -*-
"""Разбор окна аварии: не потерялись ли работы учеников (tsk-644).

Зачем. 18.08.2026 в окне 12:11–12:25 приём ответа занимал до 123,5 с. Разбор
tsk-644 показал на стенде, что при молчащем файловом хранилище приём ответа не
просто ждёт, а ЗАКАНЧИВАЕТСЯ ОТКАЗОМ 503 — работа не записывается вовсе. Значит
у вопроса «сколько ждали» есть второй, более неприятный: «а всё ли записалось».

Чего этот скрипт НЕ может. Отказ 503 не оставляет строки в `task_results` —
по определению. Прямого следа в базе у потерянной сдачи нет, он есть только в
`logs/app.log` на боевой машине. Поэтому здесь — КОСВЕННЫЕ улики, а вывод
делается вместе с логом (команда grep печатается в конце).

Что считаем:
  * попытки, живые в окне (созданы до конца окна, не отменены), у которых в окне
    нет ни одной записи ответа, — кандидаты на «ученик жал, а не записалось»;
  * ответы, записанные в окне, — фон для сравнения;
  * те же числа за соседние дни в те же минуты — без этого сравнения любое число
    выглядит страшным, хотя может быть обычным вторником.

Read-only: ни одного UPDATE.

Запуск (боевая база — явным override, tsk-246):
    DATABASE_URL=<прод-dsn> python scripts/audit_lost_submissions_tsk644.py
    DATABASE_URL=<прод-dsn> python scripts/audit_lost_submissions_tsk644.py \
        --date 2026-08-18 --from 12:00 --to 12:40

Коды выхода: 0 — кандидатов нет; 1 — есть кандидаты на разбор; 2 — ошибка.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32" and not os.environ.get("LMS_CHECK_NO_CONSOLE"):
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# Ответы, записанные в окне: фон, с которым сравниваются кандидаты.
SQL_ANSWERS = """
SELECT count(*) AS n, count(DISTINCT user_id) AS students
FROM task_results
WHERE submitted_at >= :start AND submitted_at < :end
"""

# Попытка была живой в окне, но ни одного ответа в окне нет.
SQL_SILENT_ATTEMPTS = """
SELECT a.id, a.user_id, a.course_id, a.created_at, a.finished_at
FROM attempts a
WHERE a.created_at < :end
  AND a.cancelled_at IS NULL
  AND (a.finished_at IS NULL OR a.finished_at >= :start)
  AND NOT EXISTS (
        SELECT 1 FROM task_results tr
        WHERE tr.attempt_id = a.id
          AND tr.submitted_at >= :start AND tr.submitted_at < :end
      )
ORDER BY a.created_at
LIMIT 50
"""

SQL_SILENT_COUNT = """
SELECT count(*) FROM attempts a
WHERE a.created_at < :end
  AND a.cancelled_at IS NULL
  AND (a.finished_at IS NULL OR a.finished_at >= :start)
  AND NOT EXISTS (
        SELECT 1 FROM task_results tr
        WHERE tr.attempt_id = a.id
          AND tr.submitted_at >= :start AND tr.submitted_at < :end
      )
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-08-18", help="дата окна, ГГГГ-ММ-ДД")
    ap.add_argument("--from", dest="t_from", default="12:11", help="начало окна, ЧЧ:ММ")
    ap.add_argument("--to", dest="t_to", default="12:25", help="конец окна, ЧЧ:ММ")
    ap.add_argument(
        "--compare-days", type=int, default=3,
        help="сколько предыдущих дней показать для сравнения фона",
    )
    args = ap.parse_args()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL", file=sys.stderr)
        return 2
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Границы окна — настоящие datetime, а не строки: asyncpg строку в
    # timestamptz сам не приводит и падает на первом же запросе.
    # Часовой пояс московский: время в тикете записано по нему.
    msk = timezone(timedelta(hours=3))
    try:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
        h_from, m_from = (int(x) for x in args.t_from.split(":"))
        h_to, m_to = (int(x) for x in args.t_to.split(":"))
    except ValueError:
        print("ОШИБКА: дата ждёт ГГГГ-ММ-ДД, время — ЧЧ:ММ", file=sys.stderr)
        return 2
    start = datetime.combine(day, dtime(h_from, m_from), tzinfo=msk)
    end = datetime.combine(day, dtime(h_to, m_to), tzinfo=msk)
    if end <= start:
        print("ОШИБКА: конец окна не позже начала", file=sys.stderr)
        return 2

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            where = (await conn.execute(text(
                "SELECT current_database() AS db, inet_server_addr()::text AS host"
            ))).mappings().first()
            print(f"База: {where['db']} на {where['host'] or 'localhost'}")
            print(
                f"Окно: {start:%d.%m.%Y %H:%M} — {end:%H:%M} (время московское)\n"
            )

            params = {"start": start, "end": end}
            answers = (await conn.execute(text(SQL_ANSWERS), params)).mappings().first()
            silent_n = (await conn.execute(text(SQL_SILENT_COUNT), params)).scalar() or 0
            silent = (await conn.execute(text(SQL_SILENT_ATTEMPTS), params)).mappings().all()

            print(
                f"Записано ответов в окне: {answers['n']} "
                f"(учеников: {answers['students']})"
            )
            print(f"Попыток живых в окне БЕЗ единого ответа: {silent_n}")

            # Фон соседних дней: те же минуты, но в обычные дни.
            print(f"\nДля сравнения — те же минуты в предыдущие {args.compare_days} дн.:")
            for back in range(1, args.compare_days + 1):
                shift = timedelta(days=back)
                p = {"start": start - shift, "end": end - shift}
                row = (await conn.execute(text(SQL_ANSWERS), p)).mappings().first()
                cnt = (await conn.execute(text(SQL_SILENT_COUNT), p)).scalar() or 0
                print(
                    f"  −{back} дн.: ответов {row['n']}, "
                    f"попыток без ответа {cnt}"
                )
    finally:
        await engine.dispose()

    if silent:
        print("\nКандидаты на разбор (попытка была открыта, ответов в окне нет):")
        for r in silent[:20]:
            print(
                f"  попытка {r['id']}  ученик {r['user_id']}  курс {r['course_id']}  "
                f"создана {r['created_at']:%d.%m %H:%M}"
            )

    print(
        "\nРЕШАЮЩАЯ УЛИКА — В ЛОГЕ, НЕ ЗДЕСЬ. Отказ 503 строки в базе не оставляет.\n"
        "На боевой машине выполнить:\n"
        "  ls -la /opt/lms/logs/            # ротация 5 МБ x 5: окно могло уехать в app.log.1..5\n"
        "  grep -c 'ошибка соединения с S3' /opt/lms/logs/app.log*\n"
        "  grep 'ошибка перечисления' /opt/lms/logs/app.log* | head -20\n"
        f"  grep 'slow request' /opt/lms/logs/app.log* | grep '{args.date}' | head -30\n"
        "Если строки про S3 есть в том же окне — версия tsk-644 (молчащее файловое\n"
        "хранилище) подтверждена. Если их нет вовсе — версия неверна, и разбирать\n"
        "надо заново: значит запросы держало что-то третье."
    )
    return 1 if silent_n else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print(f"ОШИБКА выполнения: {exc}", file=sys.stderr)
        sys.exit(2)
