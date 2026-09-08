# -*- coding: utf-8 -*-
"""tsk-822: убрать проверочные сдачи оператора по заданию 4187.

Что это. При проверке новой обратной связи (число совпавших строк) ответ сдавался
живьём на проде под аккаунтом оператора — дважды: через Playwright-профиль
(`live-browse.mjs`, работа 24852) и руками через Chrome (работа 24853). Обе — ответ
`28/31/33/99` при эталоне `28/31/33/30`, обе незачётные. Как улика они больше не
нужны: подтверждение есть в задаче и на скриншотах.

Что удаляется:

* `task_results` 24852 и 24853 — сами сдачи;
* `attempts` 15889 — попытка прохождения курса 1397, созданная тем же прогоном. Она
  целиком наша: других работ внутри нет, и после удаления сдач осталась бы пустым
  хвостом в истории оператора.

Проверено перед удалением (read-only): внешних ключей на `task_results` в схеме нет,
записей в `task_result_audit` по этим работам нет, `student_task_progress` по паре
(ученик 2, задание 4187) пуст. То есть удаление ничего за собой не тянет.

Скрипт намеренно узкий: id зашиты, и он падает, если под ними окажется что-то другое
(чужой ученик, другое задание, лишние работы внутри попытки).

Запуск (из корня LMS)::

    python scripts/tsk822_drop_probe_works.py                        # dry-run, ROLLBACK
    DBCHECK_OK=1 python scripts/tsk822_drop_probe_works.py --apply    # COMMIT
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

STUDENT_ID = 2  # Виктор Комлев (оператор)
TASK_ID = 4187
WORK_IDS = [24852, 24853]
ATTEMPT_ID = 15889


def _dsn() -> str:
    """Прод-DSN learn из .mcp.json (в код не хардкодим)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        for arg in cfg["mcpServers"]["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://"):
                dsn = arg.split("?")[0]
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    """Удалить проверочные сдачи и пустую попытку; без --apply — откат."""
    conn = await asyncpg.connect(_dsn())
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-822: снятие проверочных сдач {WORK_IDS} — {mode} ===\n")
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, user_id, task_id, attempt_id, score, submitted_at "
                "FROM task_results WHERE id = ANY($1::int[]) FOR UPDATE",
                WORK_IDS,
            )
            if len(rows) != len(WORK_IDS):
                raise AssertionError(f"ожидал {len(WORK_IDS)} работ, нашёл {len(rows)}")
            for r in rows:
                print(
                    f"  работа {r['id']}: ученик={r['user_id']} задание={r['task_id']} "
                    f"попытка={r['attempt_id']} балл={r['score']} "
                    f"{r['submitted_at']:%Y-%m-%d %H:%M}"
                )
                # Узкие проверки: под этими id обязано лежать ровно то, что ожидалось.
                if r["user_id"] != STUDENT_ID:
                    raise AssertionError(f"работа {r['id']}: чужой ученик {r['user_id']}")
                if r["task_id"] != TASK_ID:
                    raise AssertionError(f"работа {r['id']}: другое задание {r['task_id']}")
                if r["attempt_id"] != ATTEMPT_ID:
                    raise AssertionError(f"работа {r['id']}: другая попытка {r['attempt_id']}")
                if r["score"]:
                    raise AssertionError(f"работа {r['id']}: балл ненулевой — это не проба")

            inside = await conn.fetchval(
                "SELECT count(*) FROM task_results WHERE attempt_id = $1", ATTEMPT_ID
            )
            if inside != len(WORK_IDS):
                raise AssertionError(
                    f"в попытке {ATTEMPT_ID} работ {inside}, а не {len(WORK_IDS)} — "
                    "там есть чужая работа, попытку удалять нельзя"
                )

            await conn.execute("DELETE FROM task_results WHERE id = ANY($1::int[])", WORK_IDS)
            await conn.execute("DELETE FROM attempts WHERE id = $1", ATTEMPT_ID)

            # Проверка ВНУТРИ транзакции.
            left_works = await conn.fetchval(
                "SELECT count(*) FROM task_results WHERE id = ANY($1::int[])", WORK_IDS
            )
            left_attempt = await conn.fetchval(
                "SELECT count(*) FROM attempts WHERE id = $1", ATTEMPT_ID
            )
            others = await conn.fetchval(
                "SELECT count(*) FROM task_results WHERE user_id = $1 AND task_id = $2",
                STUDENT_ID, TASK_ID,
            )
            if left_works or left_attempt:
                raise AssertionError("удаление не сработало")
            print(
                f"\nПроверка внутри транзакции: работ осталось {left_works}, "
                f"попыток {left_attempt}; всего сдач оператора по заданию {TASK_ID}: {others}. OK"
            )

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
