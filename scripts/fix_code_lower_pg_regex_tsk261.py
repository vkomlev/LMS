"""tsk-261: добить `lower` у код-заданий, пропущенных из-за МОЕЙ ошибки в регулярке.

ЧТО ПРОИЗОШЛО. Скрипт `fix_code_answer_lower_tsk261.py` снял `lower` со 153 код-заданий, но
его `CODE_RX` была написана в синтаксисе Python, а исполняется PostgreSQL:

    \\bprint\\b|\\bimport\\b|\\bdef\\b     ← в PG `\\b` это BACKSPACE, а не граница слова

В PostgreSQL граница слова — `\\y`. Значит эти три ветки не срабатывали НИКОГДА, и задания
ловились только по скобкам/точке/`=`. У голого `import random` ничего этого нет → пропущено.
Нашёл чип tsk-262 при независимой проверке (спасибо ему).

ЖИВОЙ ЭФФЕКТ (проверен настоящим движком до правки):
    задача 5468, эталон `import random`  →  ответ `IMPORT RANDOM` = ЗАЧЁТ
Ключевые слова Python строчные; `IMPORT RANDOM` — синтаксическая ошибка.

ЧИНИМ ТРИ однозначных (эталон = инструкция кода):
    5468 `import random`, 5617 `import math`, 5748 `import turtle`

НЕ ТРОГАЕМ два спорных: 8626 (`print`) и 6124 (`def`) — там вопрос «какой командой в Python
выводят данные на экран?», то есть НАЗВАТЬ ключевое слово. `Print` — та же мысль с заглавной,
и резать за это спорно. У чипа tsk-262 классификатор точнее (разбор + код-конструкция +
отсутствие кириллицы в именах) — решение по ним оставляю ему/оператору.

FOLLOW-UP (класс, не только этот случай): проверить остальные регулярки проекта, исполняемые
в PostgreSQL, на `\\b` — тихо не срабатывают.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

TARGETS = (5468, 5617, 5748)

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
        raise RuntimeError("DATABASE_URL не прод (5.42.107.253) — передай прод-DSN из .mcp.json")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for tid in TARGETS:
                row = await conn.fetchrow(
                    """SELECT solution_rules->'short_answer'->'normalization' n,
                              (SELECT a->>'value' FROM jsonb_array_elements(
                                 solution_rules->'short_answer'->'accepted_answers') a LIMIT 1) ref
                         FROM tasks WHERE id=$1""",
                    tid,
                )
                if row is None:
                    raise RuntimeError(f"задание {tid} не найдено")
                print(f"  {tid}: эталон={row['ref']!r} было {row['n']}")
                await conn.execute(UPDATE_ONE, tid)

            # Верификация в транзакции.
            left = await conn.fetchval(
                """SELECT count(*) FROM tasks
                    WHERE id = ANY($1::int[])
                      AND solution_rules->'short_answer'->'normalization' ? 'lower'""",
                list(TARGETS),
            )
            assert left == 0, f"у {left} заданий lower остался"
            sample = await conn.fetchval(
                "SELECT solution_rules->'short_answer'->'normalization' FROM tasks WHERE id=$1", TARGETS[0]
            )
            print(f"  после: {TARGETS[0]} normalization = {sample}")
            print(f"OK: lower убран у {len(TARGETS)} заданий; спорные 8626/6124 не тронуты")

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
