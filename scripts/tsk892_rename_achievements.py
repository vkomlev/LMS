# -*- coding: utf-8 -*-
"""tsk-892: понятные ученику названия и описания достижений.

Замечание оператора 10.09: «названия ачивок непонятны никому, ни ученикам, ни
преподам». Разбор показал три беды сразу:

* **«шаг» — слово ниоткуда.** В интерфейсе ученика его нет: там «задания»,
  «материалы», «Продолжить». Слово жило только в названии значка.
* **Две линейки выглядели одной.** И объём («10 шагов между занятиями»), и
  регулярность («Неделя между занятиями») кончались одинаково, хотя меряют
  разное.
* **Описания устроены вразнобой** — от «Полтора месяца без единой
  пропущенной недели» до «Три месяца подряд — привычка заниматься между
  уроками закрепилась».

Принцип новых формулировок (утверждён оператором 10.09): в НАЗВАНИИ число и
что сделано, в ОПИСАНИИ — за что именно. Линейки разведены словами: объём
говорит «дома», регулярность — «без перерыва».

Сопоставление идёт по УСЛОВИЮ значка, а не по номеру: условие машинное и
одинаково в любой базе, а номера в dev и на проде разъезжаются. То же правило,
что и в `lesson_plan_service._achievement_reason`.

Переименование безопасно: значки выданы 303 раза, но связь идёт по номеру —
у людей просто поменяется подпись. План занятия на названия больше не смотрит
(tsk-889), он называет повод словами.

Запуск (после протокола `/db-check`):
    DBCHECK_OK=1 python scripts/tsk892_rename_achievements.py --apply
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

#: Ключ условия → (название, описание). Ключ собирается из `condition`.
WORDING: dict[tuple[str, int], tuple[str, str]] = {
    ("between_lessons_items", 10): (
        "10 сделано дома",
        "Десять заданий и материалов, пройденных между уроками.",
    ),
    ("between_lessons_items", 25): (
        "25 сделано дома",
        "Двадцать пять заданий и материалов, пройденных между уроками.",
    ),
    ("between_lessons_items", 50): (
        "50 сделано дома",
        "Полсотни заданий и материалов, пройденных между уроками.",
    ),
    ("between_lessons_items", 100): (
        "100 сделано дома",
        "Сотня заданий и материалов, пройденных между уроками.",
    ),
    ("between_lessons_items", 250): (
        "250 сделано дома",
        "Двести пятьдесят заданий и материалов, пройденных между уроками.",
    ),
    ("weekly_streak", 1): (
        "Неделя без перерыва",
        "Занимался дома хотя бы один день на этой неделе.",
    ),
    ("weekly_streak", 3): (
        "3 недели без перерыва",
        "Три недели подряд возвращался к учёбе между уроками.",
    ),
    ("weekly_streak", 6): (
        "6 недель без перерыва",
        "Полтора месяца подряд, без пропущенной недели.",
    ),
    ("weekly_streak", 12): (
        "12 недель без перерыва",
        "Три месяца подряд — привычка заниматься сама собой.",
    ),
}


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


def _key(condition: object) -> tuple[str, int] | None:
    """Ключ условия: («вид», «порог»). `None` — вид условия незнакомый."""
    if not isinstance(condition, dict):
        return None
    kind = condition.get("type")
    if kind == "between_lessons_items":
        return ("between_lessons_items", int(condition.get("count") or 0))
    if kind == "weekly_streak":
        return ("weekly_streak", int(condition.get("weeks") or 0))
    return None


async def main(apply: bool) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True, encoding="utf-8-sig")
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT a.id, a.name, a.description, a.condition, "
                    "  (SELECT count(*) FROM user_achievements ua "
                    "    WHERE ua.achievement_id = a.id) AS earned "
                    "FROM achievements a ORDER BY a.id"
                )
            )
        ).mappings().all()

        plan: list[tuple[int, str, str]] = []
        untouched: list[str] = []
        for row in rows:
            key = _key(row["condition"])
            wording = WORDING.get(key) if key else None
            if wording is None:
                # Незнакомый значок не трогаем: молча придумывать ему подпись
                # хуже, чем оставить прежнюю.
                untouched.append(f"#{row['id']} {row['name']}")
                continue
            name, description = wording
            if name == row["name"] and description == row["description"]:
                continue
            plan.append((int(row["id"]), name, description))
            print(f"#{row['id']:>3}  выдан {row['earned']:>3} раз")
            print(f"      было : {row['name']}")
            print(f"             {row['description']}")
            print(f"      будет: {name}")
            print(f"             {description}")

        if untouched:
            print("\nНе трогаем (условие незнакомое): " + ", ".join(untouched))
        if not plan:
            print("\nМенять нечего — формулировки уже на месте.")
            await engine.dispose()
            return 0

        # Названия уникальны индексом: столкновение внутри плана поймаем до
        # записи, иначе первая же строка упала бы посреди транзакции.
        names = [name for _, name, _ in plan]
        if len(set(names)) != len(names):
            print("ОШИБКА: в плане повторяются названия")
            await engine.dispose()
            return 1

        if not apply:
            print(f"\nЭто предпросмотр, к правке {len(plan)}. Записать: --apply")
            await engine.dispose()
            return 0

        for achievement_id, name, description in plan:
            await db.execute(
                text(
                    "UPDATE achievements SET name = :n, description = :d "
                    " WHERE id = :i"
                ),
                {"n": name, "d": description, "i": achievement_id},
            )
        await db.commit()

        after = (
            await db.execute(
                text("SELECT id, name, description FROM achievements ORDER BY id")
            )
        ).mappings().all()
        print("\nСтало:")
        for row in after:
            print(f"#{row['id']:>3}  {row['name']}")
            print(f"      {row['description']}")
        expected = {i: n for i, n, _ in plan}
        ok = all(r["name"] == expected[r["id"]] for r in after if r["id"] in expected)
        print("\nСверка: " + ("совпадает с планом" if ok else "РАСХОЖДЕНИЕ С ПЛАНОМ"))
        await engine.dispose()
        return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать в боевую БД")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
