# -*- coding: utf-8 -*-
"""tsk-829: переписать вердикт 12 работ, потерянных на записи ответа.

Решение оператора 08.09. Во всех 12 работах ответ совпадает с эталоном, если не
считать пробелов и буквы «ё», — то есть ученик решил ВЕРНО, а балл не получил:

* «2 102 556 498» (неразрывные пробелы из калькулятора) против «2102556498»;
* «небоскрёб» против «небоскреб»;
* «123456789» против эталона «1 2 3 4 5 6 7 8 9» — и другие того же класса.

Практического эффекта у пересчёта нет: у всех 12 учеников зачёт по этим заданиям
и так есть (каждый переписал ответ и сдал), лимита попыток у заданий нет. Это
восстановление истории — чтобы работа не числилась ошибкой ученика.

Что делает скрипт: ``score 0 -> 1``, ``is_correct false -> true``, ставит
``checked_at``/``checked_by`` (учётка оператора) и пишет причину в
``metrics.regrade_history`` — тем же форматом, что штатный regrade
(``teacher_queue_service.regrade_review``), чтобы запись была читаема существующими
инструментами. Штатный regrade вызвать нельзя: он требует уже оценённой человеком
работы (``checked_at IS NOT NULL``), а здесь все двенадцать — авто-проверка.

Побочное следствие, которое стоит знать: движок с tsk-829 воспроизводит только 5 из
12 (остальные — обратный случай «эталон через пробелы, ответ слитно», который
намеренно не засчитывается). Для этих семи разбор в истории (tsk-823) скроется
предохранителем «пересчёт разошёлся с вердиктом» — так и задумано.

Запуск (из корня LMS)::

    python scripts/tsk829_regrade_format_losses.py                       # dry-run
    DBCHECK_OK=1 python scripts/tsk829_regrade_format_losses.py --apply    # запись
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

GRADED_BY = 2  # Виктор Комлев — по чьему решению переписаны вердикты
#: Работы найдены разбором tsk-828 (ответ = эталон с точностью до пробелов и «ё»).
WORK_IDS = [4202, 9273, 11434, 15898, 16568, 16725, 17848, 17940, 18153, 18330, 18345, 21489]
COMMENT = (
    "tsk-829: ответ совпадал с эталоном с точностью до записи (пробелы, буква «ё») — "
    "ученик решил верно, балл потерян из-за нормализации. Решение оператора 08.09."
)


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
    """Переписать вердикты в одной транзакции; без --apply — откат."""
    conn = await asyncpg.connect(_dsn())
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    now = datetime.now(timezone.utc)
    print(f"=== tsk-829: пересчёт {len(WORK_IDS)} работ — {mode} ===\n")
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT tr.id, tr.task_id, tr.user_id, tr.score, tr.max_score, "
                "       tr.is_correct, tr.checked_at, tr.checked_by, tr.source_system, "
                "       tr.metrics, u.full_name "
                "FROM task_results tr JOIN users u ON u.id = tr.user_id "
                "WHERE tr.id = ANY($1::int[]) FOR UPDATE",
                WORK_IDS,
            )
            if len(rows) != len(WORK_IDS):
                raise AssertionError(f"ожидал {len(WORK_IDS)} работ, нашёл {len(rows)}")

            for r in rows:
                # Узкие проверки: переписываем только то, что и собирались.
                if r["score"]:
                    raise AssertionError(f"работа {r['id']}: балл уже {r['score']}")
                if r["is_correct"]:
                    raise AssertionError(f"работа {r['id']}: уже зачтена")
                if r["checked_by"] is not None:
                    raise AssertionError(
                        f"работа {r['id']}: её уже проверял человек ({r['checked_by']}) — "
                        "пересчёт вслепую запрещён"
                    )
                if int(r["max_score"] or 0) != 1:
                    raise AssertionError(f"работа {r['id']}: max_score={r['max_score']}, ожидал 1")

                metrics = dict(r["metrics"]) if isinstance(r["metrics"], dict) else {}
                history = list(metrics.get("regrade_history") or [])
                history.append({
                    "at": now.isoformat(),
                    "by": GRADED_BY,
                    "old_score": int(r["score"] or 0),
                    "old_is_correct": bool(r["is_correct"]),
                    "new_score": 1,
                    "new_is_correct": True,
                    "comment": COMMENT,
                })
                metrics["regrade_history"] = history

                await conn.execute(
                    "UPDATE task_results "
                    "SET score = 1, is_correct = true, checked_at = $2, checked_by = $3, "
                    "    metrics = $4::jsonb "
                    "WHERE id = $1",
                    r["id"], now, GRADED_BY, json.dumps(metrics, ensure_ascii=False),
                )
                print(f"  работа {r['id']} (задание {r['task_id']}, {r['full_name']}): 0 -> 1")

            # Проверка ВНУТРИ транзакции.
            check = await conn.fetch(
                "SELECT id, score, is_correct, checked_by, "
                "       jsonb_array_length(metrics->'regrade_history') AS events "
                "FROM task_results WHERE id = ANY($1::int[])",
                WORK_IDS,
            )
            for c in check:
                if c["score"] != 1 or not c["is_correct"]:
                    raise AssertionError(f"работа {c['id']}: пересчёт не применился")
                if c["checked_by"] != GRADED_BY or not c["events"]:
                    raise AssertionError(f"работа {c['id']}: не записана причина пересчёта")
            print(f"\nПроверка внутри транзакции: {len(check)}/{len(WORK_IDS)} переписаны, "
                  "у каждой записана причина. OK")

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
