"""tsk-262: проставить заданиям явный шаг нормализации `code_ast` (ответ — программа).

ЧТО ДЕЛАЕТ
Добавляет 'code_ast' в solution_rules.short_answer.normalization тем заданиям, где
ответ — код на Python. После этого движок сравнивает ответ и эталон как ПРОГРАММЫ
(канон через AST), а не как текст: `print(slovo .lower())` засчитывается за
`print(slovo.lower())`, но `print(I)` за `print(i)` — нет.

ПОЧЕМУ КЛАССИФИКАТОР ИМЕННО ТАКОЙ (это главная ловушка задачи)
Автоматически «угадать код» надёжно нельзя, и оба очевидных способа врут:
- регулярка tsk-261 (CODE_RX) поймала 153 задания, среди которых SQL, `.env`,
  математика `21 · (14 6 -5 6)` и проза;
- «просто ast.parse» ловит ещё хуже: `тест-кейс` разбирается как ВЫЧИТАНИЕ имён,
  `example.ru` — как обращение к атрибуту, `функция (метод)` — как вызов.
Работает третий признак: настоящий Python не содержит кириллицу в ИМЕНАХ —
только внутри строковых литералов. Отбор = эталон разбирается в AST + содержит
код-конструкцию (вызов/присваивание/импорт/f-строку) + ни одного кириллического
идентификатора. Выборка (52 задания) просмотрена глазами целиком: только задания
Python-курсов, ложных захватов нет.

Классификатор здесь — РАЗОВЫЙ инструмент первичной простановки, а не способ
проверки. Дальше флаг живёт в данных как явное свойство задания; новые задания
получают его от контентного конвейера, а не от угадывания на лету.

ДОШЛИФОВКА tsk-261 (найдено этим же скриптом, его собственной проверкой)
Скрипт снимает у отобранных заданий `lower` — у 3 из 52 он ещё стоял, хотя tsk-261
должен был убрать его у всех код-заданий. Причина: CODE_RX написана в синтаксисе
Python, но исполняется PostgreSQL, а там `\\b` — это НЕ граница слова (граница — `\\y`;
`\\b` означает символ backspace). Поэтому ветки `\\bprint\\b|\\bimport\\b|\\bdef\\b` молча
не срабатывали, и задания ловились только по скобкам/точке/`=`/квадратным скобкам.
Задания с голым `import random` / `import math` / `import turtle` не имеют ни одного
из этих признаков — они прошли мимо фикса и до сих пор принимают `IMPORT RANDOM`
за `import random`. Проверено на проде:
  select 'import random' ~ '\\bimport\\b';  -- false
  select 'import random' ~ '\\yimport\\y';  -- true
Здесь отбор не зависит от регулярок PG вообще — классификация идёт разбором в AST
на стороне Python.

ВЛИЯНИЕ (замерено на прод-сдачах, scripts/measure_ast_normalization_tsk262.py)
Меняется ровно 1 вердикт из 60: result_id=1932 (task 5448) — ложный незачёт
`print(slovo .lower())` становится зачётом. Новых ложных зачётов 0: AST — это
дополнительный путь к зачёту, при неразбираемости любой стороны сравнение
падает обратно на текст, поэтому проверка не может стать строже.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1 и go оператора).
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
from typing import List

import asyncpg

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
# Конструкции, отличающие код от голого имени/числа.
CODE_NODES = (
    ast.Call,
    ast.Assign,
    ast.AugAssign,
    ast.JoinedStr,
    ast.Import,
    ast.ImportFrom,
    ast.Subscript,
)

SELECT_ALL = """
SELECT t.id,
       t.external_uid,
       t.solution_rules->'short_answer'->'normalization'    AS normalization,
       t.solution_rules->'short_answer'->'accepted_answers' AS accepted
FROM tasks t
WHERE t.is_active
  AND t.solution_rules->'short_answer' IS NOT NULL
