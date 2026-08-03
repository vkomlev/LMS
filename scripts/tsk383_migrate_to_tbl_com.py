# -*- coding: utf-8 -*-
"""tsk-383: мини-тесты Python (SA_COM) -> TBL_COM — один вывод программы на поле.

ЗАЧЕМ
Мини-тесты вида «Запустите программу 3 раза со значениями X, Y, Z. Поместите
вывод всех 3 запусков в поле "Ответ" через пробел/перевод строки» жили как
SA_COM — ОДНО поле, а ответ мог быть длинной строкой из нескольких смысловых
фрагментов («Первое число больше Второе число больше Числа равны»). Тип
TBL_COM (tsk-366) даёт по полю на каждый вывод программы. Форма ответа здесь —
ОДИН СТОЛБЕЦ (table.columns=1), а не сетка N x M: решение оператора 2026-07-23
зафиксировано в tsk-383.

ОТБОР КАНДИДАТОВ
139 совпадений по маркерам «Запустите программу»/«через пробел»/«Вывод[ы]
программы» — НЕ критерий отбора сам по себе (проверено вручную по 2026-08-03,
tsk-383 п.1). Реальный критерий: ответ состоит из N НЕЗАВИСИМЫХ выводов (N
разных запусков с разными входными данными, либо N явно перечисленных в
условии значений), а не один вычисленный результат, который просто печатается
через пробел (список/фильтр/сумма) -- такие остаются SA_COM без изменений.
Вручную прочитано ~80 заданий по всем 9 курсам; итоговый список — 53 задания
ниже (CANDIDATES), явно перечисленные, как и в tsk-366 (NE_TABLICA/YAVNYE_STOLBCY).

ЧТО ДЕЛАЕТ
1. task_content.type -> TBL_COM, task_content.table = {"columns": 1}.
   Правила проверки (solution_rules) НЕ переписываются: эталон уже лежит в
   short_answer.accepted_answers, а движок TBL_COM columns=1 читает тот же
   блок (различие только в разборе ответа на ячейки, см. tsk-383 фикс
   checking_service._table_cells — целая строка = одна ячейка при >1 строк).
2. ИСКЛЮЧЕНИЕ (REWRITE, 4 задания): у части заданий несколько запусков
   склеены в ОДНУ строку без переноса ("Hello, Alice! ... Hello, Bob! ...").
   Для них solution_rules.short_answer.accepted_answers[0].value переписывается
   на "\n".join(rows) — граница расставлена вручную по смыслу и проверена
   реверсивностью: " ".join(rows) обязан посимвольно совпасть с исходным
   значением (ассерт в скрипте, до записи).

ВЕРИФИКАЦИЯ (два независимых слоя, оба поштучно — не агрегатом, урок tsk-317)
1. Самосогласованность: эталон (после миграции) прогоняется через настоящий
   CheckingService и обязан давать is_correct=True; плюс мутации (хвостовые
   пробелы/пустые строки/регистр) — ради этого миграция и делается.
2. Историческая регрессия (урок tsk-325/tsk-383 п.4): для КАЖДОГО task_results
   с этим task_id пересчитывается вердикт под НОВЫМ TBL_COM-правилом и
   сравнивается со старым (SA_COM) is_correct. Ученик, которого раньше
   засчитали (is_correct=True), обязан остаться засчитанным.

Запуск: dry-run по умолчанию;
  python scripts/tsk383_migrate_to_tbl_com.py
  DBCHECK_OK=1 python scripts/tsk383_migrate_to_tbl_com.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

checking = CheckingService()

# ─── Кандидаты (ручной отбор, 2026-08-03) ───────────────────────────────────

# Явные "N раз" / "N значений" мини-тесты с single-token или уже многострочным
# (с реальным \n в accepted_answers) ответом -- перенос БЕЗ правки value.
CANDIDATES: list[int] = [
    # 103 (числа, 1 явный кандидат из 18: остальные -- один запуск/одно значение)
    67,
    # 104 (функции)
    42, 89,
    # 105 (условные, 1 из 2)
    306,
    # 106 (первая программа)
    111, 113, 115,
    # 107 (словари)
    340, 349, 360, 362, 363, 366, 368, 369,
    # 108 (строки, 3 из 20)
    146, 147, 148,
    # 109 (списки, 1 из 5)
    277,
    # 110 (циклы). 223 ИСКЛЮЧЕНО: ответ — треугольник из "*", normalization
    # включает strip_punctuation, который стирает "*" как пунктуацию с ОБЕИХ
    # сторон сравнения. Это латентный дефект уже у SA_COM (совпадение по
    # схлопнутой в пустую строку записи), не специфичный для TBL_COM — чинить
    # отдельной задачей (снять strip_punctuation с этого правила), не здесь.
    217, 220, 221, 222, 224, 225, 231,
    # 111 (условные, 20 из 22 -- шахматы/booly/if-elif семейства)
    171, 172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185,
    186, 187, 188, 189, 190, 204, 205,
    # 108 (строки) -- отдельно: только эти явные "N раз"
    145,
]

# Несколько запусков склеены в ОДНУ строку без \n -- граница расставлена
# вручную. Реверсивность (" ".join(rows) == исходное value) проверяется в
# коде перед записью, а не только на бумаге.
REWRITE: dict[int, list[str]] = {
    75: ["Hello, Alice! You are 18 years old.", "Hello, Bob! You are 25 years old."],
    77: ["Имя студента: Иван", "возраст: 20", "факультет: Информатика"],
    80: ["Привет, Андрей!", "Привет, Мария!"],
    94: ["Hello from plugin1", "Hello from plugin2"],
}
CANDIDATES = sorted(set(CANDIDATES) | set(REWRITE))

SELECT_TARGETS = """
SELECT id, course_id, max_score, task_content, solution_rules
FROM tasks
WHERE id = ANY($1::int[]) AND is_active
ORDER BY id
"""

UPDATE_TASK = """
UPDATE tasks
SET task_content = $2::jsonb, solution_rules = $3::jsonb
WHERE id = $1
"""

SELECT_HISTORY = """
SELECT task_id, id AS result_id, answer_json, is_correct
FROM task_results
WHERE task_id = ANY($1::int[])
"""


def _dsn() -> str:
    """Прод-DSN learn: из окружения либо из .mcp.json (как в tsk-366)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def _proverit(content: dict, rules: dict, task_type: str, otvet: str) -> bool | None:
    result = checking.check_task(
        TaskContent.model_validate(content),
        SolutionRules.model_validate(rules),
        StudentAnswer(type=task_type, response=StudentResponse(value=otvet)),
    )
    return result.is_correct


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_TARGETS, CANDIDATES)
            print(f"Кандидатов в списке: {len(CANDIDATES)}, найдено активных: {len(rows)}")
            missing = sorted(set(CANDIDATES) - {int(r["id"]) for r in rows})
            if missing:
                raise AssertionError(f"не найдены/не активны: {missing}")

            plan: list[tuple[int, dict, dict, str, dict, dict]] = []
            for r in rows:
                task_id = int(r["id"])
                content = json.loads(r["task_content"])
                rules = json.loads(r["solution_rules"]) if r["solution_rules"] else {}
                if not isinstance(rules, dict):
                    rules = {}

                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={task_id}: ожидали SA_COM, а тип {content.get('type')!r}")

                accepted = (rules.get("short_answer") or {}).get("accepted_answers") or []
                if not accepted or not isinstance(accepted[0], dict) or not accepted[0].get("value"):
                    raise AssertionError(f"id={task_id}: нет accepted_answers[0].value — не ожидали")
                old_value = accepted[0]["value"]
                content_before = json.loads(json.dumps(content))
                rules_before = json.loads(json.dumps(rules))

                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}

                if task_id in REWRITE:
                    rows_ = REWRITE[task_id]
                    if " ".join(rows_) != old_value:
                        raise AssertionError(
                            f"id={task_id}: реверсивность REWRITE не сходится — "
                            f"{' '.join(rows_)!r} != {old_value!r}"
                        )
                    new_value = "\n".join(rows_)
                    rules["short_answer"]["accepted_answers"][0]["value"] = new_value
                    etalon = new_value
                else:
                    etalon = old_value

                plan.append((task_id, content, rules, etalon, content_before, rules_before))

            print("\nПримеры перевода:")
            for task_id, content, _, etalon, _, _ in plan[:5]:
                print(f"  id={task_id} columns=1 эталон={etalon[:60]!r}")

            # ─── Запись ─────────────────────────────────────────────────────
            for task_id, content, rules, _, _, _ in plan:
                await conn.execute(
                    UPDATE_TASK, task_id,
                    json.dumps(content, ensure_ascii=False),
                    json.dumps(rules, ensure_ascii=False),
                )
            print(f"\nЗаписано: {len(plan)} заданий переведено в TBL_COM (columns=1).")

            # ─── Верификация 1: самосогласованность (поштучно) ──────────────
            ids = [p[0] for p in plan]
            posle = {
                int(row["id"]): row
                for row in await conn.fetch(
                    "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])",
                    ids,
                )
            }

            oshibki: list[str] = []
            for task_id, _, _, etalon, _, _ in plan:
                row = posle[task_id]
                content = json.loads(row["task_content"])
                rules = json.loads(row["solution_rules"]) if row["solution_rules"] else {}

                if content.get("type") != "TBL_COM":
                    oshibki.append(f"id={task_id}: тип не TBL_COM после записи")
                    continue
                if (content.get("table") or {}).get("columns") != 1:
                    oshibki.append(f"id={task_id}: table.columns не 1 после записи")
                    continue

                if _proverit(content, rules, "TBL_COM", etalon) is not True:
                    oshibki.append(f"id={task_id}: эталон НЕ засчитывается после перевода")
                    continue

                мутации = [f"  {etalon}  ", etalon.upper()]
                if "\n" in etalon:
                    мутации.append(etalon.replace("\n", "\n\n") + "\n")
                else:
                    мутации.append(etalon.replace(" ", "  "))
                for мутация in мутации:
                    if _proverit(content, rules, "TBL_COM", мутация) is not True:
                        oshibki.append(f"id={task_id}: мутация {мутация[:40]!r} не засчитана")
                        break

            # ─── Верификация 2: историческая регрессия (поштучно) ───────────
            #
            # Сверяем НЕ с застывшим task_results.is_correct (он мог быть
            # вычислен по ПРОШЛОЙ версии solution_rules — правило могли
            # отредактировать уже после того, как ученика проверили, и тогда
            # расхождение — дрейф правила, а не эффект миграции). Сверяем с
            # вердиктом SA_COM «сейчас», на ТЕКУЩЕМ правиле (до перевода в
            # TBL_COM) — так изолируется именно эффект смены типа.
            history = await conn.fetch(SELECT_HISTORY, ids)
            print(f"\nИсторических task_results на выборке: {len(history)}")
            regressii: list[str] = []
            проверено_историй = 0
            content_new_by_id = {tid: c for tid, c, _, _, _, _ in plan}
            rules_new_by_id = {int(r["id"]): json.loads(posle[int(r["id"])]["solution_rules"]) for r in rows}
            content_before_by_id = {tid: cb for tid, _, _, _, cb, _ in plan}
            rules_before_by_id = {tid: rb for tid, _, _, _, _, rb in plan}
            for h in history:
                task_id = int(h["task_id"])
                answer_json = json.loads(h["answer_json"]) if h["answer_json"] else {}
                response = answer_json.get("response") or {}
                value = response.get("value")
                if value is None or not isinstance(value, str) or not value.strip():
                    continue
                проверено_историй += 1
                sa_com_now = _proverit(
                    content_before_by_id[task_id], rules_before_by_id[task_id], "SA_COM", value
                )
                tbl_com_new = _proverit(content_new_by_id[task_id], rules_new_by_id[task_id], "TBL_COM", value)
                if sa_com_now is True and tbl_com_new is not True:
                    regressii.append(
                        f"task_id={task_id} result_id={h['result_id']}: "
                        f"SA_COM сейчас=True, TBL_COM после перевода={tbl_com_new} "
                        f"(ответ {value[:60]!r})"
                    )

            print(f"Историй с непустым value проверено: {проверено_историй}")
            if regressii:
                oshibki.extend(regressii[:30])

            if oshibki:
                for e in oshibki[:40]:
                    print(f"  ОШИБКА: {e}")
                raise AssertionError(f"верификация не пройдена: {len(oshibki)} проблем")

            print(f"OK: проверено поштучно {len(plan)} заданий + {проверено_историй} исторических "
                  f"ответов, регрессий нет.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
