"""tsk-1391: включить шаг `decimal_comma` заданиям с дробным числовым эталоном.

Что и почему. Шаг `decimal_comma` (движок, tsk-1391) пишет одиночное число с
десятичной запятой через точку: «0,5» засчитывается за эталон «0.5». Включаем его
активным заданиям, у которых ВСЕ эталоны — одиночные числа и хотя бы один дробный.
Исключены даты 13036 (`05.10`) и 13074 (`03.09`) — решение за оператором.

Пометка ручной правки: `content_provenance` = `manual_script`, `edited_by: tsk-1391`,
пара `solution_rules`+`task_content`. Аудит: `app.audit_actor`.

Отчёт (только чтение): задания, где движок засчитывает эталон без разделителя
(«05» за «0.5») — по текущим и по новым правилам.

Перезачёт: незачтённые автоматические сдачи этих заданий с запятой в ответе
прогоняются через `CheckingService.check_task` по новому правилу; совпавшие
засчитываются вердиктом движка, `student_course_state` пересчитывается.

Безопасность (/db-check, режим записи): dry-run по умолчанию; одна транзакция,
проверка числа строк и верификация до коммита. Писать только ПОСЛЕ выката кода
с шагом: старый движок отвергнет неизвестный шаг при разборе правил.

Запуск (из корня LMS):
  python scripts/tsk1391_decimal_comma_step.py
  DBCHECK_OK=1 python scripts/tsk1391_decimal_comma_step.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

logger = logging.getLogger("tsk1391")

STEP = "decimal_comma"
EXCLUDED = {13036, 13074}
CHECKED_BY = 2
NUMBER_RE = re.compile(r"[-+]?\d+([.,]\d+)?")
FRACTION_RE = re.compile(r"[-+]?\d+[.,]\d+")
VERDICT_COMMENT = (
    "tsk-1391: ответ с десятичной запятой верен — эталон записан с точкой. "
    "Заданию включён шаг decimal_comma, сдача засчитана по решению оператора."
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


def _is_target(rules: dict[str, Any]) -> bool:
    """Все эталоны — одиночные числа, хотя бы один дробный."""
    short = rules.get("short_answer") or {}
    values = [str(a.get("value", "")).strip() for a in short.get("accepted_answers") or []]
    return bool(values) and all(NUMBER_RE.fullmatch(v) for v in values) and any(
        FRACTION_RE.fullmatch(v) for v in values
    )


async def run(apply: bool) -> int:
    """Отобрать задания, построить план, при apply — записать в одной транзакции.

    :returns: 0 — успех.
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
            rows = (await db.execute(text(
                "SELECT id, task_content, solution_rules, max_score, course_id FROM tasks"
                " WHERE is_active AND jsonb_typeof(solution_rules->'short_answer'->'accepted_answers') = 'array'"
                " ORDER BY id FOR UPDATE"
            ))).mappings().all()
            targets: dict[int, dict[str, Any]] = {}
            for t in rows:
                rules_raw = _as_dict(t["solution_rules"])
                if int(t["id"]) in EXCLUDED or not _is_target(rules_raw):
                    continue
                if STEP in rules_raw["short_answer"].get("normalization", []):
                    continue
                new_rules = copy.deepcopy(rules_raw)
                new_rules["short_answer"]["normalization"] = list(
                    rules_raw["short_answer"].get("normalization") or ["trim", "lower"]
                ) + [STEP]
                targets[int(t["id"])] = {"row": t, "old": rules_raw, "new": new_rules}
            print(f"заданий к включению шага: {len(targets)}: {sorted(targets)}")

            # Отчёт: засчитывает ли движок эталон без разделителя («05» за «0.5»).
            for tid, item in targets.items():
                content = TaskContent.model_validate(_as_dict(item["row"]["task_content"]))
                for label, raw in (("сейчас", item["old"]), ("после", item["new"])):
                    rules = service.build_solution_rules(raw, fallback_max_score=item["row"]["max_score"] or 1)
                    for acc in raw["short_answer"]["accepted_answers"]:
                        glued = re.sub(r"[.,]", "", str(acc["value"]).strip())
                        if glued == str(acc["value"]).strip():
                            continue
                        res = service.check_task(content, rules, StudentAnswer.model_validate(
                            {"type": content.type, "response": {"value": glued}}))
                        if res.is_correct:
                            print(f"СКЛЕЙКА {label}: задание {tid} засчитывает {glued!r} за {acc['value']!r}")

            planned: dict[int, tuple[int, int, int]] = {}
            for tid, item in targets.items():
                content = TaskContent.model_validate(_as_dict(item["row"]["task_content"]))
                rules = service.build_solution_rules(item["new"], fallback_max_score=item["row"]["max_score"] or 1)
                results = (await db.execute(text(
                    "SELECT id, user_id, score, checked_by, answer_json FROM task_results"
                    " WHERE task_id = :t AND is_correct IS NOT TRUE"
                    " AND answer_json->'response'->>'value' LIKE '%,%' ORDER BY id"
                ), {"t": tid})).mappings().all()
                for r in results:
                    answer = _as_dict(r["answer_json"])
                    value = (answer.get("response") or {}).get("value")
                    if r["checked_by"] is not None or (r["score"] or 0) != 0:
                        print(f"ПРОПУСК: сдача {r['id']} задания {tid} проверялась вручную")
                        continue
                    res = service.check_task(content, rules, StudentAnswer.model_validate(answer))
                    if res.is_correct is not True:
                        print(f"  не совпала: сдача {r['id']} задания {tid} ({value!r})")
                        continue
                    planned[int(r["id"])] = (int(r["user_id"]), res.score, tid)
                    print(f"сдача {r['id']} задания {tid} (ученик {r['user_id']}, {value!r}): → {res.score}")
            print(f"к перезачёту: {len(planned)}")

            if not apply:
                print("DRY-RUN: ничего не записано.")
                await db.rollback()
                return 0

            now = datetime.now(timezone.utc)
            await db.execute(text("SELECT set_config('app.audit_actor', 'tsk-1391', true)"))
            prov = json.dumps({
                "source": "manual_script",
                "edited_at": now.isoformat(),
                "edited_by": "tsk-1391",
                "fields": ["solution_rules", "task_content"],
                "reason": "шаг decimal_comma заданиям с дробным числовым эталоном (tsk-1391)",
            }, ensure_ascii=False)
            for tid, item in targets.items():
                res = await db.execute(text(
                    "UPDATE tasks SET solution_rules = CAST(:r AS jsonb), content_provenance = CAST(:p AS jsonb)"
                    " WHERE id = :id"
                ), {"r": json.dumps(item["new"], ensure_ascii=False), "p": prov, "id": tid})
                if res.rowcount != 1:
                    raise RuntimeError(f"задание {tid}: обновлено {res.rowcount} строк")

            for rid, (_uid, score, _tid) in planned.items():
                res = await db.execute(text(
                    "UPDATE task_results SET is_correct = true, score = :s,"
                    " checked_at = :now, checked_by = :by,"
                    " metrics = CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END"
                    "   || jsonb_build_object('comment', CAST(:c AS text))"
                    " WHERE id = :id AND is_correct IS NOT TRUE AND score = 0 AND checked_by IS NULL"
                ), {"s": score, "now": now, "by": CHECKED_BY, "c": VERDICT_COMMENT, "id": rid})
                if res.rowcount != 1:
                    raise RuntimeError(f"сдача {rid}: обновлено {res.rowcount} строк вместо 1")

            for uid, tid in sorted({(u, t) for u, _, t in planned.values()}):
                course_id = targets[tid]["row"]["course_id"]
                for root_id in await learning.list_active_roots_of_node(db, uid, course_id):
                    await learning.compute_course_state(db, uid, root_id, update_state_table=True)

            check = (await db.execute(text(
                "SELECT count(*) FROM tasks WHERE id = ANY(:ids)"
                " AND solution_rules->'short_answer'->'normalization' ? :step"
                " AND content_provenance->>'edited_by' = 'tsk-1391'"
            ), {"ids": list(targets), "step": STEP})).scalar_one()
            if check != len(targets):
                raise RuntimeError(f"верификация заданий: {check} из {len(targets)}")
            if planned:
                ok = (await db.execute(text(
                    "SELECT count(*) FROM task_results WHERE id = ANY(:ids) AND is_correct AND checked_by = :by"
                ), {"ids": list(planned), "by": CHECKED_BY})).scalar_one()
                if ok != len(planned):
                    raise RuntimeError(f"верификация сдач: {ok} из {len(planned)}")
            await db.commit()
            print(f"Записано и проверено: шаг у {check} заданий, зачтено {len(planned)}")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Точка входа."""
    ap = argparse.ArgumentParser(description="tsk-1391: шаг decimal_comma")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию dry-run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
