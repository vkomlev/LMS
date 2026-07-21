"""tsk-356 — разовый фикс битого task_content->>'stem' у 50 заданий ЕГЭ.

Побочная находка tsk-354: у части заданий стем содержит служебный текст
сайта-источника вместо условия (JS-заглушка kompege.ru / шапка sdamgia.ru),
а не реальное условие задачи.

Две подгруппы:
- 10 kompege (`ext:calib:kompege:*`, курс 142) — контент копируется из твина
  `ext:d4:kompege:*` с тем же числовым ID kompege, уже присутствующего в БД
  (дубликат импорта, не требует внешнего запроса).
- 40 sdamgia (`wp_nav:*`, курсы 138-165) — новый stem получен живым ре-скрейпом
  (см. reviews/2026-07-21-tsk356-sdamgia-extracted.json), заранее провалидирован
  (40/40 не содержат ни "JavaScript", ни служебной шапки портала).

Отключает trg_set_task_order_position через session-variable, т.к. паттерн
уже используется в tsk-345 (та же таблица, тот же триггер) — хотя правка
task_content его не будит (реагирует только на order_position), оставлено
для консистентности с соседними скриптами по этой таблице.

Запуск (на прод-сервере, .env с прод DSN):
    python scripts/fix_broken_scrape_stem_tsk356.py            # dry-run
    python scripts/fix_broken_scrape_stem_tsk356.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

KOMPEGE_PAIRS = [
    (2947, 2084), (2948, 2088), (2949, 2093), (2950, 2092), (2951, 2091),
    (2952, 2089), (2953, 2090), (2954, 2086), (2955, 2085), (2956, 2087),
]

SDAMGIA_JSON = project_root / "reviews" / "2026-07-21-tsk356-sdamgia-extracted.json"


def load_sdamgia_fixes() -> list[tuple[int, str]]:
    data = json.loads(SDAMGIA_JSON.read_text(encoding="utf-8"))
    bad = [e for e in data if e.get("status") != "ok"]
    if bad:
        raise RuntimeError(f"Не все sdamgia-записи готовы к записи: {bad}")
    return [(e["task_id"], e["new_stem"]) for e in data]


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    sdamgia_fixes = load_sdamgia_fixes()
    print(f"=== tsk-356: fix broken scrape stem — {mode} ===")
    print(f"kompege pairs: {len(KOMPEGE_PAIRS)}, sdamgia fixes: {len(sdamgia_fixes)}\n")

    async with async_session_factory() as db:
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )

        # --- kompege: копия stem твина ---
        kompege_updated = 0
        for broken_id, good_id in KOMPEGE_PAIRS:
            result = await db.execute(
                text(
                    "UPDATE tasks SET task_content = jsonb_set("
                    "  task_content, '{stem}', "
                    "  to_jsonb((SELECT task_content->>'stem' FROM tasks WHERE id = :good_id))"
                    ") WHERE id = :broken_id"
                ),
                {"broken_id": broken_id, "good_id": good_id},
            )
            kompege_updated += result.rowcount
        print(f"kompege UPDATE rowcount = {kompege_updated} (ожидалось {len(KOMPEGE_PAIRS)})")

        # --- sdamgia: новый stem из живого ре-скрейпа ---
        sdamgia_updated = 0
        for task_id, new_stem in sdamgia_fixes:
            result = await db.execute(
                text(
                    "UPDATE tasks SET task_content = jsonb_set("
                    "  task_content, '{stem}', to_jsonb(CAST(:new_stem AS text))"
                    ") WHERE id = :task_id"
                ),
                {"task_id": task_id, "new_stem": new_stem},
            )
            sdamgia_updated += result.rowcount
        print(f"sdamgia UPDATE rowcount = {sdamgia_updated} (ожидалось {len(sdamgia_fixes)})")

        # --- верификация: ни один из 50 больше не содержит служебный текст ---
        all_ids = [b for b, _ in KOMPEGE_PAIRS] + [t for t, _ in sdamgia_fixes]
        still_broken = (await db.execute(
            text(
                "SELECT id FROM tasks WHERE id = ANY(:ids) AND ("
                "  task_content->>'stem' ILIKE '%JavaScript enabled%' OR "
                "  task_content->>'stem' ILIKE '%SDAM GIA%' OR "
                "  task_content->>'stem' ILIKE '%Образовательный портал%'"
                ")"
            ),
            {"ids": all_ids},
        )).fetchall()
        if still_broken:
            print(f"\nОШИБКА: {len(still_broken)} заданий всё ещё битые: {still_broken} — ROLLBACK")
            await db.rollback()
            return 1
        print(f"\nВерификация: 0 из {len(all_ids)} всё ещё содержат служебный текст — OK")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения сохранены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
