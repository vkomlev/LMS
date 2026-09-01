"""tsk-760: пометить задания, содержимое которых в LMS разошлось с источником.

Зачем. Переиздание курса из ContentBackbone (ADR-0055 CB) обновляет задания,
но обязано не трогать то, что здесь уже поправили. Отличить одно от другого
было нечем: `content_provenance` заполняет только кабинет методиста (на 01.09 —
3 задания из 7749), `updated_at` появился лишь этой задачей, а `task_audit` до
неё не видел правок условия. Про всё, что правили раньше, база молчала.

Ответ нашёлся не в LMS, а в расхождении: CB хранит содержимое, которое
отправлял, и сверка (`python -m monolith lms-drift-audit` на стороне CB)
показывает, где версия в LMS отличается от отправленной. Такое расхождение —
след работы, сделанной уже здесь: правки методиста, чистка утечки ответа,
перенос картинок в CAS, восстановление разметки. Кто именно её сделал — человек
руками или сервисный скрипт — для переиздания неважно: и то и другое пропадёт,
если сверху лечь старым снимком из источника.

Скрипт проставляет таким заданиям `content_provenance` с
`source='manual_script'` — источник, который `TasksService` уважает наравне с
`manual_web` (см. `HUMAN_EDIT_SOURCES`). После этого импорт перестаёт
перезаписывать у них `task_content` и `solution_rules`.

Вход — JSON сверки CB: `{"uids": {"<external_uid>": {...}}}` либо
`{"rows": [{"external_uid": ..., "status": "edited_in_lms"}]}`.

Протокол (db-check): по умолчанию читает и показывает план; запись — только с
`--apply`, одной транзакцией, с проверкой после. Существующую пометку
`manual_web` не трогает: она поставлена человеком в кабинете и точнее нашей.

Usage:
    python scripts/tsk760_mark_manual_edits.py --input out/candidates.json
    python scripts/tsk760_mark_manual_edits.py --input out/candidates.json --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Помечаем ОБА поля разом: правило проверки и условие связаны перекрёстной
# валидацией, и защита одного без другого их рассинхронизирует (та же логика,
# что в TasksService._manually_edited_task_fields).
_MARKED_FIELDS = ["task_content", "solution_rules"]
_SOURCE = "manual_script"


def load_uids(path: Path) -> list[str]:
    """Ключи заданий из отчёта сверки CB (оба формата отчёта)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("uids"), dict):
        return sorted(data["uids"].keys())
    rows = data.get("rows") or []
    return sorted(
        r["external_uid"]
        for r in rows
        if isinstance(r, dict) and r.get("status") == "edited_in_lms" and r.get("external_uid")
    )


def _provenance(reason: str) -> dict[str, Any]:
    return {
        "source": _SOURCE,
        "edited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "edited_by": "tsk-760",
        "fields": list(_MARKED_FIELDS),
        "reason": reason,
    }


async def run(input_path: Path, *, apply: bool, reason: str, limit: int | None) -> int:
    load_dotenv(_ROOT / ".env")
    dsn = (os.environ.get("DATABASE_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    if not dsn:
        print("ERROR: DATABASE_URL не задан", file=sys.stderr)
        return 2

    uids = load_uids(input_path)
    if limit is not None:
        uids = uids[:limit]
    if not uids:
        print("Во входном файле нет заданий со статусом edited_in_lms — делать нечего.")
        return 0

    conn = await asyncpg.connect(dsn)
    try:
        # 1. ЧТЕНИЕ: что реально лежит в базе по этим ключам.
        rows = await conn.fetch(
            """
            SELECT id, external_uid, is_active, content_provenance
            FROM tasks
            WHERE external_uid = ANY($1::text[])
            """,
            uids,
        )
        found = {r["external_uid"]: r for r in rows}
        missing = [u for u in uids if u not in found]

        already_web = [u for u, r in found.items() if _source_of(r) == "manual_web"]
        already_script = [u for u, r in found.items() if _source_of(r) == _SOURCE]
        to_mark = [
            u for u, r in found.items()
            if _source_of(r) not in ("manual_web", _SOURCE)
        ]

        print(f"Во входном файле:                 {len(uids)}")
        print(f"  найдено в базе:                 {len(found)}")
        print(f"  нет в базе (пропускаем):        {len(missing)}")
        print(f"  уже помечено кабинетом:         {len(already_web)} (не трогаем)")
        print(f"  уже помечено этим скриптом:     {len(already_script)}")
        print(f"  будет помечено:                 {len(to_mark)}")
        for uid in to_mark[:10]:
            print(f"    · {uid} (id={found[uid]['id']})")
        if len(to_mark) > 10:
            print(f"    … и ещё {len(to_mark) - 10}")

        if not apply:
            print("\nDRY-RUN. Запись не выполнялась. Для записи добавьте --apply.")
            return 0
        if not to_mark:
            print("\nПомечать нечего.")
            return 0

        # 2. ЗАПИСЬ одной транзакцией.
        payload = json.dumps(_provenance(reason), ensure_ascii=False)
        async with conn.transaction():
            updated = await conn.fetch(
                """
                UPDATE tasks
                SET content_provenance = $2::jsonb
                WHERE external_uid = ANY($1::text[])
                RETURNING external_uid
                """,
                to_mark,
                payload,
            )
        print(f"\nПомечено: {len(updated)}")

        # 3. ВЕРИФИКАЦИЯ после записи.
        check = await conn.fetchval(
            """
            SELECT count(*) FROM tasks
            WHERE external_uid = ANY($1::text[])
              AND content_provenance->>'source' = $2
            """,
            to_mark,
            _SOURCE,
        )
        print(f"Проверка: помеченных в базе {check} из {len(to_mark)}")
        return 0 if check == len(to_mark) else 1
    finally:
        await conn.close()


def _source_of(row: Any) -> str | None:
    prov = row["content_provenance"]
    if isinstance(prov, str):
        try:
            prov = json.loads(prov)
        except json.JSONDecodeError:
            return None
    return prov.get("source") if isinstance(prov, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description="tsk-760: пометить задания, правленные вне источника")
    ap.add_argument("--input", required=True, help="JSON-отчёт сверки CB (lms-drift-audit)")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    ap.add_argument(
        "--reason",
        default="содержимое в LMS разошлось с последним снимком ContentBackbone (сверка tsk-760)",
        help="что записать в content_provenance.reason",
    )
    ap.add_argument("--limit", type=int, default=None, help="ограничить число заданий (проба)")
    args = ap.parse_args()
    return asyncio.run(
        run(Path(args.input), apply=args.apply, reason=args.reason, limit=args.limit)
    )


if __name__ == "__main__":
    raise SystemExit(main())
