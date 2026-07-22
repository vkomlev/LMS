"""tsk-358 — снять manual_review_required=true у SA_COM-заданий с известным ответом.

Root cause (ContentBackbone `monolith/external_tasks/adapter/builder.py:148`):
`"manual_review_required": methodist_answer is None` — флаг ставится безусловно
true для всего sdamgia/kompege/polyakov-импорта (пайплайн никогда не передаёт
methodist_answer), независимо от того, что автоматический парсер реально
извлёк надёжный ответ в accepted_answers. Из-за этого верно проверенные
автопроверкой ответы годами ждут ручной проверки преподавателя (живой
инцидент: ученица Емельяненко Софья, задание id=2058).

Масштаб на проде (read-only аудит, 2026-07-22): 186 активных SA_COM-заданий
с этим противоречием (accepted_answers непустой + manual_review_required=true).
Из них 16 — брак экстракции (пустой ответ, обрывок прозы, формула вместо
результата, только ":" или неоднозначное объединение вариантов через "&"/"и") —
оставлены под ручной проверкой, НЕ трогать. Явное решение оператора
(2026-07-22): применить снятие флага к оставшимся 170.

Запуск (на прод-сервере, .env с прод DSN):
    python scripts/fix_manual_review_required_tsk358.py            # dry-run
    python scripts/fix_manual_review_required_tsk358.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

# Брак экстракции — НЕ трогать (оставить manual_review_required=true).
EXCLUDED_IDS = [
    54,    # пустой ответ ("")
    2242,  # обрывок прозы вместо ответа
    2300,  # вставлен код на Python + текст решения
    2303,  # формула "720 + 576 = 1296" вместо результата
    2304,  # формула "21 · (...) = 154248381" вместо результата
    2306,  # "1253&2494" — неоднозначное объединение вариантов
    2310,  # "1150&2652" — неоднозначное объединение вариантов
    2311,  # "667 и 4009" — неоднозначное объединение вариантов
    2313,  # "416&1390" — неоднозначное объединение вариантов
    2338,  # формула "— 12 + 9 = 21" вместо результата
    2355,  # обрывок прозы вместо ответа
    2379,  # только ":" — сломанная экстракция
    2380,  # только ":" — сломанная экстракция
    2381,  # только ":" — сломанная экстракция
    2382,  # только ":" — сломанная экстракция
    2386,  # числа + текст решения вместо чистого ответа
]

EXPECTED_TOTAL = 186
EXPECTED_UPDATED = 170

CANDIDATE_PREDICATE = """
    is_active = true
    AND task_content->>'type' = 'SA_COM'
    AND COALESCE((solution_rules->>'manual_review_required')::boolean, false) IS TRUE
    AND jsonb_array_length(COALESCE(solution_rules->'short_answer'->'accepted_answers', '[]'::jsonb)) > 0
"""


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-358: снять manual_review_required — {mode} ===\n")

    async with async_session_factory() as db:
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )

        total = (await db.execute(
            text(f"SELECT count(*) FROM tasks WHERE {CANDIDATE_PREDICATE}")
        )).scalar_one()
        print(f"Кандидатов всего (до фильтра исключений): {total} (ожидалось {EXPECTED_TOTAL})")
        if total != EXPECTED_TOTAL:
            print("ОШИБКА: множество кандидатов изменилось с момента аудита — ROLLBACK")
            await db.rollback()
            return 1

        result = await db.execute(
            text(
                f"UPDATE tasks SET solution_rules = jsonb_set("
                f"  solution_rules, '{{manual_review_required}}', 'false'::jsonb"
                f") WHERE {CANDIDATE_PREDICATE} AND id != ALL(:excluded)"
            ),
            {"excluded": EXCLUDED_IDS},
        )
        updated = result.rowcount
        print(f"UPDATE rowcount = {updated} (ожидалось {EXPECTED_UPDATED})")
        if updated != EXPECTED_UPDATED:
            print("ОШИБКА: количество обновлённых строк не совпало с ожиданием — ROLLBACK")
            await db.rollback()
            return 1

        # Верификация 1: исключённые 16 не тронуты (флаг всё ещё true).
        untouched = (await db.execute(
            text(
                "SELECT count(*) FROM tasks WHERE id = ANY(:excluded) "
                "AND COALESCE((solution_rules->>'manual_review_required')::boolean, false) IS TRUE"
            ),
            {"excluded": EXCLUDED_IDS},
        )).scalar_one()
        print(f"Исключённые всё ещё manual_review_required=true: {untouched} (ожидалось {len(EXCLUDED_IDS)})")
        if untouched != len(EXCLUDED_IDS):
            print("ОШИБКА: часть исключённых заданий была затронута — ROLLBACK")
            await db.rollback()
            return 1

        # Верификация 2: у обновлённых кандидатов флаг снят.
        remaining = (await db.execute(
            text(f"SELECT count(*) FROM tasks WHERE {CANDIDATE_PREDICATE}")
        )).scalar_one()
        print(f"Кандидатов с противоречием после апдейта: {remaining} (ожидалось {len(EXCLUDED_IDS)})")
        if remaining != len(EXCLUDED_IDS):
            print("ОШИБКА: после апдейта осталось не то количество кандидатов — ROLLBACK")
            await db.rollback()
            return 1

        # Верификация 3: живой кейс — задание 2058 (реальный инцидент).
        row = (await db.execute(
            text("SELECT solution_rules->>'manual_review_required' AS mrr FROM tasks WHERE id = 2058")
        )).mappings().one()
        print(f"task id=2058 manual_review_required = {row['mrr']} (ожидалось false)")
        if row["mrr"] != "false":
            print("ОШИБКА: контрольное задание 2058 не обновилось — ROLLBACK")
            await db.rollback()
            return 1

        print(f"\nВерификация пройдена: {updated} обновлено, {len(EXCLUDED_IDS)} корректно оставлены под ручной проверкой.")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения сохранены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
