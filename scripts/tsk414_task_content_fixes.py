# -*- coding: utf-8 -*-
"""tsk-414: точечные правки stem/solution_rules отдельных заданий курса 88.

1. id=114 (класс D, слив ответа): пример формата ответа в условии дословно
   совпадал с верным ответом ИМЕННО этой задачи ("9 int Привет, мир! str").
   Заменяем на абстрактный плейсхолдер формата, не совпадающий ни с каким
   реальным ответом.
2. id=46 (класс E, терминология): "периметр окружности" -> "длина окружности"
   (окружность - кривая, у неё нет периметра, только длина).
3. id=64 (класс E, опечатка): пропущен пробел/символ сравнения в
   "h1:m1:s1ge h:m:s" -> "h1:m1:s1 >= h:m:s".
4. id=55,56,59 (класс C, автопроверка): strip_punctuation в normalization
   стирает "." и "-" ДО сравнения (см. _PUNCT_RE = r"[^\\w\\s]" в
   checking_service.py) - для чисто числового ответа это значит, что "0.5"
   и "05" неотличимы, "-8" и "8" неотличимы и т.п. Убираем
   strip_punctuation из normalization этих 3 явно подтверждённых QA задач
   (chisla-v-python:10/11/14) - trim/lower/collapse_spaces остаются.
   Дальнейшие ~15 подобных задач в дереве курса 88 - за рамками (см. отчёт).
5. id=138,139,140,141 (класс C, кириллица/латиница "о"): условие показывает
   единственный символ "о" (подтверждено Cyrillic U+043E), а в самой строке
   "Программирование на Python это весело и полезно" встречается ОДНА
   латинская 'o' - внутри слова Python. Ученик, добросовестно скопировавший
   ЛАТИНСКУЮ "o" (визуально неотличима), получит другой, но самосогласованный
   верный результат. Добавляем этот результат вторым accepted_answer (score
   тот же), эталон на кириллице не трогаем.

Запуск: dry-run по умолчанию;
  python scripts/tsk414_task_content_fixes.py
  DBCHECK_OK=1 python scripts/tsk414_task_content_fixes.py --apply
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


def _dsn() -> str:
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


# ---------- 1. id=114 слив ответа ----------

OLD_STEM_114 = "Например, формат: `9 int Привет, мир! str`."
NEW_STEM_114 = "Например, формат: `<значение1> <тип1> <значение2> <тип2>`."


def fix_stem_114(stem: str) -> str:
    assert stem.count(OLD_STEM_114) == 1
    new_stem = stem.replace(OLD_STEM_114, NEW_STEM_114)
    assert "9 int" not in new_stem
    return new_stem


# ---------- 2. id=46 терминология ----------

OLD_STEM_46 = "выводит её периметр (длину окружности `L = 2·π·r`), округлённый\nдо 2 знаков после запятой."
NEW_STEM_46 = "выводит длину окружности (`L = 2·π·r`), округлённую\nдо 2 знаков после запятой."


def fix_stem_46(stem: str) -> str:
    assert stem.count(OLD_STEM_46) == 1
    return stem.replace(OLD_STEM_46, NEW_STEM_46)


# ---------- 3. id=64 опечатка ----------

OLD_STEM_64 = "h1:m1:s1ge h:m:s"
NEW_STEM_64 = "h1:m1:s1 >= h:m:s"


def fix_stem_64(stem: str) -> str:
    assert stem.count(OLD_STEM_64) == 1
    return stem.replace(OLD_STEM_64, NEW_STEM_64)


STEM_FIXES = {
    114: fix_stem_114,
    46: fix_stem_46,
    64: fix_stem_64,
}


# ---------- 4. numeric strip_punctuation ----------

NORMALIZATION_FIX_IDS = [55, 56, 59]


def fix_normalization(rules: dict) -> dict:
    sa = rules["short_answer"]
    assert sa["normalization"] == ["trim", "lower", "strip_punctuation", "collapse_spaces"], sa["normalization"]
    new_rules = json.loads(json.dumps(rules))
    new_rules["short_answer"]["normalization"] = ["trim", "lower", "collapse_spaces"]
    return new_rules


# ---------- 5. cyrillic/latin "о" alternates ----------

ALT_ANSWERS = {
    138: "24",  # find, латинская 'o' внутри Python
    139: "1",   # count, латинская 'o' встречается 1 раз (в Python)
    140: "Программирование на Pyth*n это весело и полезно",  # replace
    141: "24",  # rfind, единственное вхождение латинской 'o'
}


def fix_alt_answer(rules: dict, task_id: int) -> dict:
    sa = rules["short_answer"]
    existing = [a["value"] for a in sa["accepted_answers"]]
    assert len(existing) == 1, f"id={task_id}: ожидался 1 accepted_answer, найдено {len(existing)}"
    alt = ALT_ANSWERS[task_id]
    assert alt not in existing
    new_rules = json.loads(json.dumps(rules))
    base_score = new_rules["short_answer"]["accepted_answers"][0]["score"]
    new_rules["short_answer"]["accepted_answers"].append({"value": alt, "score": base_score})
    return new_rules


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            # -- stem fixes --
            for task_id, fixer in STEM_FIXES.items():
                row = await conn.fetchrow("SELECT task_content FROM tasks WHERE id=$1", task_id)
                assert row is not None, f"id={task_id} не найден"
                tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else dict(row["task_content"])
                old_stem = tc["stem"]
                new_stem = fixer(old_stem)
                print(f"--- task {task_id} stem ---")
                print(f"ДО:    {old_stem[:120]!r}")
                print(f"ПОСЛЕ: {new_stem[:120]!r}")
                if apply:
                    new_tc = dict(tc)
                    new_tc["stem"] = new_stem
                    await conn.execute(
                        "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                        json.dumps(new_tc, ensure_ascii=False), task_id,
                    )
                    after = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", task_id)
                    if after != new_stem:
                        raise AssertionError(f"id={task_id}: stem после UPDATE не совпадает с ожидаемым")

            # -- normalization fixes --
            for task_id in NORMALIZATION_FIX_IDS:
                row = await conn.fetchrow("SELECT solution_rules FROM tasks WHERE id=$1", task_id)
                assert row is not None, f"id={task_id} не найден"
                rules = json.loads(row["solution_rules"]) if isinstance(row["solution_rules"], str) else dict(row["solution_rules"])
                new_rules = fix_normalization(rules)
                print(f"--- task {task_id} normalization ---")
                print(f"ДО:    {rules['short_answer']['normalization']}")
                print(f"ПОСЛЕ: {new_rules['short_answer']['normalization']}")
                if apply:
                    await conn.execute(
                        "UPDATE tasks SET solution_rules = $1::jsonb WHERE id = $2",
                        json.dumps(new_rules, ensure_ascii=False), task_id,
                    )
                    after = await conn.fetchval(
                        "SELECT solution_rules->'short_answer'->'normalization' FROM tasks WHERE id=$1", task_id
                    )
                    after_list = json.loads(after) if isinstance(after, str) else after
                    if after_list != new_rules["short_answer"]["normalization"]:
                        raise AssertionError(f"id={task_id}: normalization после UPDATE не совпадает")

            # -- alt answers --
            for task_id in ALT_ANSWERS:
                row = await conn.fetchrow("SELECT solution_rules FROM tasks WHERE id=$1", task_id)
                assert row is not None, f"id={task_id} не найден"
                rules = json.loads(row["solution_rules"]) if isinstance(row["solution_rules"], str) else dict(row["solution_rules"])
                new_rules = fix_alt_answer(rules, task_id)
                print(f"--- task {task_id} alt answer ---")
                print(f"Добавлен вариант: {ALT_ANSWERS[task_id]!r}")
                if apply:
                    await conn.execute(
                        "UPDATE tasks SET solution_rules = $1::jsonb WHERE id = $2",
                        json.dumps(new_rules, ensure_ascii=False), task_id,
                    )
                    after = await conn.fetchval(
                        "SELECT solution_rules->'short_answer'->'accepted_answers' FROM tasks WHERE id=$1", task_id
                    )
                    after_list = json.loads(after) if isinstance(after, str) else after
                    values = [a["value"] for a in after_list]
                    if ALT_ANSWERS[task_id] not in values:
                        raise AssertionError(f"id={task_id}: альтернативный ответ не сохранился")

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
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
