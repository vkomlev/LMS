"""tsk-004 Этап 1.5 — удалить 8 text-материалов «Задания…» Группы A.

Группа A — материалы внутри подкурсов курса 88 «Python для ЕГЭ»:
дублируют содержимое уже существующих tasks в БД и подлежат удалению
(оператор подтвердил после ручного отбора). Группа B (27 шт. в
курсах-навигаторах «Задание N ЕГЭ») оставлена нетронутой — там
контент с внешними ссылками kompege.ru/inf-ege.sdamgia.ru.

Запуск:
    python scripts/delete_zadania_group_a.py            # dry-run
    python scripts/delete_zadania_group_a.py --apply    # COMMIT
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

GROUP_A_IDS = [200, 211, 218, 235, 245, 283, 292, 309]
# Контроль, что Группа B НЕ затронута
GROUP_B_IDS = [
    324, 357, 364, 367, 368, 372, 377, 381, 384, 386,
    388, 390, 392, 402, 405, 406, 408, 410, 411, 414,
    417, 425, 434, 438, 443, 446,
]


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== Delete zadania Group A — {mode} ===\n")

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, course_id, title, "
                    "COALESCE(length(content->>'text'), 0) AS text_len "
                    "FROM materials WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": GROUP_A_IDS},
            )
        ).fetchall()
        print(f"BEFORE: найдено {len(rows)} материалов из {len(GROUP_A_IDS)} ожидаемых")
        for r in rows:
            print(f"  id={r.id:>3} course_id={r.course_id:>3} text_len={r.text_len:>6}  {r.title!r}")

        result = await db.execute(
            text("DELETE FROM materials WHERE id = ANY(:ids)"),
            {"ids": GROUP_A_IDS},
        )
        print(f"\nDELETE rowcount = {result.rowcount}")

        n_after = (
            await db.execute(
                text("SELECT COUNT(*) FROM materials WHERE id = ANY(:ids)"),
                {"ids": GROUP_A_IDS},
            )
        ).scalar()
        print(f"AFTER Group A: осталось {n_after} (ожидаем 0)")

        n_b = (
            await db.execute(
                text("SELECT COUNT(*) FROM materials WHERE id = ANY(:ids)"),
                {"ids": GROUP_B_IDS},
            )
        ).scalar()
        print(f"Group B intact: {n_b}/{len(GROUP_B_IDS)} (ожидаем {len(GROUP_B_IDS)})")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения сохранены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT. Без флага — dry-run.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
