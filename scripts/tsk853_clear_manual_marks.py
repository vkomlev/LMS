"""tsk-853: снять пометку ручной правки, поставленную по ошибочному основанию.

Зачем. Пометка `content_provenance.source='manual_script'` защищает задание от
переиздания курса: импорт из ContentBackbone его не перезапишет. Ставится она по
сверке — «содержимое в LMS разошлось с тем, что отправляли». Если расхождение
оказалось мнимым, пометка вредна: задание молча перестаёт получать правки из
источника, и автор курса об этом не узнает.

Случай, ради которого написан скрипт (задание 5278, пробное занятие IT-школы):
публиковали из рабочего дерева, а коммит с той же правкой лёг через 44 секунды
после публикации. Снимок сверки берёт последнюю версию, закоммиченную ДО
публикации, и потому считал отправленным то, что автор к тому моменту уже
переписал. В LMS лежит ровно актуальный источник — правки человека там нет.
Сборка снимка исправлена в CB (`ambiguous_publish`), эта правка убирает
последствие в базе.

Снимается только своя пометка: `manual_script`, поставленная прогонами tsk-760.
`manual_web` (кабинет методиста) не трогается — она поставлена человеком и
точнее любой сверки.

Протокол (db-check): по умолчанию читает и показывает план; запись — только с
`--apply`, одной транзакцией, с проверкой после.

Usage:
    python scripts/tsk853_clear_manual_marks.py --uid "authored:it-shkola:probnoe-6-9#q4"
    DBCHECK_OK=1 python scripts/tsk853_clear_manual_marks.py --uid "..." --apply
    DBCHECK_OK=1 python scripts/tsk853_clear_manual_marks.py --input uids.json --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: Снимаем только пометку, поставленную нашими прогонами сверки.
_OWN_SOURCE = "manual_script"


def prod_dsn() -> str:
    """DSN боевой базы из `.mcp.json` (пароль не печатаем).

    `DATABASE_URL` в локальном `.env` смотрит на dev-копию — старый снимок
    прода, на котором правка выглядела бы успешной, ничего не изменив.
    """
    mcp = json.loads((_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    return str(mcp["mcpServers"]["learn_prod_db"]["args"][-1])


def load_uids(args: argparse.Namespace) -> list[str]:
    """Ключи заданий: из аргументов или из JSON-файла `{"uids": [...]}`."""
    uids = list(args.uid or [])
    if args.input:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        uids.extend(data.get("uids") or [])
    if not uids:
        raise SystemExit("нужен --uid или --input")
    return sorted(dict.fromkeys(uids))


async def run(uids: list[str], *, apply: bool, reason: str) -> int:
    dsn = prod_dsn()
    parsed = urlparse(dsn)
    print(f"База: {parsed.username}@{parsed.hostname}:{parsed.port or 5432}{parsed.path}\n")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT id, external_uid, is_active, content_provenance
            FROM tasks WHERE external_uid = ANY($1::text[])
            """,
            uids,
        )
        found = {r["external_uid"]: r for r in rows}
        missing = [u for u in uids if u not in found]

        def source_of(row) -> str | None:
            prov = row["content_provenance"]
            if isinstance(prov, str):
                try:
                    prov = json.loads(prov)
                except json.JSONDecodeError:
                    return None
            return prov.get("source") if isinstance(prov, dict) else None

        to_clear = [u for u, r in found.items() if source_of(r) == _OWN_SOURCE]
        foreign = [u for u, r in found.items() if source_of(r) not in (None, _OWN_SOURCE)]
        already = [u for u, r in found.items() if source_of(r) is None]

        print(f"Во входе:                       {len(uids)}")
        print(f"  найдено в базе:               {len(found)}")
        print(f"  нет в базе (пропускаем):      {len(missing)}")
        print(f"  пометка кабинета (не трогаем):{len(foreign)}")
        print(f"  пометки уже нет:              {len(already)}")
        print(f"  будет снято:                  {len(to_clear)}")
        for uid in to_clear[:20]:
            print(f"    · {uid} (id={found[uid]['id']})")

        if not apply:
            print("\nDRY-RUN. Запись не выполнялась. Для записи добавьте --apply.")
            return 0
        if not to_clear:
            print("\nСнимать нечего.")
            return 0

        async with conn.transaction():
            updated = await conn.fetch(
                """
                UPDATE tasks SET content_provenance = NULL
                WHERE external_uid = ANY($1::text[])
                  AND content_provenance->>'source' = $2
                RETURNING external_uid
                """,
                to_clear,
                _OWN_SOURCE,
            )
        print(f"\nСнято пометок: {len(updated)} (причина: {reason})")

        left = await conn.fetchval(
            """
            SELECT count(*) FROM tasks
            WHERE external_uid = ANY($1::text[]) AND content_provenance IS NOT NULL
            """,
            to_clear,
        )
        print(f"Проверка: осталось с пометкой {left} из {len(to_clear)} (ожидаем 0)")
        return 0 if left == 0 else 1
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="tsk-853: снять ошибочную пометку ручной правки")
    ap.add_argument("--uid", action="append", help="ключ задания (можно несколько раз)")
    ap.add_argument("--input", help="JSON-файл со списком: {\"uids\": [...]}")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    ap.add_argument(
        "--reason",
        default="расхождение оказалось мнимым: снимок сверки хранил версию до публикации",
        help="что записать в отчёт (в базу не пишется — пометка снимается целиком)",
    )
    args = ap.parse_args()
    return asyncio.run(run(load_uids(args), apply=args.apply, reason=args.reason))


if __name__ == "__main__":
    raise SystemExit(main())
