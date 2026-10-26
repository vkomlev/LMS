"""
Закрыть 14 старых сдач заданий Arduino как проверенные (tsk-1437).

После перевода заданий на `manual_review_required=true` очередь преподавателя
перенесла августовские непроверенные сдачи из «опциональных» в «обязательные»
(ось очереди — текущий флаг задания). Решение оператора 26.10.2026: закрыть их,
сохранив автоматические оценки. Меняются только `checked_at`/`checked_by`;
`score` и `is_correct` не трогаются. Прямой UPDATE, а не эндпоинт оценки, —
чтобы не пересчитывать балл и не слать ученику уведомлений о проверке.

Запуск::

    python scripts/tsk1437_close_stale_arduino_reviews.py            # dry-run, откат
    DBCHECK_OK=1 python scripts/tsk1437_close_stale_arduino_reviews.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import List

import asyncpg

logger = logging.getLogger("tsk1437_close")

RESULT_IDS: List[int] = [15230, 15906, 17040, 17045, 17048, 17055, 17056, 17059,
                         18017, 18023, 20034, 20091, 20094, 20102]
CHECKED_BY = 2  # тот же проверяющий, что закрывал соседние сдачи этих заданий
STUDENT_ID = 4509


def _dsn() -> str:
    """Строка прод-БД из .mcp.json (не печатается)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json").read_text(encoding="utf-8"))
    return next(a for a in cfg["mcpServers"]["learn_prod_db"]["args"] if a.startswith("postgresql://"))


async def main(apply: bool) -> None:
    """Обновить в транзакции; при расхождении числа строк — откат."""
    conn = await asyncpg.connect(_dsn())
    try:
        tr = conn.transaction()
        await tr.start()
        before = await conn.fetch(
            "SELECT id, score, is_correct FROM task_results WHERE id = ANY($1) "
            "AND checked_at IS NULL AND user_id = $2 ORDER BY id", RESULT_IDS, STUDENT_ID)
        if len(before) != len(RESULT_IDS):
            raise RuntimeError(f"ожидалось {len(RESULT_IDS)} непроверенных строк, найдено {len(before)}")
        status = await conn.execute(
            "UPDATE task_results SET checked_at = now(), checked_by = $3 "
            "WHERE id = ANY($1) AND checked_at IS NULL AND user_id = $2", RESULT_IDS, STUDENT_ID, CHECKED_BY)
        after = await conn.fetch(
            "SELECT id, score, is_correct, checked_by FROM task_results WHERE id = ANY($1) ORDER BY id", RESULT_IDS)
        same = all(a["score"] == b["score"] and a["is_correct"] == b["is_correct"] for a, b in zip(after, before))
        ok = status == f"UPDATE {len(RESULT_IDS)}" and same and all(a["checked_by"] == CHECKED_BY for a in after)
        logger.info("%s; оценки не изменились: %s", status, same)
        if apply and ok:
            await tr.commit()
            logger.info("COMMIT")
        else:
            await tr.rollback()
            logger.info("ROLLBACK (%s)", "dry-run" if ok else "проверка не прошла")
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--apply", action="store_true")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main(p.parse_args().apply))
