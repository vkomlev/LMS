"""tsk-1158: задание 564 — пробелы вокруг «+» сделать необязательными и зачесть две сдачи.

Что и почему. Задание 564 («Генерация арифметического выражения») проверяется регуляркой
`^[1-9][0-9]{0,2} \\+ [1-9][0-9]{0,2}$`: пробелы вокруг «+» обязательны. Ответы «32+86»
(работа 36812) и «79+10» (работа 33302) по сути верны, но получили незачёт за формат.
Новое правило — `\\s*` вокруг «+».

Шаги (одна транзакция):
1. Сверить «до» дословно: старая регулярка, состояние обеих работ.
2. Прогнать настоящим `CheckingService.check_task` ВСЕ сдачи 564 с текстом ответа по
   новому правилу: прежние зачёты остаются зачётами, меняются ровно 36812 и 33302.
3. Записать правило в `tasks.solution_rules`, вердикты двух работ — как у штатной
   дооценки (`score`, `is_correct`, `checked_at`, `checked_by`, `metrics.comment`;
   образец `tsk1148_regrade_stale_last_verdicts.py`).
Обе работы — не последние сдачи (ученики позже сдали то же задание верно), поэтому
состояние курса не пересчитывается: движок судит по последней сдаче.

Запуск (из корня LMS):
  python scripts/tsk1158_task564_regex_optional_spaces.py
  DBCHECK_OK=1 python scripts/tsk1158_task564_regex_optional_spaces.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

logger = logging.getLogger("tsk1158")

TASK_ID = 564
OLD_REGEX = r"^[1-9][0-9]{0,2} \+ [1-9][0-9]{0,2}$"
NEW_REGEX = r"^[1-9][0-9]{0,2}\s*\+\s*[1-9][0-9]{0,2}$"
#: Работы к зачёту и их ожидаемые ответы «до».
TARGETS: dict[int, dict[str, Any]] = {
    36812: {"user_id": 4503, "answer": "32+86"},
    33302: {"user_id": 4524, "answer": "79+10"},
}
CHECKED_BY = 2
VERDICT_COMMENT = (
    "tsk-1158: ответ верный, незачёт был только за отсутствие пробелов вокруг «+». "
    "Правило проверки исправлено, вердикт этой работы пересчитан."
)


def _prod_async_dsn() -> str:
    """Боевой DSN из .mcp.json в форме для asyncpg. Пароль не печатается."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    dsn: str = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


async def run(apply: bool) -> int:
    """Сверить, перепроверить движком и (при apply) записать.

    :returns: код выхода: 0 — успех, 1 — отказ по сверке.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.schemas.checking import StudentAnswer
    from app.schemas.task_content import TaskContent
    from app.services.checking_service import CheckingService

    engine = create_async_engine(_prod_async_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = CheckingService()
    print(f"Режим: {'ЗАПИСЬ' if apply else 'DRY-RUN'}")
    try:
        async with factory() as db:
            task = (await db.execute(text(
                "SELECT task_content, solution_rules, max_score FROM tasks WHERE id = :id FOR UPDATE"
            ), {"id": TASK_ID})).mappings().one()
            old_rules = task["solution_rules"]
            if old_rules["short_answer"]["regex"] != OLD_REGEX:
                print(f"ОТКАЗ: регулярка «до» не совпала: {old_rules['short_answer']['regex']!r}")
                return 1
            new_rules = copy.deepcopy(old_rules)
            new_rules["short_answer"]["regex"] = NEW_REGEX
            content = TaskContent.model_validate(task["task_content"])
            rules = service.build_solution_rules(new_rules, fallback_max_score=task["max_score"] or 1)

            rows = (await db.execute(text(
                "SELECT id, user_id, is_correct, score, max_score, checked_by, answer_json"
                " FROM task_results WHERE task_id = :id ORDER BY id"
            ), {"id": TASK_ID})).mappings().all()
            flips: dict[int, int] = {}
            checked = 0
            for r in rows:
                value = ((r["answer_json"] or {}).get("response") or {}).get("value")
                if value is None:
                    continue  # ручной зачёт без текста ответа — движку нечего проверять
                checked += 1
                res = service.check_task(content, rules, StudentAnswer.model_validate(r["answer_json"]))
                mark = "" if bool(res.is_correct) == bool(r["is_correct"]) else "  <- МЕНЯЕТСЯ"
                print(f"  {r['id']}: {value!r} было {r['is_correct']} → движок {res.is_correct}{mark}")
                if r["is_correct"] and not res.is_correct:
                    print(f"ОТКАЗ: прежний зачёт {r['id']} стал бы незачётом")
                    return 1
                if res.is_correct and not r["is_correct"]:
                    flips[int(r["id"])] = res.score
                    exp = TARGETS.get(int(r["id"]))
                    if not (exp and exp["user_id"] == r["user_id"] and exp["answer"] == value
                            and r["score"] == 0 and r["checked_by"] is None
                            and res.max_score == r["max_score"]):
                        print(f"ОТКАЗ: неожиданная смена вердикта у {r['id']}")
                        return 1
            if set(flips) != set(TARGETS):
                print(f"ОТКАЗ: меняются {sorted(flips)}, ожидались {sorted(TARGETS)}")
                return 1
            print(f"Проверено движком: {checked} сдач с текстом; меняются {sorted(flips)}")

            if not apply:
                print("DRY-RUN: ничего не записано.")
                await db.rollback()
                return 0

            now = datetime.now(timezone.utc)
            res = await db.execute(text(
                "UPDATE tasks SET solution_rules = CAST(:r AS jsonb), updated_at = :now"
                " WHERE id = :id AND solution_rules->'short_answer'->>'regex' = :old"
            ), {"r": json.dumps(new_rules, ensure_ascii=False), "now": now, "id": TASK_ID, "old": OLD_REGEX})
            if res.rowcount != 1:
                raise RuntimeError(f"задание {TASK_ID}: обновлено {res.rowcount} строк")
            for rid, score in flips.items():
                res = await db.execute(text(
                    "UPDATE task_results SET is_correct = true, score = :s,"
                    " checked_at = :now, checked_by = :by,"
                    " metrics = CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END"
                    "   || jsonb_build_object('comment', CAST(:c AS text))"
                    " WHERE id = :id AND is_correct = false AND score = 0 AND checked_by IS NULL"
                ), {"s": score, "now": now, "by": CHECKED_BY, "c": VERDICT_COMMENT, "id": rid})
                if res.rowcount != 1:
                    raise RuntimeError(f"работа {rid}: обновлено {res.rowcount} строк")

            ok = (await db.execute(text(
                "SELECT (SELECT solution_rules->'short_answer'->>'regex' FROM tasks WHERE id = :t) = :new"
                " AND (SELECT count(*) FROM task_results WHERE id = ANY(:ids) AND is_correct AND checked_by = :by) = :n"
            ), {"t": TASK_ID, "new": NEW_REGEX, "ids": list(TARGETS), "by": CHECKED_BY, "n": len(TARGETS)})).scalar_one()
            if not ok:
                raise RuntimeError("верификация не прошла")
            await db.commit()
            print("Записано и проверено.")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Точка входа."""
    ap = argparse.ArgumentParser(description="tsk-1158: регулярка задания 564 и два зачёта")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию dry-run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
