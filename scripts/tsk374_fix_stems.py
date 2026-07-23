# -*- coding: utf-8 -*-
"""tsk-374, шаг 2: убрать из условий мусор со страниц источников.

ЧТО И ПОЧЕМУ

Разбор `scripts/tsk374_scan.py` по всем 6301 активным заданиям дал четыре класса.

A. КРИТЕРИИ ОЦЕНИВАНИЯ ЭКСПЕРТА — 104 задания (партии `sdamgia:oge:13..16`), 130 таблиц.
   Таблица «Критерии оценивания выполнения задания / Баллы» пересказывает, что должно
   быть в верном ответе («Получены правильные ответы на два вопроса и верно построена
   диаграмма»), то есть это та же утечка, что ловит правило answer-in-stem ([[tsk-254]],
   [[tsk-296]]), только пришедшая из шапки экспертной проверки ОГЭ.

   Режется ровно элемент `<table>…</table>`, а НЕ «от маркера до конца условия»: у 7244
   после таблицы критериев идёт таблица тестов («Для проверки правильности работы
   программы необходимо использовать следующие тесты») — слепой срез отрезал бы часть
   задачи, и снаружи это не всплыло бы ничем (урок [[tsk-370]]).

B. ФОРМА ОТВЕТА SDAMGIA — 27 заданий (`wp_nav:26`). Хвост страницы источника: надпись
   «Ответ:» и поля ввода `<input class="test_inp" name="answer_part_N">`, которые в LMS
   ничего не делают, но выглядят как место для ответа.

C. ГОТОВОЕ РЕШЕНИЕ — 3128 (`tg:ege:839`). В условии лежит разбор: слово «Решение», код
   на Python целиком и путь с машины разработчика `D:/Work/CyberGuru/...`. Срезается от
   «Решение» до конца; постановка задачи («Для выполнения этого задания необходимо
   написать программу.») остаётся.

D. УСЛОВИЕ ТОЛЬКО КАРТИНКОЙ — 2324 (`ext:d4:sdamgia:20260602:52845`). Источник и сам
   держит это условие картинкой (`/get_file?id=123781`), так что «мусор импорта» здесь
   ни при чём, но ученику достаётся нераспознаваемый текст: не выделяется, не ищется,
   не читается экранным диктором и плывёт на телефоне. Текст переносится в условие.
   Привязка проверена трижды: преамбула и финальный вопрос совпадают слово в слово с
   соседним заданием того же курса (2169, `kompege:4694`); алгоритм прочитан с картинки;
   по прочитанному алгоритму пересчитано число точек внутри области — 42, ровно
   сохранённый в LMS ответ. Картинка не сохраняется: она дублировала бы тот же текст.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Перед записью каждое условие
сверяется с тем, на котором собиралась правка. После COMMIT — независимая проверка
ПОСТРОЧНО по всему затронутому множеству, а не агрегатом (урок [[tsk-317]]).

Запуск: python scripts/tsk374_fix_stems.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import ASK_RE, dsn, strip_html  # noqa: E402
from tsk374_scan import (  # noqa: E402
    CRIT_RE, cut_answer_form, cut_criteria, has_content, strip_soft,
)

# ------------------------------------------------------------------ C. 3128

FIX_3128_ID = 3128
# Хвост условия перед разбором — якорь, что правим то самое задание.
FIX_3128_KEEP = "Для выполнения этого задания необходимо написать программу."
FIX_3128_CUT = re.compile(r"<br\s*/?>\s*Решение\s*<br\s*/?>.*$", re.IGNORECASE | re.DOTALL)


def fix_3128(stem: str) -> str:
    """Условие 3128 без готового разбора с кодом и путём разработчика."""
    if FIX_3128_KEEP not in stem:
        raise RuntimeError("3128: постановка задачи не найдена — условие изменилось")
    if "D:/Work/CyberGuru" not in stem:
        raise RuntimeError("3128: пути разработчика уже нет — править нечего")
    out = FIX_3128_CUT.sub("</p>", stem)
    if out == stem:
        raise RuntimeError("3128: блок «Решение» не найден")
    if "D:/Work" in out or "import " in out:
        raise RuntimeError("3128: после среза остался код или путь разработчика")
    return out


# ------------------------------------------------------------------ D. 2324

FIX_2324_ID = 2324
# Условие сейчас — ровно один тег картинки. Якорь: другого текста в нём нет.
FIX_2324_IMG = "c9899e0dd78effe71da8b4a82848a14a5f075843dcc7733d5daa6ccc24234e9f.png"
FIX_2324_ANSWER = "42"
# Преамбула и финальный вопрос — дословно из 2169 (`kompege:4694`), тот же курс 157 и
# та же версия исполнителя (две команды). Отличается только блок алгоритма.
FIX_2324_STEM = (
    "<p>Исполнитель Черепаха действует на плоскости с декартовой системой координат. "
    "В начальный момент Черепаха находится в начале координат, её голова направлена "
    "вдоль положительного направления оси ординат, хвост опущен. При опущенном хвосте "
    "Черепаха оставляет на поле след в виде линии. В каждый конкретный момент известно "
    "положение исполнителя и направление его движения. У исполнителя существует две "
    "команды: </p>"
    "<p><strong>Вперёд n</strong> (где n – целое число), вызывающая передвижение "
    "Черепахи на n единиц в том направлении, куда указывает её голова, и </p>"
    "<p><strong>Направо m</strong> (где m – целое число), вызывающая изменение "
    "направления движения на m градусов по часовой стрелке. </p>"
    "<p>Запись <strong>Повтори k [Команда1 Команда2 … КомандаS] </strong>означает, "
    "что последовательность из S команд повторится k раз. </p>"
    "<p>Черепахе был дан для исполнения следующий алгоритм: </p><p><br/></p>"
    "<p><strong>Повтори 2 [Направо 120 Вперёд 7]</strong><br/>"
    "<strong>Направо 300</strong><br/>"
    "<strong>Повтори 2 [Направо 120 Вперёд 7]</strong>. </p><p><br/></p>"
    "<p>Определите, сколько точек с целочисленными координатами будут находиться внутри "
    "области, ограниченной линией, заданной данным алгоритмом. Точки на линии учитывать "
    "не следует. </p>"
)


def fix_2324(stem: str, answer: str | None) -> str:
    """Условие 2324 текстом вместо картинки."""
    if FIX_2324_IMG not in stem:
        raise RuntimeError("2324: картинка не та — условие изменилось")
    if has_content(strip_soft(strip_html(stem))):
        raise RuntimeError("2324: в условии появился текст — разбирать руками")
    if (answer or "").strip() != FIX_2324_ANSWER:
        raise RuntimeError(
            f"2324: ответ в базе {answer!r} разошёлся с проверенным "
            f"{FIX_2324_ANSWER!r} — привязка не подтверждена")
    return FIX_2324_STEM


# ------------------------------------------------------------------ план правок


def build_plan(rows: dict[int, asyncpg.Record]) -> dict[int, tuple[str, str]]:
    """Карта id → (класс правки, новое условие) по всем четырём классам."""
    plan: dict[int, tuple[str, str]] = {}
    for tid, r in rows.items():
        stem = r["stem"] or ""
        new, kind = stem, None

        if tid == FIX_3128_ID:
            new, kind = fix_3128(stem), "готовое решение с кодом"
        elif tid == FIX_2324_ID:
            new, kind = fix_2324(stem, r["answer"]), "условие только картинкой"
        else:
            if CRIT_RE.search(strip_soft(strip_html(stem))):
                new, removed = cut_criteria(new)
                if not removed:
                    raise RuntimeError(f"{tid}: таблица критериев не найдена")
                kind = f"критерии оценивания ({len(removed)} табл.)"
            if re.search(r"(?i)<input\b", new):
                new, removed = cut_answer_form(new)
                if not removed:
                    raise RuntimeError(f"{tid}: форма ответа не найдена")
                kind = "форма ответа sdamgia" if kind is None else kind + " + форма ответа"

        if kind is None or new == stem:
            raise RuntimeError(f"{tid}: правка не собралась — условие в базе изменилось")

        left = strip_soft(strip_html(new))
        if not has_content(left):
            raise RuntimeError(f"{tid}: после правки условие осталось без текста")
        if CRIT_RE.search(left) or re.search(r"(?i)<input\b", new):
            raise RuntimeError(f"{tid}: после правки мусор остался")
        if not ASK_RE.search(left):
            raise RuntimeError(f"{tid}: после правки в условии нет постановки задачи")
        plan[tid] = (kind, new)
    return plan


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, "
            "       task_content->>'stem' AS stem, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE is_active AND ("
            "     id = ANY($1::int[])"
            "  OR replace(task_content->>'stem', U&'\\00AD', '') "
            "       ~ '(Критерии оценивани|Максимальный балл)'"
            "  OR task_content->>'stem' ILIKE '%<input%')",
            [FIX_3128_ID, FIX_2324_ID])}
        missing = [i for i in (FIX_3128_ID, FIX_2324_ID) if i not in rows]
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")

        plan = build_plan(rows)
        ids = sorted(plan)

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"], "stem": rows[i]["stem"]}
             for i in ids], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних значений: {backup_path} ({len(ids)} заданий)\n")

        by_kind: dict[str, int] = {}
        for tid in ids:
            kind = plan[tid][0].split(" (")[0]
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            print(f"  {kind}: {n}")
        cut = sum(len(rows[i]["stem"]) - len(plan[i][1]) for i in ids)
        print(f"\nВсего заданий: {len(ids)}; срезано {cut} символов разметки")

        print("\nПримеры до/после (текст условия, хвост):")
        for tid in (7151, 7244, 3763, 3128, 2324):
            if tid not in plan:
                continue
            was = strip_soft(strip_html(rows[tid]["stem"]))
            now = strip_soft(strip_html(plan[tid][1]))
            print(f"\n[{tid}] {rows[tid]['external_uid']} — {plan[tid][0]}")
            print(f"  было ({len(was)} симв.): …{was[-180:]}")
            print(f"  стало ({len(now)} симв.): …{now[-180:]}")

        if not apply:
            print("\nDRY-RUN: в базу ничего не записано. Повторить с --apply.")
            return

        async with conn.transaction():
            for tid in ids:
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set("
                    "  task_content, '{stem}', to_jsonb($2::text), true) "
                    "WHERE id = $1", tid, plan[tid][1])
        print("\nCOMMIT выполнен. Построчная проверка после записи:")

        check = {r["id"]: r["stem"] for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
            ids)}
        bad = [i for i in ids if check.get(i) != plan[i][1]]
        print(f"  сверено заданий: {len(ids)}; совпало: {len(ids) - len(bad)}")
        if bad:
            raise RuntimeError(f"после COMMIT не совпало: {bad}")
        # Контрольный вопрос к самой базе, а не к плану: мусора не осталось нигде.
        left = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE is_active AND ("
            "     replace(task_content->>'stem', U&'\\00AD', '') "
            "       ~ '(Критерии оценивани|Максимальный балл)'"
            "  OR task_content->>'stem' ILIKE '%<input%')")
        print(f"  осталось заданий с мусором в базе: {left}")
        if left:
            raise RuntimeError("мусор остался — разбирать руками")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.backup, args.apply))
