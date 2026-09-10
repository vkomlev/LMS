# -*- coding: utf-8 -*-
"""tsk-873: пометить корневые курсы подготовки признаком «экзаменационный».

Признак ставится ТОЛЬКО корневым курсам: подкурс («Циклы в Python») входит
сразу в несколько программ и в курсы для подростков — свойство принадлежит
программе, а не ему. Признак «пора усложнить» (tsk-649) смотрит от задания
ВВЕРХ по дереву до корня, поэтому пометки на корне достаточно.

Помечаются три корня действующих программ:

    88   Python для ЕГЭ
    112  ЕГЭ по информатике
    1080 ОГЭ по информатике

«Python для ОГЭ» (1454) в список не входит намеренно: он подкурс 1080, и
пометка на нём нарушила бы правило «только корневые», ничего не добавив —
подъём по дереву и так дойдёт до 1080.

Вводные курсы («ЕГЭ по информатике: что за экзамен…», «…диагностика за
15 минут» и одноимённый по ОГЭ) не помечаются: это не программы подготовки,
и из расчёта нагрузки они убраны ещё в tsk-869.

Запуск (после протокола `/db-check`):
    DBCHECK_OK=1 python scripts/tsk873_mark_exam_courses.py --apply
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

#: Корни программ подготовки: (id, ожидаемое название для сверки).
EXAM_ROOTS: list[tuple[int, str]] = [
    (88, "Python для ЕГЭ"),
    (112, "ЕГЭ по информатике"),
    (1080, "ОГЭ по информатике"),
]


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


async def main(apply: bool) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ids = [cid for cid, _ in EXAM_ROOTS]

    async with factory() as db:
        # Колонка появляется миграцией tsk873_course_exam_and_active — без неё
        # запись молча не про что, поэтому проверяем до плана.
        has_column = (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'courses' AND column_name = 'is_exam'"
                )
            )
        ).scalar()
        if not has_column:
            print("ОШИБКА: колонки courses.is_exam нет — миграция не применена")
            await engine.dispose()
            return 1

        rows = (
            await db.execute(
                text(
                    "SELECT c.id, c.title, c.is_exam, EXISTS ("
                    "  SELECT 1 FROM course_parents cp WHERE cp.course_id = c.id"
                    ") AS has_parent "
                    "FROM courses c WHERE c.id = ANY(:ids) ORDER BY c.id"
                ),
                {"ids": ids},
            )
        ).all()
        found = {int(r[0]): r for r in rows}

        missing = [cid for cid in ids if cid not in found]
        if missing:
            print(f"ОШИБКА: курсы не найдены: {missing}")
            await engine.dispose()
            return 1

        print(f"{'id':>6}  {'корневой':<9} {'было':<6} {'станет':<7} название")
        for cid, expected_title in EXAM_ROOTS:
            _, title, is_exam, has_parent = found[cid]
            if title != expected_title:
                print(f"ОШИБКА: курс {cid} называется «{title}», ждали «{expected_title}»")
                await engine.dispose()
                return 1
            if has_parent:
                print(f"ОШИБКА: курс {cid} не корневой — признак ставится только корням")
                await engine.dispose()
                return 1
            print(f"{cid:>6}  {'да':<9} {str(is_exam):<6} {'True':<7} {title}")

        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            await engine.dispose()
            return 0

        # Транзакция открыта первым SELECT — коммитим её явно.
        await db.execute(
            text("UPDATE courses SET is_exam = true WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.commit()

        # Верификация после записи: и что помечено ровно три, и что нигде
        # больше признак не всплыл.
        marked = (
            await db.execute(
                text("SELECT id, title FROM courses WHERE is_exam ORDER BY id")
            )
        ).all()
        print("\nПомечены как экзаменационные:")
        for cid, title in marked:
            print(f"{cid:>6}  {title}")
        ok = sorted(int(r[0]) for r in marked) == sorted(ids)
        print("\nСверка: " + ("совпадает с планом" if ok else "РАСХОЖДЕНИЕ С ПЛАНОМ"))
        await engine.dispose()
        return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать в боевую БД")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
