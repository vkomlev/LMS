"""tsk-1168: засчитать сдачу 37625 (ученик 4501, задание 2103), срезанную гейтом.

Что и почему. 30.09 ученик сдал в попытку 17562 ответ «95» (эталон «95»). Кабинет
подставил в форму файл из вчерашней попытки 17455 (черновик хранился по заданию,
а не по попытке). Гейт tsk-419 засчитывает только файл пары «попытка+задание»
(tsk-575), чужой не увидел и поставил 0 — хотя ответ верный и файл решения был.
Дефект кабинета исправлен в tsk-1168; эту сдачу засчитываем по решению оператора.

Вердикт не выдумывается: ответ прогоняется через `CheckingService.check_task`
(тот же код, что на приёме), в базу пишется то, что вернул движок. Поля — как у
штатной ручной дооценки и у образцов tsk-602/tsk-1148: `score`, `is_correct`,
`checked_at`, `checked_by`, плюс пояснение в `metrics.comment`. Незачёт —
последняя сдача задания, поэтому в той же транзакции пересчитывается
`student_course_state` по корням дерева.

Безопасность (/db-check, режим записи): dry-run по умолчанию; состояние «до»
сверяется дословно; одна транзакция, проверка числа строк и верификация до коммита.

Запуск (из корня LMS):
  python scripts/tsk1168_regrade_37625.py
  DBCHECK_OK=1 python scripts/tsk1168_regrade_37625.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Настройки приложения требуют DATABASE_URL при импорте сервисов; сам скрипт идёт
# своим движком на прод-DSN из .mcp.json, а .env (dev) нужен только для Settings().
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

logger = logging.getLogger("tsk1168")

#: Работы и ожидаемое состояние «до» — сверяется дословно.
TARGETS: dict[int, dict[str, Any]] = {
    37625: {"task_id": 2103, "user_id": 4501, "answer": "95"},
}
CHECKED_BY = 2
VERDICT_COMMENT = (
    "tsk-1168: ответ верный, файл решения был, но кабинет приложил его из прошлой "
    "попытки и гейт «комментарий или файл» его не увидел. Засчитано по решению оператора."
)


def _prod_async_dsn() -> str:
    """Боевой DSN из .mcp.json в форме для asyncpg. Пароль не печатается."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    dsn: str = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


async def run(apply: bool) -> int:
    """Сверить, пересчитать движком и (при apply) записать.

    :returns: код выхода: 0 — успех, 1 — отказ по сверке.
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
                "SELECT tr.id, tr.task_id, tr.user_id, tr.score, tr.max_score, tr.is_correct,"
                " tr.checked_by, tr.checked_at, tr.answer_json, t.task_content, t.solution_rules,"
                " t.max_score AS task_max_score, t.course_id,"
                " NOT EXISTS (SELECT 1 FROM task_results r WHERE r.task_id = tr.task_id"
                "   AND r.user_id = tr.user_id AND r.submitted_at > tr.submitted_at) AS is_last"
                " FROM task_results tr JOIN tasks t ON t.id = tr.task_id WHERE tr.id = ANY(:ids)"
            ), {"ids": list(TARGETS)})).mappings().all()
            by_id = {int(r["id"]): r for r in rows}
            planned: dict[int, tuple[bool, int]] = {}
            for rid, exp in TARGETS.items():
                r = by_id.get(rid)
                value = ((r["answer_json"] or {}).get("response") or {}).get("value") if r else None
                ok = (
                    r is not None and r["task_id"] == exp["task_id"] and r["user_id"] == exp["user_id"]
                    and value == exp["answer"]
                    and r["is_correct"] is False and r["score"] == 0 and r["checked_by"] is None
                    and r["is_last"]
                )
                if not ok:
                    print(f"ОТКАЗ: работа {rid} — состояние «до» не совпало: {dict(r) if r else None}")
                    return 1
                content = TaskContent.model_validate(r["task_content"])
                rules = service.build_solution_rules(r["solution_rules"], fallback_max_score=r["task_max_score"] or 1)
                result = service.check_task(content, rules, StudentAnswer.model_validate(r["answer_json"]))
                if result.is_correct is not True or result.max_score != r["max_score"]:
                    print(f"ОТКАЗ: работа {rid} — движок не подтверждает зачёт ({result.is_correct}, {result.score}/{result.max_score})")
                    return 1
                planned[rid] = (True, result.score)
                print(f"работа {rid} (задание {r['task_id']}, ответ {value!r}): незачёт → зачёт {result.score}/{result.max_score}")

            if not apply:
                print("DRY-RUN: ничего не записано.")
                await db.rollback()
                return 0

            now = datetime.now(timezone.utc)
            for rid, (verdict, score) in planned.items():
                res = await db.execute(text(
                    "UPDATE task_results SET is_correct = :v, score = :s,"
                    " checked_at = :now, checked_by = :by,"
                    " metrics = CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END"
                    "   || jsonb_build_object('comment', CAST(:c AS text))"
                    " WHERE id = :id AND is_correct = false AND score = 0 AND checked_by IS NULL"
                ), {"v": verdict, "s": score, "now": now, "by": CHECKED_BY, "c": VERDICT_COMMENT, "id": rid})
                if res.rowcount != 1:
                    raise RuntimeError(f"работа {rid}: обновлено {res.rowcount} строк вместо 1")

            for rid, exp in TARGETS.items():
                course_id = by_id[rid]["course_id"]
                roots = await learning.list_active_roots_of_node(db, exp["user_id"], course_id)
                for root_id in roots:
                    await learning.compute_course_state(db, exp["user_id"], root_id, update_state_table=True)
                print(f"состояние курса пересчитано: ученик {exp['user_id']}, узел {course_id}, корни {roots}")

            check = (await db.execute(text(
                "SELECT count(*) FROM task_results WHERE id = ANY(:ids) AND is_correct AND checked_by = :by"
            ), {"ids": list(TARGETS), "by": CHECKED_BY})).scalar_one()
            if check != len(TARGETS):
                raise RuntimeError(f"верификация: {check} из {len(TARGETS)}")
            await db.commit()
            print(f"Записано и проверено: {check} из {len(TARGETS)}")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Точка входа."""
    ap = argparse.ArgumentParser(description="tsk-1148: пересчёт трёх устаревших последних незачётов")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию dry-run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