ORDER BY t.id
"""

# Добавляем 'code_ast' и одновременно убираем 'lower': у задания, где ответ — код,
# регистр обязан быть значим (см. DOSHLIFOVKA tsk-261 ниже). Остальные шаги не трогаем.
UPDATE_ONE = """
UPDATE tasks
SET solution_rules = jsonb_set(
    solution_rules,
    '{short_answer,normalization}',
    COALESCE(
      (SELECT jsonb_agg(x)
         FROM jsonb_array_elements(solution_rules->'short_answer'->'normalization') x
        WHERE x NOT IN ('"lower"'::jsonb, '"code_ast"'::jsonb)),
      '[]'::jsonb
    ) || '"code_ast"'::jsonb
)
WHERE id = $1
"""


def is_python_code(value: str) -> bool:
    """Эталон — самостоятельная программа на Python, а не проза, похожая на код."""
    try:
        tree = ast.parse(value.strip())
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    if not tree.body:
        return False
    if not any(isinstance(n, CODE_NODES) for n in ast.walk(tree)):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and CYRILLIC.search(node.id):
            return False
        if isinstance(node, ast.Attribute) and CYRILLIC.search(node.attr):
            return False
        if isinstance(node, ast.keyword) and node.arg and CYRILLIC.search(node.arg):
            return False
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and CYRILLIC.search(node.name):
            return False
        if isinstance(node, ast.arg) and CYRILLIC.search(node.arg):
            return False
    return True


def _dsn() -> str:
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
            rows = await conn.fetch(SELECT_ALL)
            targets: List[int] = []
            for r in rows:
                accepted = [a["value"] for a in json.loads(r["accepted"] or "[]")]
                if accepted and all(is_python_code(v) for v in accepted):
                    targets.append(r["id"])

            total = len(rows)
            print(f"Активных заданий с коротким ответом: {total}")
            print(f"Отобрано как «ответ — код на Python»: {len(targets)}")
            if not targets:
                raise RuntimeError("кандидатов нет — возможно, уже применено")

            # Контроль ДО: сколько заданий уже имеет флаг и сколько его не имеет.
            before = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND solution_rules->'short_answer'->'normalization' ? 'code_ast'"
            )
            print(f"Уже с флагом code_ast до правки: {before}")

            for tid in targets:
                await conn.execute(UPDATE_ONE, tid)

            # Верификация внутри транзакции.
            after = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND solution_rules->'short_answer'->'normalization' ? 'code_ast'"
            )
            flagged_not_target = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND solution_rules->'short_answer'->'normalization' ? 'code_ast' "
                "AND NOT (id = ANY($1::int[]))",
                targets,
            )
            # Явные raise, а не assert: под python -O assert выкидывается,
            # а это единственный контроль корректности прод-записи.
            if after != len(targets):
                raise AssertionError(f"ожидали {len(targets)} с флагом, получили {after}")
            if flagged_not_target != 0:
                raise AssertionError(f"флаг попал на задания вне выборки: {flagged_not_target}")

            # Контроль целостности: остальные шаги нормализации на месте.
            sample = await conn.fetchrow(
                "SELECT id, solution_rules->'short_answer'->'normalization' n "
                "FROM tasks WHERE id = $1",
                targets[0],
            )
            print(f"Пример {sample['id']}: normalization = {sample['n']}")

            # Контроль: lower и code_ast вместе бессмысленны (регистр значим).
            with_lower = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND solution_rules->'short_answer'->'normalization' ? 'code_ast' "
                "AND solution_rules->'short_answer'->'normalization' ? 'lower'"
            )
            if with_lower != 0:
                raise AssertionError(
                    f"у {with_lower} заданий code_ast соседствует с lower — "
                    "регистр обязан быть значим"
                )

            # Контроль: текстовые задания не задеты — их lower остался на месте.
            text_with_lower = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE is_active "
                "AND solution_rules->'short_answer'->'normalization' ? 'lower' "
                "AND NOT (id = ANY($1::int[]))",
                targets,
            )
            print(f"Текстовых заданий с lower (не тронуты): {text_with_lower}")

            print(f"OK: флаг проставлен {len(targets)} заданиям, lower ни у одного из них нет")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
