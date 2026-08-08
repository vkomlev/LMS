"""tsk-396 — перевод заданий ОГЭ-14 (курс 1179) на гибридный режим проверки.

Заменяет обход tsk-395. Было: `manual_review_required=true` без авто-сверки —
преподаватель сверял числа руками по эталону, мгновенной обратной связи у ученика
не было. Хуже того, связка «эталон есть + ручной гейт» давала оптимистичный зачёт
(живая проба: ответ «999 999» при эталоне «12 516,30» → state=PASSED), то есть
гейт, на который рассчитывал обход, фактически не держал.

Ставится ДВА флага (см. ADR-0007):
  - `partial_auto_check=true` — числа сверяются авто-чеком и итог сразу виден
    ученику, но балл не начисляется до оценки преподавателем;
  - `requires_attachment=true` — диаграмма сдаётся файлом .ods/.xlsx (решение
    оператора 2026-08-08). Механизм существует с tsk-227, вложение привязано к
    заданию с tsk-575. Стем каждого задания прямо требует «Постройте круговую
    диаграмму» и называет файл `task14.ods`, так что требование файла законно.

Отбор намеренно узкий: курс 1179, активные, ручной гейт, эталон непустой.
20 заданий «напиши программу целиком» (курсы 864-869, 1405-1415) под тот же
отбор НЕ попадают — там эталон это исходный код, авто-сверка ненадёжна, режим им
не подходит.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/enable_partial_auto_check_tsk396.py
    PROD_DB_DSN=... python scripts/enable_partial_auto_check_tsk396.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

COURSE_ID = 1179
EXPECTED_TASKS = 25  # 24 SA_COM + 1 TBL_COM (снято с прода 2026-08-08)

SELECT_TARGETS = """
    SELECT id,
           task_content->>'type' AS ttype,
           COALESCE((solution_rules->>'partial_auto_check')::boolean, false) AS partial_auto_check,
           COALESCE((solution_rules->>'requires_attachment')::boolean, false) AS requires_attachment,
           solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS reference
    FROM tasks
    WHERE course_id = $1
      AND is_active IS TRUE
      AND COALESCE((solution_rules->>'manual_review_required')::boolean, false) IS TRUE
      AND jsonb_array_length(
              COALESCE(solution_rules->'short_answer'->'accepted_answers', '[]'::jsonb)
          ) > 0
    ORDER BY id
"""


async def main(apply: bool) -> int:
    """Ставит гибридный режим заданиям ОГЭ-14 в одной транзакции с поштучной сверкой."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-396: гибридный режим, курс {COURSE_ID} — {mode} ===\n")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(SELECT_TARGETS, COURSE_ID)
        if not rows:
            print("нечего обновлять — под отбор не попало ни одного задания.")
            return 1

        print(f"под отбор попало заданий: {len(rows)} (ожидалось {EXPECTED_TASKS})")
        if len(rows) != EXPECTED_TASKS:
            # Расхождение с инвентарём — данные изменились после снятия. Не пишем
            # молча: непроверенный отбор в общем движке дороже повторного запуска.
            print(
                "ОШИБКА: число заданий разошлось с инвентарём. Пересними инвентарь "
                "(/db-check) и обнови EXPECTED_TASKS осознанно."
            )
            return 1

        by_type: dict[str, int] = {}
        already = 0
        for row in rows:
            by_type[row["ttype"]] = by_type.get(row["ttype"], 0) + 1
            if row["partial_auto_check"] and row["requires_attachment"]:
                already += 1
        print(f"по типам: {dict(sorted(by_type.items()))}")
        print(f"уже в целевом состоянии: {already}; будет изменено: {len(rows) - already}\n")

        print("выборка (первые 5):")
        for row in rows[:5]:
            print(
                f"  id={row['id']} {row['ttype']:<8} эталон={row['reference']!r} "
                f"partial={row['partial_auto_check']} attach={row['requires_attachment']}"
            )
        print()

        target_ids = [row["id"] for row in rows]

        tx = conn.transaction()
        await tx.start()
        try:
            # Прецедент прод-скриптов: триггер пересчёта порядка не должен
            # срабатывать на правке, которая порядка не касается.
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")

            status = await conn.execute(
                """
                UPDATE tasks
                SET solution_rules = solution_rules
                    || jsonb_build_object('partial_auto_check', true,
                                          'requires_attachment', true)
                WHERE id = ANY($1::int[])
                """,
                target_ids,
            )
            print(f"UPDATE: {status}")

            # Верификация ПОШТУЧНО, а не агрегатом (урок tsk-317): агрегат
            # «было/стало» пропускает строку, которая не обновилась молча.
            check = await conn.fetch(
                """
                SELECT id,
                       COALESCE((solution_rules->>'partial_auto_check')::boolean, false) AS pac,
                       COALESCE((solution_rules->>'requires_attachment')::boolean, false) AS ra,
                       COALESCE((solution_rules->>'manual_review_required')::boolean, false) AS mrr,
                       jsonb_array_length(
                           COALESCE(solution_rules->'short_answer'->'accepted_answers', '[]'::jsonb)
                       ) AS n_ref
                FROM tasks WHERE id = ANY($1::int[]) ORDER BY id
                """,
                target_ids,
            )
            bad = [
                dict(r) for r in check
                if not (r["pac"] and r["ra"] and r["mrr"] and r["n_ref"] > 0)
            ]
            if len(check) != len(target_ids) or bad:
                print(f"ОШИБКА верификации: проверено {len(check)} из {len(target_ids)}, "
                      f"не в целевом состоянии {len(bad)}: {bad[:5]}")
                await tx.rollback()
                return 1
            print(f"верификация: все {len(check)} заданий в целевом состоянии "
                  f"(partial_auto_check, requires_attachment, manual_review_required, эталон)")

            # Инвариант ADR-0007: режим не должен утечь за пределы отбора.
            leaked = await conn.fetchval(
                """
                SELECT COUNT(*) FROM tasks
                WHERE COALESCE((solution_rules->>'partial_auto_check')::boolean, false) IS TRUE
                  AND NOT (id = ANY($1::int[]))
                """,
                target_ids,
            )
            if leaked:
                print(f"ОШИБКА: гибридный режим стоит ещё у {leaked} заданий вне отбора.")
                await tx.rollback()
                return 1
            print("инвариант: вне отбора гибридного режима нет ни у одного задания")

            if apply:
                await tx.commit()
                print("\nCOMMIT выполнен.")
            else:
                await tx.rollback()
                print("\nROLLBACK (dry-run). Для записи запусти с --apply.")
        except Exception:
            await tx.rollback()
            raise
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить COMMIT (по умолчанию dry-run)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
