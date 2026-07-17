"""tsk-261 (класс «нормализация ответа-кода»): убрать `lower` у заданий, где ответ — код.

ДЕФЕКТ (приёмка QA 2026-07-16, находки B2 и B6, подтверждены на проде):
`normalization: [trim, lower, strip_punctuation, collapse_spaces]` применяется к заданиям,
где ответ — программный код. `lower` делает регистр незначимым, а в Python он значим:
`print(I)` и `print(i)` — разные переменные, но нормализация даёт из обоих `printi` ⇒ зачёт.
То же с текстом вывода: `print("ты нашёл")` проходит за `print("Ты нашёл")`.

ПОЧЕМУ ТОЛЬКО `lower`, а не «строгое сравнение» (замерено на 62 реальных сдачах):
- убрать И `strip_punctuation` → сломались бы 15 из 36 зачётов, среди них ВАЛИДНЫЕ:
  `print(slovo.find('и'))` за эталон с двойными кавычками (в Python это одно и то же),
  `print(f"Мне {vozrast } лет!")` (пробел в фигурных скобках допустим);
- убрать только `lower` → перестают проходить РОВНО 4 сдачи, и все 4 — те самые
  ложные зачёты, которыми QA доказывала баг (`print(I)` x2, `print("...ты нашёл...")` x2).
  Новых зачётов не появляется (0), валидные ответы не ломаются.
Итого: минимальная правка, бьющая точно в дефект. Кавычки остаются эквивалентными,
потому что `strip_punctuation` сохраняется.

ОТБОР (почему не все 183): признак кода — эталон содержит вызов/атрибут/присваивание/скобки
(`CODE_RX`). Текстовые ответы (`плюс`, `информация`, `0`, `3.14`) НЕ трогаем: для них `lower`
правилен («Москва» = «москва»). Выборка проверена вручную — ложных захватов нет.

ВЛИЯНИЕ НА УЧЕНИКОВ: нет. Все 4 ложных зачёта принадлежат user=3 (Серебрякова, QA) — её
пробные ответы. Ретро-пересчёт не требуется, правка действует вперёд.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

# Признак «ответ — код»: вызов, атрибут, присваивание, скобки, ключевые слова.
CODE_RX = r"\(\)|\(.*\)|\.[a-zA-Z_]|=|\bprint\b|\bimport\b|\bdef\b|\[.*\]"

SELECT_TARGETS = """
SELECT t.id
FROM tasks t
WHERE t.is_active
  AND t.solution_rules->'short_answer'->'normalization' ? 'lower'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(t.solution_rules->'short_answer'->'accepted_answers') a
    WHERE a->>'value' ~ $1
  )
ORDER BY t.id
"""

# Убираем ТОЛЬКО 'lower' из массива normalization, остальное не трогаем.
UPDATE_ONE = """
UPDATE tasks
SET solution_rules = jsonb_set(
    solution_rules,
    '{short_answer,normalization}',
    COALESCE(
      (SELECT jsonb_agg(x)
         FROM jsonb_array_elements(solution_rules->'short_answer'->'normalization') x
        WHERE x <> '"lower"'::jsonb),
      '[]'::jsonb
    )
)
WHERE id = $1
"""


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        raise RuntimeError(
            "DATABASE_URL не указывает на прод (5.42.107.253). В .env лежит localhost — "
            "передай прод-DSN из .mcp.json явно."
        )
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = [r["id"] for r in await conn.fetch(SELECT_TARGETS, CODE_RX)]
            print(f"Заданий-кандидатов (эталон-код + lower): {len(ids)}")
            if not ids:
                raise RuntimeError("кандидатов нет — возможно, уже применено")

            # Контроль до правки: текстовые задания с lower не должны попасть в набор.
            text_with_lower = await conn.fetchval(
                """SELECT count(*) FROM tasks t WHERE t.is_active
                     AND t.solution_rules->'short_answer'->'normalization' ? 'lower'
                     AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(
                           t.solution_rules->'short_answer'->'accepted_answers') a
                         WHERE a->>'value' ~ $1)""",
                CODE_RX,
            )
            print(f"Текстовых заданий с lower (НЕ трогаем): {text_with_lower}")

            for tid in ids:
                await conn.execute(UPDATE_ONE, tid)

            # Верификация внутри транзакции.
            left = await conn.fetchval(
                """SELECT count(*) FROM tasks t WHERE t.is_active
                     AND t.solution_rules->'short_answer'->'normalization' ? 'lower'
                     AND EXISTS (SELECT 1 FROM jsonb_array_elements(
                           t.solution_rules->'short_answer'->'accepted_answers') a
                         WHERE a->>'value' ~ $1)""",
                CODE_RX,
            )
            still_text = await conn.fetchval(
                """SELECT count(*) FROM tasks t WHERE t.is_active
                     AND t.solution_rules->'short_answer'->'normalization' ? 'lower'
                     AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(
                           t.solution_rules->'short_answer'->'accepted_answers') a
                         WHERE a->>'value' ~ $1)""",
                CODE_RX,
            )
            assert left == 0, f"после правки остались код-задания с lower: {left}"
            assert still_text == text_with_lower, (
                f"пострадали текстовые задания: было {text_with_lower}, стало {still_text}"
            )

            # Контроль целостности: остальные шаги нормализации на месте.
            sample = await conn.fetchrow(
                "SELECT id, solution_rules->'short_answer'->'normalization' n FROM tasks WHERE id = $1",
                ids[0],
            )
            print(f"Пример {sample['id']}: normalization = {sample['n']}")

            print(f"OK: у {len(ids)} код-заданий убран lower; текстовые ({text_with_lower}) не тронуты")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
