"""tsk-1388: задание 55 — принимать «0,5» с десятичной запятой и зачесть такие сдачи.

Что и почему. Эталон задания 55 (cos 60° с округлением) — `0.5` / `0.5000`.
Нормализация задания `trim, lower, collapse_spaces` запятую не трогает, поэтому
верный по смыслу ответ «0,5» получал незачёт. По решению оператора 19.10.2026
добавляем варианты `0,5` и `0,5000` в `short_answer.accepted_answers`.

Пометка ручной правки: `content_provenance` = `manual_script`,
`edited_by: tsk-1388`, пара `solution_rules`+`task_content` — чтобы
переиздание из ContentBackbone не стёрло правку. Аудит: `app.audit_actor`.

Перезачёт: незачтённые сдачи задания 55 с ответом «0,5»/«0,5000» прогоняются
через `CheckingService.check_task` уже по новому правилу; пишется вердикт
движка (как tsk-1168): `score`, `is_correct`, `checked_at`, `checked_by`,
`metrics.comment`; затем пересчёт `student_course_state` по корням.

Безопасность (/db-check, режим записи): dry-run по умолчанию; состояние «до»
сверяется; одна транзакция, проверка числа строк и верификация до коммита.

Запуск (из корня LMS):
  python scripts/tsk1388_task55_decimal_comma.py
  DBCHECK_OK=1 python scripts/tsk1388_task55_decimal_comma.py --apply
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

logger = logging.getLogger("tsk1388")

TASK_ID = 55
EXPECTED_BEFORE = ["0.5", "0.5000"]
ADD = ["0,5", "0,5000"]
COMMA_ANSWERS = ["0,5", "0,5000"]
CHECKED_BY = 2
VERDICT_COMMENT = (
    "tsk-1388: ответ «0,5» верен — эталон 0.5 записан с точкой, запятая не "
    "принималась. Вариант с запятой добавлен в задание, сдача засчитана по решению оператора."
)


def _prod_async_dsn() -> str:
    """Боевой DSN из .mcp.json в форме для asyncpg. Пароль не печатается."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    dsn: str = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def _as_dict(raw: Any) -> dict[str, Any]:
    """jsonb может прийти строкой — привести к словарю."""
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


