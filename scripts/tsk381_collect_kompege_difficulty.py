"""tsk-381 — сбор оценки сложности с kompege для активных заданий LMS (READ-ONLY).

Канон 3 (вспомогательный): оценка внешнего сайта. У kompege сложность отдаётся
публичным API без авторизации: ``GET https://kompege.ru/api/v1/task/<id>`` → числовое
поле ``difficulty``. Подписи на сайте: базовая / средняя / сложная / гроб.

Скрипт ничего не пишет: собирает пары «задание LMS ↔ оценка источника», печатает
распределение и расхождения. Решение о применении принимает оператор.

Запуск:
    python scripts/tsk381_collect_kompege_difficulty.py
    python scripts/tsk381_collect_kompege_difficulty.py --limit 20 --out out/kompege.json
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk381.kompege")

_LMS_ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://kompege.ru/api/v1/task/{task_id}"

# difficulty_id в LMS.
EASY, NORMAL, HARD = 2, 3, 4
DIFFICULTY_NAME = {1: "THEORY", 2: "EASY", 3: "NORMAL", 4: "HARD", 5: "PROJECT"}

# Гипотеза шкалы источника; подтверждается оператором до применения.
KOMPEGE_SCALE = {0: "базовая", 1: "средняя", 2: "сложная", 3: "гроб"}
KOMPEGE_TO_LMS = {0: EASY, 1: NORMAL, 2: HARD, 3: HARD}


def _dsn() -> str:
    """DSN прод-LMS из .mcp.json. Секрет не логируется."""
    config = json.loads((_LMS_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    for arg in config["mcpServers"]["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg.split("?")[0]
    raise RuntimeError("DSN learn_prod_db не найден")


def load_tasks() -> list[dict[str, Any]]:
    """Активные задания LMS с источником kompege и числовым id источника."""
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            r"""
            SELECT id, external_uid, course_id, difficulty_id,
                   (regexp_match(external_uid, '(\d+)$'))[1] AS source_id
            FROM tasks
            WHERE is_active AND external_uid ILIKE '%kompege%'
            ORDER BY id
            """
        )
        return [
            {
                "id": r[0], "external_uid": r[1], "course_id": r[2],
                "difficulty_id": r[3], "source_id": r[4],
            }
            for r in cur.fetchall()
        ]


def fetch_difficulty(source_id: str, *, timeout: int = 20) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """(difficulty, number, ошибка) из API kompege по числовому id задания."""
    request = urllib.request.Request(
        API_URL.format(task_id=source_id),
        headers={"User-Agent": "Mozilla/5.0 (LMS content audit)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, None, f"HTTP {exc.code}"
    except Exception as exc:  # сеть/JSON — фиксируем и идём дальше
        return None, None, str(exc)[:80]
    if not isinstance(payload, dict):
        return None, None, "ответ не объект"
    difficulty = payload.get("difficulty")
    return (
        difficulty if isinstance(difficulty, int) else None,
        payload.get("number") if isinstance(payload.get("number"), int) else None,
        None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tsk-381 сбор сложности kompege (read-only)")
    parser.add_argument("--out", default="out/tsk381_kompege.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.25, help="пауза между запросами, сек")
    args = parser.parse_args(argv)

    tasks = load_tasks()
    if args.limit is not None:
        tasks = tasks[: args.limit]
    logger.info("активных заданий kompege в LMS: %d", len(tasks))

    cache: dict[str, tuple[Optional[int], Optional[int], Optional[str]]] = {}
    rows: list[dict[str, Any]] = []
    errors = 0

    for index, task in enumerate(tasks, 1):
        source_id = task["source_id"]
        if source_id is None:
            rows.append({**task, "kompege_difficulty": None, "error": "id источника не разобран"})
            errors += 1
            continue
        if source_id not in cache:
            cache[source_id] = fetch_difficulty(source_id)
            time.sleep(args.delay)
        difficulty, number, error = cache[source_id]
        if error:
            errors += 1
        expected = KOMPEGE_TO_LMS.get(difficulty) if difficulty is not None else None
        rows.append({
            **task,
            "kompege_difficulty": difficulty,
            "kompege_label": KOMPEGE_SCALE.get(difficulty) if difficulty is not None else None,
            "kompege_number": number,
            "expected_difficulty_id": expected,
            "current_level": DIFFICULTY_NAME.get(task["difficulty_id"]),
            "verdict": (
                "нет оценки источника" if expected is None
                else "совпадает" if expected == task["difficulty_id"]
                else "РАСХОЖДЕНИЕ"
            ),
            "error": error,
        })
        if index % 25 == 0:
            logger.info("  обработано %d/%d", index, len(tasks))

    distribution: dict[str, int] = {}
    for row in rows:
        key = f"{row['kompege_difficulty']} ({row['kompege_label']})"
        distribution[key] = distribution.get(key, 0) + 1

    logger.info("")
    logger.info("=== Распределение оценок источника ===")
    for key, count in sorted(distribution.items(), key=lambda kv: str(kv[0])):
        logger.info("  %-20s %d", key, count)

    mismatches = [r for r in rows if r["verdict"] == "РАСХОЖДЕНИЕ"]
    logger.info("")
    logger.info("совпадает: %d | расхождений: %d | без оценки: %d | ошибок сети: %d",
                sum(1 for r in rows if r["verdict"] == "совпадает"), len(mismatches),
                sum(1 for r in rows if r["verdict"] == "нет оценки источника"), errors)

    logger.info("")
    logger.info("=== Расхождения (поле LMS ≠ оценка kompege) ===")
    for row in mismatches:
        logger.info(
            "  id=%-5s %-40s курс=%-5s поле=%s -> источник=%s (%s), №%s",
            row["id"], row["external_uid"], row["course_id"], row["current_level"],
            DIFFICULTY_NAME.get(row["expected_difficulty_id"]), row["kompege_label"],
            row["kompege_number"],
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"task": "tsk-381", "source": "kompege", "scale_hypothesis": KOMPEGE_SCALE,
                    "distribution": distribution, "rows": rows},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("")
    logger.info("отчёт: %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
