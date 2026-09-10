# -*- coding: utf-8 -*-
"""tsk-875: временные занятия для живой проверки спойлера.

Спойлер прошедших занятий виден только тому, у кого эти занятия есть, а у
тестовых учётных записей оператора (2 и 142) занятий нет вовсе. Входить ради
проверки под учётной записью реального ученика — читать чужой кабинет, чего
делать не нужно.

Скрипт заводит ТРИ разовых занятия для учётной записи оператора (два прошедших,
одно завтрашнее) — под ней и идёт живой прогон, чужих кабинетов это не
касается. После проверки убирается тем же скриптом с `--cleanup`.

Запуск (после протокола `/db-check`):
    DBCHECK_OK=1 python scripts/tsk875_live_check_fixture.py --apply
    DBCHECK_OK=1 python scripts/tsk875_live_check_fixture.py --cleanup --apply
Без `--apply` — только показывает план.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

STUDENT_ID = 2     # Виктор Комлев — под этой учётной записью идёт живой прогон
TEACHER_ID = 2     # он же преподаватель: занятие служебное, чужих данных не трогает
#: (сдвиг в часах от «сейчас», статус явки) — два прошедших и одно будущее.
LESSONS: list[tuple[int, str]] = [
    (-48, "completed"),
    (-24, "no_show"),
    (+24, "scheduled"),
]
DURATION_MINUTES = 60

#: Куда скрипт записывает id заведённых занятий. Удаляется РОВНО этот список,
#: а не «все разовые занятия пары»: первая редакция чистила по признаку
#: `slot_id IS NULL` и снесла занятие #917, заведённое не ею. Признак не
#: доказывает авторство — доказывает только собственная запись.
STATE_FILE = PROJECT_ROOT / "scratchpad" / "tsk875-fixture-ids.json"


def load_prod_dsn_asyncpg_style() -> str:
    """DSN прод-роли из `.mcp.json` в формате SQLAlchemy (секрет не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(apply: bool, cleanup: bool) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True, encoding="utf-8-sig")
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        # Что уже есть: занятия этого ученика в окне экрана [-2 суток, +14].
        existing = (
            await db.execute(
                text(
                    "SELECT lo.id, lo.scheduled_at, lop.status "
                    "  FROM lesson_occurrence_participant lop "
                    "  JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                    " WHERE lop.student_id = :sid "
                    "   AND lo.scheduled_at BETWEEN now() - interval '2 days' "
                    "                           AND now() + interval '14 days' "
                    " ORDER BY lo.scheduled_at"
                ),
                {"sid": STUDENT_ID},
            )
        ).all()
        print(f"Сейчас у ученика {STUDENT_ID} в окне экрана: {len(existing)} занятий")
        for row in existing:
            print(f"  #{row[0]}  {row[1]}  {row[2]}")

        if cleanup:
            # Убираем только разовые (slot_id IS NULL) занятия этой пары —
            # регулярные к скрипту отношения не имеют и трогать их нельзя.
            ids = [
                int(r[0])
                for r in (
                    await db.execute(
                        text(
                            "SELECT lo.id FROM lesson_occurrence lo "
                            "  JOIN lesson_occurrence_participant lop "
                            "    ON lop.occurrence_id = lo.id AND lop.student_id = :sid "
                            " WHERE lo.slot_id IS NULL AND lo.teacher_id = :tid"
                        ),
                        {"sid": STUDENT_ID, "tid": TEACHER_ID},
                    )
                ).all()
            ]
            print(f"\nК удалению разовых занятий: {ids}")
            if not apply or not ids:
                print("Это предпросмотр. Удалить: добавить --apply")
                await engine.dispose()
                return 0
            await db.execute(
                text("DELETE FROM lesson_occurrence_participant WHERE occurrence_id = ANY(:i)"),
                {"i": ids},
            )
            await db.execute(
                text("DELETE FROM lesson_occurrence_teacher WHERE occurrence_id = ANY(:i)"),
                {"i": ids},
            )
            await db.execute(
                text("DELETE FROM lesson_occurrence WHERE id = ANY(:i)"), {"i": ids}
            )
            await db.commit()
            left = (
                await db.execute(
                    text("SELECT count(*) FROM lesson_occurrence WHERE id = ANY(:i)"),
                    {"i": ids},
                )
            ).scalar()
            print(f"Удалено. Осталось строк из списка: {left}")
            if left == 0:
                STATE_FILE.unlink()
            await engine.dispose()
            return 0 if left == 0 else 1

        print("\nПлан: завести разовые занятия")
        for hours, status in LESSONS:
            print(f"  сдвиг {hours:+d} ч, явка «{status}»")
        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            await engine.dispose()
            return 0

        created: list[int] = []
        for hours, status in LESSONS:
            occ_id = int(
                (
                    await db.execute(
                        text(
                            "INSERT INTO lesson_occurrence "
                            "  (slot_id, teacher_id, scheduled_at, duration_minutes) "
                            "VALUES (NULL, :tid, now() + make_interval(hours => :h), :d) "
                            "RETURNING id"
                        ),
                        {"tid": TEACHER_ID, "h": hours, "d": DURATION_MINUTES},
                    )
                ).scalar_one()
            )
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence_participant "
                    "  (occurrence_id, student_id, status) VALUES (:o, :s, :st)"
                ),
                {"o": occ_id, "s": STUDENT_ID, "st": status},
            )
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id) "
                    "VALUES (:o, :t)"
                ),
                {"o": occ_id, "t": TEACHER_ID},
            )
            created.append(occ_id)
        await db.commit()
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(created), encoding="utf-8")

        rows = (
            await db.execute(
                text(
                    "SELECT lo.id, lo.scheduled_at, lop.status "
                    "  FROM lesson_occurrence lo "
                    "  JOIN lesson_occurrence_participant lop ON lop.occurrence_id = lo.id "
                    " WHERE lo.id = ANY(:i) ORDER BY lo.scheduled_at"
                ),
                {"i": created},
            )
        ).all()
        print("\nЗаведено:")
        for row in rows:
            print(f"  #{row[0]}  {row[1]}  {row[2]}")
        print(f"\nСверка: {len(rows)} из {len(LESSONS)}")
        await engine.dispose()
        return 0 if len(rows) == len(LESSONS) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать в боевую БД")
    parser.add_argument("--cleanup", action="store_true", help="убрать заведённое")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply, args.cleanup)))
