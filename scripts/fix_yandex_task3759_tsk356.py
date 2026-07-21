"""tsk-356 (follow-up) — восстановить task_content->>'stem' для id=3759 (yandex SPA-каркас).

Единственное оставшееся задание из 51 (см. scripts/fix_broken_scrape_stem_tsk356.py —
там уже применены 50/51: 10 kompege + 40 sdamgia). Условие получено вручную через
Claude in Chrome (учётка Виктора, authenticated), examTaskId=d5e3d14e-2d2c-42a6-9cbe-8c848c4a4c6f
(https://education.yandex.ru/ege/inf/training/7/task/1) — обычный HTTP/API-запрос без
браузера не отдаёт контент (SPA), поэтому не вошло в основной batch-фикс.

Запуск (на прод-сервере, .env с прод DSN):
    python scripts/fix_yandex_task3759_tsk356.py            # dry-run
    python scripts/fix_yandex_task3759_tsk356.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

TASK_ID = 3759
NEW_STEM = (
    "<p>Картинка занимает 840 бит в памяти, за сколько секунд она будет "
    "передана в город А, если скорость передачи составляет 1 байт "
    "в секунду?</p>"
)


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-356 follow-up: fix yandex task {TASK_ID} — {mode} ===\n")

    async with async_session_factory() as db:
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )

        before = (await db.execute(
            text("SELECT left(task_content->>'stem', 80) FROM tasks WHERE id = :id"),
            {"id": TASK_ID},
        )).scalar()
        print(f"BEFORE: {before}")

        result = await db.execute(
            text(
                "UPDATE tasks SET task_content = jsonb_set("
                "  task_content, '{stem}', to_jsonb(CAST(:new_stem AS text))"
                ") WHERE id = :id"
            ),
            {"id": TASK_ID, "new_stem": NEW_STEM},
        )
        print(f"UPDATE rowcount = {result.rowcount} (ожидалось 1)")

        after = (await db.execute(
            text("SELECT left(task_content->>'stem', 80) FROM tasks WHERE id = :id"),
            {"id": TASK_ID},
        )).scalar()
        print(f"AFTER: {after}")

        if "RadioButton" in (after or ""):
            print("\nОШИБКА: всё ещё битый stem — ROLLBACK")
            await db.rollback()
            return 1

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
