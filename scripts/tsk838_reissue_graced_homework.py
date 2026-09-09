# -*- coding: utf-8 -*-
"""tsk-838: переиздать домашнюю работу, набранную до фикса прощённых элементов.

Правило tsk-692 прощает ученику содержимое, добавленное в курс ПОСЛЕ того, как
он прошёл тему. Выдача про это правило не знала (исправлено 08.09, `d9693e6`) —
и наборы, выданные ДО фикса, до сих пор состоят из заданий давно закрытых тем.
Сами они не обновятся: автовыдача переиздаёт только после следующего занятия.

Решение оператора 09.09: переиздать тем, у кого лишнее составляет больше
половины набора, а сделано почти ничего. Остальным — дождаться автовыдачи:
сбрасывать набор с наработанным прогрессом ради двух лишних пунктов вредно.

Прежние срок, источник, автор и занятие сохраняются: для ученика это тот же
набор, только без чужих заданий. Выполненное не теряется — оно считается по
факту решения, а не хранится в выдаче.

Запуск (после протокола `/db-check`):
    DBCHECK_OK=1 python scripts/tsk838_reissue_graced_homework.py --apply
Без `--apply` — только показывает, что получится.
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

#: Кого переиздаём (решение оператора 09.09). Номера действующих выдач, а не
#: учеников: если набор успел смениться сам, выдачи уже не будет — и трогать
#: ученика не нужно.
ASSIGNMENT_IDS = [106, 108, 126, 91, 130, 114]

_CURRENT_SQL = """
SELECT ha.id, ha.student_id, u.full_name, ha.due_at, ha.source,
       ha.issued_by, ha.occurrence_id,
       (SELECT count(*) FROM homework_item hi WHERE hi.homework_id = ha.id) AS items
  FROM homework_assignment ha
  JOIN users u ON u.id = ha.student_id
 WHERE ha.id = ANY(:ids) AND ha.cancelled_at IS NULL
 ORDER BY ha.id
"""


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

    from app.services import homework_service
    from app.services.content_grace_service import compute_graced_items

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        rows = (
            await db.execute(text(_CURRENT_SQL), {"ids": ASSIGNMENT_IDS})
        ).mappings().all()
        missing = set(ASSIGNMENT_IDS) - {int(r["id"]) for r in rows}
        if missing:
            print(f"Уже не действуют (обновились сами): {sorted(missing)}")

        print(f"\n{'ученик':<28}{'было':>6}{'станет':>8}  срок")
        planned: list[dict] = []
        for r in rows:
            fresh = await homework_service._next_items(
                db, student_id=int(r["student_id"]), limit=int(r["items"]),
            )
            planned.append({"row": r, "fresh": len(fresh)})
            print(
                f"{r['full_name'][:27]:<28}{r['items']:>6}{len(fresh):>8}  "
                f"{r['due_at']:%d.%m %H:%M}"
            )

        empty = [p for p in planned if p["fresh"] == 0]
        if empty:
            print(
                "\nВНИМАНИЕ: у "
                + ", ".join(p["row"]["full_name"] for p in empty)
                + " задавать нечего — программа пройдена. Их пропускаем."
            )

        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            await engine.dispose()
            return 0

        done = 0
        for item in planned:
            r = item["row"]
            if item["fresh"] == 0:
                continue
            await homework_service.issue(
                db,
                student_id=int(r["student_id"]),
                due_at=r["due_at"],
                source=r["source"],
                issued_by=r["issued_by"],
                occurrence_id=r["occurrence_id"],
                note="Состав обновлён: убраны задания из уже пройденных тем.",
            )
            done += 1
        await db.commit()
        print(f"\nПереиздано выдач: {done}")

        # Верификация: в новых наборах прощённых быть не должно.
        bad_total = 0
        for item in planned:
            sid = int(item["row"]["student_id"])
            current = await homework_service.get_current(db, student_id=sid)
            if current is None:
                continue
            roots = (
                await db.execute(
                    text(
                        "SELECT course_id FROM user_courses "
                        " WHERE user_id = :s AND is_active = true"
                    ),
                    {"s": sid},
                )
            ).scalars().all()
            graced_tasks: set[int] = set()
            graced_materials: set[int] = set()
            for root in roots:
                g = await compute_graced_items(db, sid, int(root))
                graced_tasks |= set(g.tasks)
                graced_materials |= set(g.materials)
            bad = [
                i for i in current["items"]
                if (i["kind"] == "task" and int(i["item_id"]) in graced_tasks)
                or (i["kind"] == "material" and int(i["item_id"]) in graced_materials)
            ]
            bad_total += len(bad)
            print(
                f"  {item['row']['full_name'][:27]:<28}"
                f"{len(current['items']):>3} элем., прощённых {len(bad)}"
            )

        if bad_total:
            print(f"\nОШИБКА: прощённые остались ({bad_total}) — разобраться")
            await engine.dispose()
            return 1
        print("\nГотово: в новых наборах прощённых нет.")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="записать в базу")
    sys.exit(asyncio.run(main(parser.parse_args().apply)))
