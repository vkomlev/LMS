# -*- coding: utf-8 -*-
"""tsk-877: пометить служебные курсы признаком `is_service`.

Служебный — тот, что НЕ УЧИТ ПРЕДМЕТУ: объясняет устройство сервиса и
экзамена либо меряет вход. Его задания не идут в аналитические признаки:
живая проверка 10.09 показала, что 24 вопроса про кабинет и правила экзамена
в одиночку закрывали порог «пора усложнить» (tsk-649) у большинства школы.

    1466  ЕГЭ по информатике: диагностика за 15 минут
    1467  С чего начать: кабинет, занятия и работа дома
    1474  ЕГЭ по информатике: что за экзамен и как к нему готовиться
    1481  ОГЭ по информатике: что за экзамен и как к нему готовиться

Учебные вводные курсы («Вводный Python» 682, «Вводная информатика» 681,
«Пробное занятие IT-школы» 651, «Повторение…» 754/1460) НЕ помечаются: они
учат предмету, и признак «пора усложнить» там уместен. Вводный по уровню не
значит служебный по назначению.

Признак ставится корневым курсам, как и `is_exam`: он поднимается по дереву
от задания вверх.

Запуск (после протокола `/db-check`):
    DBCHECK_OK=1 python scripts/tsk877_mark_service_courses.py --apply
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

#: Корни служебных курсов: (id, ожидаемое название для сверки).
SERVICE_ROOTS: list[tuple[int, str]] = [
    (1466, "ЕГЭ по информатике: диагностика за 15 минут"),
    (1467, "С чего начать: кабинет, занятия и работа дома"),
    (1474, "ЕГЭ по информатике: что за экзамен и как к нему готовиться"),
    (1481, "ОГЭ по информатике: что за экзамен и как к нему готовиться"),
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
    ids = [cid for cid, _ in SERVICE_ROOTS]

    async with factory() as db:
        has_column = (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'courses' AND column_name = 'is_service'"
                )
            )
        ).scalar()
        if not has_column:
            print("ОШИБКА: колонки courses.is_service нет — миграция не применена")
            await engine.dispose()
            return 1

        rows = (
            await db.execute(
                text(
                    "SELECT c.id, c.title, c.is_service, c.is_exam, EXISTS ("
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

        print(f"{'id':>6}  {'было':<6} {'станет':<7} название")
        for cid, expected_title in SERVICE_ROOTS:
            _, title, is_service, is_exam, has_parent = found[cid]
            if title != expected_title:
                print(f"ОШИБКА: курс {cid} называется «{title}», ждали «{expected_title}»")
                await engine.dispose()
                return 1
            if has_parent:
                print(f"ОШИБКА: курс {cid} не корневой — признак ставится только корням")
                await engine.dispose()
                return 1
            if is_exam:
                # Программа подготовки и служебный курс — разные вещи;
                # совпадение означало бы ошибку в одном из двух списков.
                print(f"ОШИБКА: курс {cid} помечен как экзаменационный")
                await engine.dispose()
                return 1
            print(f"{cid:>6}  {str(is_service):<6} {'True':<7} {title}")

        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            await engine.dispose()
            return 0

        await db.execute(
            text("UPDATE courses SET is_service = true WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.commit()

        marked = (
            await db.execute(
                text("SELECT id, title FROM courses WHERE is_service ORDER BY id")
            )
        ).all()
        print("\nПомечены как служебные:")
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
