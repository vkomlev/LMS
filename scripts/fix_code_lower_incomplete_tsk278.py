"""tsk-278: снять `lower` у код-заданий с неполным сниппетом-эталоном.

КОНТЕКСТ. Аудит tsk-278 (класс «`\\b` в PG-регексе молча не срабатывает», follow-up
tsk-261/262) подтвердил: других PG-исполняемых регулярок с `\\b` в LMS/CB/TG_LMS нет.
Но та же мёртвая ветка `\\bimport\\b|\\bdef\\b|\\bprint\\b` из `fix_code_answer_lower_tsk261`
оставила остаточный ущерб на данных: часть активных код-заданий сохранила `lower` в
`solution_rules.short_answer.normalization`. tsk-262 добирал их AST-классификатором
(`ast.parse` + «нет кириллицы в именах»), но НЕПОЛНЫЕ сниппеты (`while True:`,
`for x in y:`, `return z`) как самостоятельная программа не парсятся → AST их отверг →
`lower` у них остался.

ЖИВОЙ УЩЕРБ (подтверждён данными прода, read-only, 2026-07-17). Это задания «напиши код
сам», где `lower` даёт ложный зачёт невалидного Python:
    5527  эталон `for jivotnoe in spisok:`   → `FOR JIVOTNOE IN SPISOK:` засчитан
    5613  эталон `return rezultat`            → `RETURN REZULTAT` засчитан
    5636  эталон `return rezultat`            → то же
    5768  эталон `while True:`                → `while true:` засчитан (а `true` не Python!)

ПОЧЕМУ ИМЕННО СНЯТЬ `lower`, А НЕ ДОБАВИТЬ `code_ast`. По движку оценки
(`app/services/checking_service.py::_matches_short_answer`) `code_ast` — лишь
ДОПОЛНИТЕЛЬНЫЙ путь к зачёту: если хотя бы одна сторона не разбирается как программа,
сравнение откатывается на текстовую нормализацию. Неполные сниппеты не парсятся, поэтому
`code_ast` для них инертен и ложный зачёт не чинит. Чинит именно снятие `lower` —
текстовое сравнение становится регистрозависимым: `while true:` ≠ `while True:`.
Остальные шаги (`trim`/`strip_punctuation`/`collapse_spaces`) сохраняются как были.

ГРАНИЦА. НЕ трогаем «назови команду маленькими буквами» (8626 `print`, 6124 `def`,
8653 `return`) и спорные keyword-fill (5463/5567 `elif`, 6222 `for`, 6224 `input`) —
там tsk-262/оператор провели линию сознательно (решение оператора по tsk-278).

Запуск: dry-run по умолчанию; для записи `DBCHECK_OK=1 python scripts/fix_code_lower_incomplete_tsk278.py --apply`.
DSN — прод из .mcp.json (learn_prod_db), передаётся через переменную окружения LEARN_PROD_DSN
или флаг --dsn; на localhost не пишем.
"""
import argparse
import asyncio
import os
import sys

import asyncpg

TARGETS: tuple[int, ...] = (5527, 5613, 5636, 5768)

# Снять только шаг 'lower', сохранив порядок и остальные шаги.
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
  AND solution_rules->'short_answer'->'normalization' ? 'lower'
"""

SELECT_ONE = """
SELECT id,
       (SELECT a->>'value'
          FROM jsonb_array_elements(solution_rules->'short_answer'->'accepted_answers') a
         LIMIT 1) AS ref,
       solution_rules->'short_answer'->'normalization' AS norm
  FROM tasks WHERE id = $1
"""


def _dsn(cli_dsn: str | None) -> str:
    """Прод-DSN из --dsn или env LEARN_PROD_DSN; проверка, что это прод-хост."""
    dsn = cli_dsn or os.environ.get("LEARN_PROD_DSN", "")
    if not dsn:
        raise RuntimeError(
            "Не передан прод-DSN. Укажи --dsn или LEARN_PROD_DSN "
            "(строка learn_prod_db из .mcp.json)."
        )
    if "5.42.107.253" not in dsn:
        raise RuntimeError("DSN не прод (5.42.107.253) — правка данных только на проде.")
    return dsn


async def main(apply: bool, cli_dsn: str | None) -> None:
    conn = await asyncpg.connect(_dsn(cli_dsn))
    try:
        async with conn.transaction():
            for tid in TARGETS:
                before = await conn.fetchrow(SELECT_ONE, tid)
                if before is None:
                    raise RuntimeError(f"задание {tid} не найдено")
                await conn.execute(UPDATE_ONE, tid)
                after = await conn.fetchrow(SELECT_ONE, tid)
                print(
                    f"  {tid}: эталон={before['ref']!r}\n"
                    f"        было {before['norm']}\n"
                    f"        стало {after['norm']}"
                )

            # Верификация в транзакции: ни у одного не осталось lower, code_ast не добавлен.
            left = await conn.fetchval(
                """SELECT count(*) FROM tasks
                    WHERE id = ANY($1::int[])
                      AND solution_rules->'short_answer'->'normalization' ? 'lower'""",
                list(TARGETS),
            )
            assert left == 0, f"у {left} заданий lower остался"
            ast_added = await conn.fetchval(
                """SELECT count(*) FROM tasks
                    WHERE id = ANY($1::int[])
                      AND solution_rules->'short_answer'->'normalization' ? 'code_ast'""",
                list(TARGETS),
            )
            assert ast_added == 0, f"code_ast ошибочно появился у {ast_added} заданий"
            print(f"OK: lower снят у {len(TARGETS)} заданий; code_ast не добавлялся (инертен для неполных сниппетов)")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dsn", default=None)
    ns = parser.parse_args()
    try:
        asyncio.run(main(ns.apply, ns.dsn))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
