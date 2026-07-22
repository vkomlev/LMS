# -*- coding: utf-8 -*-
"""tsk-373, добор: убрать кириллические двойники из имён переменных задания 4551.

ЧТО НЕ ТАК
У задания 4551 (`crylov:v1t2`, курс 148 «Задание 2 ЕГЭ. Таблицы истинности») переменные в
условии перечислены как «w, х, у, z», где **х и у — русские буквы** (U+0445, U+0443), а
формула выше и принимаемый ответ `wxzy` — латиница. Внешне текст неотличим, но ученик,
скопировавший буквы из условия (или прочитавший их как русские и набравший на русской
раскладке), отправит `wхzу` кириллицей. Проверка приводит ответ только `trim`+`lower`
(`app/services/checking_service.py`), кириллическая «х» латинской «x» не равна — верный
ответ засчитается как неверный.

ПОЧЕМУ ПРАВИТСЯ ТЕКСТ, А НЕ СПИСОК ОТВЕТОВ
Дефект — смешение алфавитов в самом условии. Добавить второй принимаемый ответ означало бы
лечить симптом у одного задания и разойтись с остальными 90 заданиями с буквенным ответом.
Устойчивость к раскладке — это шаг нормализации на уровне платформы (в
`NormalizationStep` такого шага нет), отдельная задача.

ОХВАТ
Сплошная проверка активных заданий двумя проходами: (1) буквы ответа против имён переменных,
объявленных в условии; (2) одиночные кириллические двойники букв ответа во всём тексте.
Первый дал ровно один случай — 4551. Второй дал 11, но десять из них — русские предлоги
«в», «с», «к», «а» в обычном тексте, не переменные.

Правится только точное перечисление «w, х, у, z» (три вхождения: два «каждая из переменных»
и одно «напишите буквы»). Замена посимвольная по всему тексту недопустима: те же буквы
стоят внутри русских слов.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Бэкап — до записи, проверка — после COMMIT.

Запуск: python scripts/tsk373_fix_4551_homoglyphs.py --backup <файл.json> [--apply]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import dsn  # noqa: E402

TASK_ID = 4551
CYR_LIST = "w, х, у, z"   # w, х, у, z — х и у кириллические
LAT_LIST = "w, x, y, z"
EXPECTED = 3                        # сколько раз перечисление встречается в условии
HOMOGLYPHS = "ху"         # что не должно остаться среди имён переменных


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        row = await conn.fetchrow(
            "SELECT id, external_uid, is_active, task_content->>'stem' AS stem, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS ans "
            "FROM tasks WHERE id = $1", TASK_ID)
        if row is None:
            raise RuntimeError(f"задание {TASK_ID} не найдено")
        if not row["is_active"]:
            raise RuntimeError(f"задание {TASK_ID} неактивно, править нечего")

        stem = row["stem"]
        found = stem.count(CYR_LIST)
        if found != EXPECTED:
            raise RuntimeError(
                f"перечисление переменных кириллицей найдено {found} раз, ожидалось "
                f"{EXPECTED} — условие изменилось, правку не делаю")
        new_stem = stem.replace(CYR_LIST, LAT_LIST)
        if len(new_stem) != len(stem):
            raise RuntimeError("длина условия изменилась — замена задела лишнее")
        # в тексте должны остаться русские слова с этими буквами: проверяем, что мы
        # поправили именно перечисления, а не вычистили букву отовсюду
        left = sum(new_stem.count(ch) for ch in HOMOGLYPHS)
        print(f"Задание {TASK_ID} ({row['external_uid']}), ответ {row['ans']!r}")
        print(f"  перечислений «{CYR_LIST}» → «{LAT_LIST}»: {found}")
        print(f"  кириллических х/у осталось в тексте (внутри русских слов): {left}")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            {"id": TASK_ID, "external_uid": row["external_uid"], "stem": stem},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  бэкап условия: {backup_path}")

        async with conn.transaction():
            await conn.execute(
                "UPDATE tasks SET task_content = "
                "  jsonb_set(task_content, '{stem}', to_jsonb($2::text)) WHERE id = $1",
                TASK_ID, new_stem)
            check = await conn.fetchrow(
                "SELECT task_content->>'stem' AS stem, "
                "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS ans "
                "FROM tasks WHERE id = $1", TASK_ID)
            problems = []
            if CYR_LIST in check["stem"]:
                problems.append("кириллическое перечисление осталось")
            if check["stem"].count(LAT_LIST) != EXPECTED:
                problems.append("латинских перечислений не столько, сколько ожидалось")
            if check["ans"] != row["ans"]:
                problems.append("ответ изменился, а не должен был")
            if problems:
                raise AssertionError(f"проверка внутри транзакции не прошла: {problems}")
            print("Внутри транзакции: условие обновлено и проверено, ответ не тронут.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetchrow(
            "SELECT task_content->>'stem' AS stem, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS ans "
            "FROM tasks WHERE id = $1", TASK_ID)
        idx = after["stem"].find(LAT_LIST)
        frag = after["stem"][max(0, idx - 60):idx + 20]
        print(f"  ответ: {after['ans']!r}")
        print(f"  фрагмент: …{frag}…")
        print("  коды букв перечисления: "
              + " ".join(f"{ch}=U+{ord(ch):04X}" for ch in LAT_LIST if ch.isalpha()))
        if CYR_LIST in after["stem"]:
            print("  ПРОБЛЕМА: кириллица осталась")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