async def run(apply: bool) -> int:
    """Сверить, поправить правило, перепроверить сдачи движком; при apply — записать.

    :returns: 0 — успех, 1 — отказ по сверке.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.schemas.checking import StudentAnswer
    from app.schemas.task_content import TaskContent
    from app.services.checking_service import CheckingService
    from app.services.learning_engine_service import LearningEngineService

    engine = create_async_engine(_prod_async_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = CheckingService()
    learning = LearningEngineService()
    print(f"Режим: {'ЗАПИСЬ' if apply else 'DRY-RUN'}")
    try:
        async with factory() as db:
            task = (await db.execute(text(
                "SELECT id, task_content, solution_rules, max_score, course_id"
                " FROM tasks WHERE id = :id FOR UPDATE"
            ), {"id": TASK_ID})).mappings().one()
            rules_raw = _as_dict(task["solution_rules"])
            accepted = rules_raw["short_answer"]["accepted_answers"]
            values = [a["value"] for a in accepted]
            if values != EXPECTED_BEFORE:
                print(f"ОТКАЗ: эталоны задания {TASK_ID} не совпали с ожидаемыми: {values}")
                return 1
            new_rules = copy.deepcopy(rules_raw)
            new_rules["short_answer"]["accepted_answers"] = accepted + [
                {"score": 1, "value": v} for v in ADD
            ]
            print(f"задание {TASK_ID}: эталоны {values} → {values + ADD}")

            content = TaskContent.model_validate(_as_dict(task["task_content"]))
            rules = service.build_solution_rules(new_rules, fallback_max_score=task["max_score"] or 1)

            rows = (await db.execute(text(
                "SELECT id, user_id, score, max_score, is_correct, checked_by, answer_json"
                " FROM task_results WHERE task_id = :t AND is_correct IS NOT TRUE"
                " AND answer_json->'response'->>'value' = ANY(:vals) ORDER BY id"
            ), {"t": TASK_ID, "vals": COMMA_ANSWERS})).mappings().all()
            planned: dict[int, tuple[int, int]] = {}
            for r in rows:
                result = service.check_task(content, rules, StudentAnswer.model_validate(_as_dict(r["answer_json"])))
                value = _as_dict(r["answer_json"])["response"]["value"]
                if r["checked_by"] is not None or r["score"] != 0:
                    print(f"ПРОПУСК: сдача {r['id']} уже проверялась вручную ({r['checked_by']}, {r['score']})")
                    continue
                if result.is_correct is not True:
                    print(f"ПРОПУСК: сдача {r['id']} ({value!r}) — движок не зачёл: {result.score}/{result.max_score}")
                    continue
                planned[int(r["id"])] = (int(r["user_id"]), result.score)
                print(f"сдача {r['id']} (ученик {r['user_id']}, ответ {value!r}): незачёт → {result.score}/{result.max_score}")
            print(f"к перезачёту: {len(planned)} из {len(rows)}")

            if not apply:
                print("DRY-RUN: ничего не записано.")
                await db.rollback()
                return 0

            now = datetime.now(timezone.utc)
            await db.execute(text("SELECT set_config('app.audit_actor', 'tsk-1388', true)"))
            prov = {
                "source": "manual_script",
                "edited_at": now.isoformat(),
                "edited_by": "tsk-1388",
                "fields": ["solution_rules", "task_content"],
                "reason": "задание 55: принимать 0,5 с десятичной запятой (tsk-1388)",
            }
            res = await db.execute(text(
                "UPDATE tasks SET solution_rules = CAST(:r AS jsonb), content_provenance = CAST(:p AS jsonb)"
                " WHERE id = :id"
            ), {"r": json.dumps(new_rules, ensure_ascii=False), "p": json.dumps(prov, ensure_ascii=False), "id": TASK_ID})
            if res.rowcount != 1:
                raise RuntimeError(f"задание {TASK_ID}: обновлено {res.rowcount} строк")

            for rid, (_uid, score) in planned.items():
                res = await db.execute(text(
                    "UPDATE task_results SET is_correct = true, score = :s,"
                    " checked_at = :now, checked_by = :by,"
                    " metrics = CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END"
                    "   || jsonb_build_object('comment', CAST(:c AS text))"
                    " WHERE id = :id AND is_correct IS NOT TRUE AND score = 0 AND checked_by IS NULL"
                ), {"s": score, "now": now, "by": CHECKED_BY, "c": VERDICT_COMMENT, "id": rid})
                if res.rowcount != 1:
                    raise RuntimeError(f"сдача {rid}: обновлено {res.rowcount} строк вместо 1")

            for uid in sorted({u for u, _ in planned.values()}):
                roots = await learning.list_active_roots_of_node(db, uid, task["course_id"])
                for root_id in roots:
                    await learning.compute_course_state(db, uid, root_id, update_state_table=True)
                print(f"состояние курса пересчитано: ученик {uid}, корни {roots}")

            after = _as_dict((await db.execute(text(
                "SELECT solution_rules FROM tasks WHERE id = :id"), {"id": TASK_ID})).scalar_one())
            got = [a["value"] for a in after["short_answer"]["accepted_answers"]]
            if got != EXPECTED_BEFORE + ADD:
                raise RuntimeError(f"верификация эталонов: {got}")
            if planned:
                check = (await db.execute(text(
                    "SELECT count(*) FROM task_results WHERE id = ANY(:ids) AND is_correct AND checked_by = :by"
                ), {"ids": list(planned), "by": CHECKED_BY})).scalar_one()
                if check != len(planned):
                    raise RuntimeError(f"верификация сдач: {check} из {len(planned)}")
            await db.commit()
            print(f"Записано и проверено: эталоны {got}, зачтено {len(planned)}")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Точка входа."""
    ap = argparse.ArgumentParser(description="tsk-1388: задание 55 — десятичная запятая")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию dry-run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
