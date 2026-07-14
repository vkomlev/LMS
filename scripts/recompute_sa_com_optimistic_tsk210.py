"""Ретроактивный пересчёт SA_COM, ложно зачтённых optimistic-PASSED (tsk-210).

Контекст
--------
До фикса tsk-210 submit-поток (`app/api/v1/attempts.py`, блок 2.3c) ставил
ЛЮБОМУ ответу SA_COM `is_correct=TRUE, score=max_score` («optimistic-PASSED»),
затирая результат сверки с эталоном. Из-за этого неверные ответы (напр. «0» на
задачу, где эталон другой) отображались ученику как «Верно» и получали полный
балл, хотя учитель ещё не проверял (`checked_at IS NULL`).

Этот скрипт находит такие записи и ПЕРЕСЧИТЫВАЕТ их честно: заново прогоняет
сохранённый ответ (`answer_json`) через `CheckingService` с реальными
`task_content`/`solution_rules` задачи и, при `--apply`, записывает настоящие
`score`/`is_correct`.

Что берём в кандидаты
---------------------
- `task_content->>'type' = 'SA_COM'`
- `checked_at IS NULL`  — учитель НЕ выставлял оценку (значит текущий TRUE
  пришёл только от optimistic-PASSED, а не от ручной проверки).
- `is_correct IS TRUE` — сейчас помечено «верно».

Что НЕ трогаем
--------------
- TA — у него нет эталона, optimistic-PASSED легитимен.
- SA_COM без правил проверки (`short_answer` не задан) → повторная сверка
  вернёт `is_correct=None` (авто-проверять нечем): такие пропускаем, вердикт
  по ним ставит только учитель. Оптимистичный TRUE для них корректен.
- Записи с `checked_at IS NOT NULL` — их уже оценил учитель, его вердикт
  авторитетен, не переписываем.
- Записи, где повторная сверка ПОДТВЕРЖДАЕТ верность (ответ реально совпал с
  эталоном) — TRUE остаётся, ничего не пишем.

Флипаем в FAILED только те, где повторная сверка дала `is_correct=False`
(реально неверный ответ, ошибочно зачтённый).

Безопасность
------------
- По умолчанию РЕЖИМ ЧТЕНИЯ (`--dry-run`): только считает и печатает отчёт,
  ничего не пишет. Служит одновременно оценкой масштаба (шаг 3 плана tsk-210).
- `--apply` включает запись. В `metrics` каждой изменённой записи проставляется
  маркер `tsk210_recompute` со старыми значениями — для аудита и возможного
  отката.
- ВАЖНО: перед прогоном на ПРОДЕ снять бэкап task_results. Подключение — через
  DATABASE_URL из .env того окружения, где запускаете.

Запуск (из корня проекта):
  python scripts/recompute_sa_com_optimistic_tsk210.py            # dry-run
  python scripts/recompute_sa_com_optimistic_tsk210.py --limit 20 # dry-run, показать 20 примеров
  python scripts/recompute_sa_com_optimistic_tsk210.py --apply    # запись
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")


CANDIDATE_SQL = """
    SELECT tr.id, tr.user_id, tr.task_id, tr.score, tr.max_score,
           tr.answer_json, t.task_content, t.solution_rules
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.checked_at IS NULL
      AND tr.is_correct IS TRUE
      AND t.task_content->>'type' = 'SA_COM'
    ORDER BY tr.id
"""


def _recheck(task_content_raw: Any, solution_rules_raw: Any, answer_raw: Any):
    """Повторно прогнать сохранённый ответ через CheckingService.

    Возвращает CheckResult либо None, если запись невозможно перепроверить
    (битый answer_json / несовпадение типа / нет правил → манускрипт-only).
    """
    from app.schemas.task_content import TaskContent
    from app.schemas.solution_rules import SolutionRules
    from app.schemas.checking import StudentAnswer
    from app.services.checking_service import CheckingService
    from app.utils.exceptions import DomainError

    try:
        task_content = TaskContent.model_validate(task_content_raw)
        solution_rules = SolutionRules.model_validate(solution_rules_raw)
        answer = StudentAnswer.model_validate(answer_raw)
    except Exception:
        return None

    try:
        return CheckingService().check_task(task_content, solution_rules, answer)
    except DomainError:
        return None
    except Exception:
        return None


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ретро-пересчёт SA_COM optimistic-PASSED (tsk-210)")
    parser.add_argument("--apply", action="store_true", help="Записать изменения (по умолчанию dry-run)")
    parser.add_argument("--limit", type=int, default=10, help="Сколько примеров-флипов показать")
    args = parser.parse_args()

    from sqlalchemy import text
    from app.db.session import async_session_factory

    total = 0
    manual_no_rules = 0   # SA_COM без правил → пропущены (is_correct=None при пересверке)
    unverifiable = 0      # битые/непересчитываемые записи
    confirmed_true = 0    # реально верные — оставляем
    to_flip: list[dict[str, Any]] = []

    async with async_session_factory() as session:
        res = await session.execute(text(CANDIDATE_SQL))
        rows = res.mappings().all()
        total = len(rows)

        for row in rows:
            cr = _recheck(row["task_content"], row["solution_rules"], row["answer_json"])
            if cr is None:
                unverifiable += 1
                continue
            if cr.is_correct is None:
                manual_no_rules += 1
                continue
            if cr.is_correct is True:
                confirmed_true += 1
                continue
            # cr.is_correct is False → ошибочно зачтено, флипаем
            to_flip.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "task_id": row["task_id"],
                "old_score": row["score"],
                "new_score": cr.score,
                "max_score": row["max_score"],
            })

        print("=== tsk-210 SA_COM optimistic-PASSED recompute ===")
        print(f"Режим:                 {'APPLY (запись)' if args.apply else 'DRY-RUN (только чтение)'}")
        print(f"Кандидатов всего:      {total}")
        print(f"  подтверждено верно:  {confirmed_true}  (TRUE остаётся)")
        print(f"  без правил (manual): {manual_no_rules}  (пропущено, вердикт за учителем)")
        print(f"  не пересчитать:      {unverifiable}  (битый answer_json/тип)")
        print(f"  К ФЛИПУ в FAILED:    {len(to_flip)}")

        if to_flip:
            print(f"\nПримеры (до {args.limit}):")
            for item in to_flip[: args.limit]:
                print(
                    f"  result_id={item['id']} user={item['user_id']} task={item['task_id']} "
                    f"score {item['old_score']}→{item['new_score']}/{item['max_score']}"
                )

        if args.apply and to_flip:
            for item in to_flip:
                await session.execute(
                    text(
                        """
                        UPDATE task_results
                        SET score = :new_score,
                            is_correct = FALSE,
                            metrics = COALESCE(metrics, '{}'::jsonb) || jsonb_build_object(
                                'tsk210_recompute',
                                jsonb_build_object(
                                    'old_is_correct', true,
                                    'old_score', :old_score,
                                    'new_score', :new_score
                                )
                            )
                        WHERE id = :id
                        """
                    ),
                    {"id": item["id"], "old_score": item["old_score"], "new_score": item["new_score"]},
                )
            await session.commit()
            print(f"\nЗаписано: {len(to_flip)} записей помечены FAILED (маркер metrics.tsk210_recompute).")
        elif args.apply:
            print("\nНечего писать — флипов нет.")
        else:
            print("\nDRY-RUN: изменения НЕ записаны. Для записи добавь --apply.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
