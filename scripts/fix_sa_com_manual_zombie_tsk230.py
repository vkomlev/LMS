"""tsk-230: вернуть в очередь ручной проверки авто-оценённые SA_COM с manual_review_required=true.

Дефект (root cause, LMS): до фикса checking_service авто-проверял ВСЕ SA_COM по слову и
проставлял is_correct (true/false), игнорируя флаг manual_review_required. Из-за этого работы,
помеченные под обязательную ручную проверку, получали авто-вердикт и НЕ попадали в очередь
преподавателя (`GET /task-results/by-pending-review` требует is_correct IS NULL).

Фикс кода (app/services/checking_service.py) чинит будущие отправки. Этот скрипт возвращает
УЖЕ зависшие результаты в очередь: сбрасывает is_correct=NULL, score=0 у SA_COM-результатов,
где задание помечено manual_review_required=true, а результат авто-оценён (checked_at IS NULL,
is_correct IS NOT NULL). Teacher-проверенные (checked_at IS NOT NULL) НЕ трогаются.

Критерий-based (не хардкод id) и идемпотентен: повторный запуск найдёт 0 строк.

Запуск (на проде): dry-run по умолчанию; для записи —
    DBCHECK_OK=1 python scripts/fix_sa_com_manual_zombie_tsk230.py --apply
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

SELECT_TARGETS = """
SELECT tr.id, tr.task_id, tr.user_id, tr.score, tr.max_score, tr.is_correct
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
WHERE tr.checked_at IS NULL
  AND tr.is_correct IS NOT NULL
  AND t.task_content->>'type' = 'SA_COM'
  AND COALESCE((t.solution_rules->>'manual_review_required')::boolean, false) = true
ORDER BY tr.id
"""


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(SELECT_TARGETS)
        print(f"Кандидатов на возврат в очередь: {len(rows)}")
        for r in rows:
            print(
                f"  result#{r['id']} task={r['task_id']} user={r['user_id']} "
                f"score={r['score']}/{r['max_score']} is_correct={r['is_correct']} -> is_correct=NULL, score=0"
            )
        if not rows:
            print("Нечего делать.")
            return
        if not apply:
            print("\nDRY-RUN: запись не выполнена. Для применения — --apply (нужен DBCHECK_OK=1).")
            return
        ids = [r["id"] for r in rows]
        async with conn.transaction():
            updated = await conn.execute(
                "UPDATE task_results SET is_correct=NULL, score=0 WHERE id = ANY($1::int[])",
                ids,
            )
            # Верификация внутри транзакции: целевые строки теперь pending (is_correct IS NULL).
            still = await conn.fetch(
                "SELECT id FROM task_results WHERE id = ANY($1::int[]) AND is_correct IS NOT NULL",
                ids,
            )
            if still:
                raise RuntimeError(f"Верификация не прошла: остались с вердиктом {[r['id'] for r in still]}")
            print(f"\nОБНОВЛЕНО: {updated}. Верификация пройдена — все {len(ids)} в очереди (is_correct=NULL).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
