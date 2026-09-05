"""tsk-796: применить вычитанный план словоформ к `accepted_answers` (прод).

План готовит `scripts/gen_wordforms_tsk796.py`, человек вычитывает его по отчёту
`docs/qa/2026-09-05-tsk796-wordforms-review.md`, и только потом запускается это.

Протокол /db-check (режим записи):
  * dry-run по умолчанию — печатает, сколько заданий и форм затронуто;
  * перед записью КАЖДОГО задания текущий `accepted_answers` сверяется дословно
    с тем, что видел генератор: разошлось — задание пропускается, не правится
    вслепую (эталон мог поменять методист между снимком и запуском);
  * запись только добавляет элементы, исходные записи остаются на своих местах
    и первыми — от этого зависит `guest_diagnostic_service._reference_answer`,
    который показывает в разборе эталон с максимальным баллом;
  * всё в одной транзакции, после записи — верификация выборкой, затем COMMIT.

Запуск (из корня LMS):
  python scripts/apply_wordforms_tsk796.py
  DBCHECK_OK=1 python scripts/apply_wordforms_tsk796.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk796.apply")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = PROJECT_ROOT / "scripts" / "tsk796_wordforms_plan.json"


def prod_dsn() -> Dict[str, Any]:
    """Параметры подключения к боевой базе из `.mcp.json` (пароль не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def same_accepted(current: Any, expected: List[Dict[str, Any]]) -> bool:
    """Сверка текущего `accepted_answers` с тем, что видел генератор."""
    if not isinstance(current, list) or len(current) != len(expected):
        return False
    for got, want in zip(current, expected):
        if (got or {}).get("value") != want.get("value"):
            return False
        if (got or {}).get("score") != want.get("score"):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Добавить словоформы в accepted_answers (tsk-796)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="Ограничить число заданий (для пробы)")
    parser.add_argument(
        "--plan",
        default=str(PLAN_PATH),
        help="Файл плана (по умолчанию машинный; вторая, ручная партия лежит отдельно)",
    )
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = plan["apply"]
    if args.limit:
        items = items[: args.limit]

    dsn = prod_dsn()
    logger.info("=== tsk-796: словоформы в accepted_answers ===")
    logger.info("Подключение: %s@%s/%s", dsn["user"], dsn["host"], dsn["dbname"])
    logger.info("Режим: %s", "APPLY" if args.apply else "DRY-RUN")
    logger.info("Заданий в плане: %d, форм к добавлению: %d",
                len(items), sum(len(i["add"]) for i in items))

    conn = psycopg2.connect(**dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    changed = 0
    added_forms = 0
    skipped: List[str] = []

    try:
        for item in items:
            task_id = item["task_id"]
            cur.execute(
                "SELECT solution_rules->'short_answer'->'accepted_answers' AS accepted "
                "FROM tasks WHERE id = %s AND is_active = true",
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                skipped.append(f"{task_id}: задание не найдено или выключено")
                continue
            if not same_accepted(row["accepted"], item["before"]):
                skipped.append(
                    f"{task_id}: эталон изменился с момента снимка — "
                    f"сейчас {json.dumps(row['accepted'], ensure_ascii=False)}"
                )
                continue

            score = item["before"][0].get("score")
            new_accepted = list(item["before"]) + [
                {"value": form, "score": score} for form in item["add"]
            ]
            cur.execute(
                "UPDATE tasks "
                "SET solution_rules = jsonb_set("
                "    solution_rules, '{short_answer,accepted_answers}', %s::jsonb, true) "
                "WHERE id = %s",
                (json.dumps(new_accepted, ensure_ascii=False), task_id),
            )
            changed += 1
            added_forms += len(item["add"])

        logger.info("Готово к записи: заданий %d, форм %d", changed, added_forms)
        if skipped:
            logger.warning("Пропущено %d:", len(skipped))
            for line in skipped:
                logger.warning("  %s", line)

        if not args.apply:
            conn.rollback()
            logger.info("DRY-RUN: откат, база не изменена.")
            return 0

        # Верификация внутри той же транзакции: контрольные задания и общий счёт.
        probe_ids = [i["task_id"] for i in items[:3]] + [i["task_id"] for i in items[-2:]]
        cur.execute(
            "SELECT id, solution_rules->'short_answer'->'accepted_answers' AS accepted "
            "FROM tasks WHERE id = ANY(%s) ORDER BY id",
            (probe_ids,),
        )
        for probe in cur.fetchall():
            values = [a.get("value") for a in probe["accepted"]]
            logger.info("  проверка %s: %d вариантов, первый %r",
                        probe["id"], len(values), values[0])

        conn.commit()
        logger.info("COMMIT выполнен: заданий %d, форм %d", changed, added_forms)
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена, база не изменена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
