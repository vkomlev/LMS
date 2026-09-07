# -*- coding: utf-8 -*-
"""tsk-800: переписать условия трёх заданий так, чтобы прочтение осталось одно.

Зачем. Все три эталона пересчитаны и верны (перебор позиций игры и симуляция
исполнителя, tsk-799/tsk-800), но ученик Глеб Анфалов потерял на 3472 и 4067 семь
попыток, считая правильно — по другому прочтению того же текста. Решение оператора
от 07.09: переписывать условие, эталон не трогать, уровень задания не менять.

Что именно двусмысленно (проверено решателем ``scratchpad/solve_games.py``):

* **3472, задание 20.** «Ваня выигрывает своим первым ходом после неудачного хода
  Пети» читается тремя способами, и все три дают осмысленный ответ:
  A «есть хотя бы один неудачный ход Пети, после которого Ваня выигрывает» → 9 (эталон);
  B «Ваня выигрывает при ЛЮБОМ ходе Пети» → 16 (ровно то, что ученик давал 4 раза);
  C «неудачный = Петя упустил победу» → 17.
* **4067, задание 19.** Тот же текст в каноне ФИПИ: A → 20 (эталон), B → невозможно,
  C → 39 (ровно ответ ученика). Прочтение C не выдумано: в задании 4033 курса 1397 оно
  прямо записано определением («если у него есть ход, приводящий к победе, но он
  ошибается»). То есть в LMS одновременно живут оба смысла слова «неудачный».
* **3472, задание 19.** «Петя выигрывает своим первым ходом» допускает чтение «любым
  своим ходом» (даёт 32). Правится одним словом «может» — заодно ради единообразия с
  переписанным заданием 20, иначе две разные формы рядом сами создадут вопрос.
* **4067, задание 20.** Просит два значения, а формат ответа не назван; таблица —
  одна колонка, эталон из четырёх строк.
* **4067** вдобавок не содержит определения выигрышной стратегии, хотя задания 20 и 21
  им пользуются (в 3472 определение есть).
* **2998.** Двусмысленности прочтения нет — ученик промахнулся сам (96276 против
  96726, перестановка цифр в собственном ответе). Зато в тексте порча импорта:
  латинская «B» вместо «в» и «6eз» вместо «без», плюс точка предложения, попавшая
  внутрь блока кода (тот же класс, что tsk-772).

Критерий приёмки (не «звучит понятнее», а проверка): переписанное условие решается
прежними прочтениями — прочтения B и C должны перестать давать осмысленный ответ.

Запуск::

    python scripts/tsk800_fix_stems.py                       # dry-run, откат
    DBCHECK_OK=1 python scripts/tsk800_fix_stems.py --apply   # запись
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
REPORT = project_root / "docs" / "qa" / "tsk800-stem-diff.md"

#: Определение, которого не хватало обоим заданиям блока. Формулировка «выигрышной
#: стратегии» взята слово в слово из 3472 (там она уже есть), второе предложение —
#: то, что снимает прочтение C.
DEFS = (
    "Будем говорить, что игрок имеет выигрышную стратегию, если он может выиграть "
    "при любых ходах противника. Будем говорить, что ход игрока неудачен, если этим "
    "ходом он не выигрывает (независимо от того, был ли у него выигрышный ход)."
)

#: (task_id, [(что заменить, на что), ...]). Каждая подстрока обязана встречаться
#: ровно один раз — иначе скрипт падает, а не правит наугад.
PLAN: list[tuple[int, str, list[tuple[str, str]]]] = [
    (
        3472,
        "ЕГЭ 19–21, «Две кучи камней до 42»",
        [
            (
                "<p>Будем говорить, что игрок имеет выигрышную стратегию, если он "
                "может выиграть при любых ходах противника.</p>",
                f"<p>{DEFS}</p>",
            ),
            (
                "<p>Найдите минимальное значение <em>S</em>, при котором Петя "
                "выигрывает своим первым ходом.</p>",
                "<p>Найдите минимальное значение <em>S</em>, при котором Петя может "
                "выиграть своим первым ходом.</p>",
            ),
            (
                "<p>Для игры, описанной в задании 19, найдите минимальное значение "
                "<em>S</em>, при котором Ваня выигрывает своим первым ходом после "
                "неудачного хода Пети.</p>",
                "<p>Для игры, описанной в задании 19, найдите минимальное значение "
                "<em>S</em>, при котором у Пети есть хотя бы один неудачный первый "
                "ход, после которого Ваня выигрывает своим первым ходом.</p>",
            ),
        ],
    ),
    (
        4067,
        "ЕГЭ 19–21, «Два S для победы Пети»",
        [
            (
                "<p>Известно, что Ваня выиграл своим первым ходом после неудачного "
                "первого хода Пети. Укажите минимальное значение S, когда такая "
                "ситуация возможна.</p>",
                f"<p>{DEFS}</p><p>Найдите минимальное значение S, при котором у Пети "
                "есть хотя бы один неудачный первый ход, после которого Ваня "
                "выигрывает своим первым ходом.</p>",
            ),
            (
                "<p>Найденные значения запишите в ответе в порядке возрастания.</p>",
                "<p>Найденные значения запишите в ответе в порядке возрастания, "
                "каждое в отдельной строке.</p>",
            ),
        ],
    ),
    (
        2998,
        "Черепаха, «Площадь объединения двух фигур»",
        [
            ("находится B начале координат", "находится в начале координат"),
            ("переход к перемещению 6eз рисования", "переход к перемещению без рисования"),
            # Внутри блока кода слова разделены узким неразрывным пробелом (U+202F),
            # поэтому цепляемся за скобку с точкой, а не за «Направо 90».
            ("].<br>```", "]<br>```"),
        ],
    ),
]


def _dsn() -> str:
    """Прод-DSN learn из .mcp.json (в код не хардкодим)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        for arg in cfg["mcpServers"]["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://"):
                dsn = arg.split("?")[0]
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def transform(stem: str, edits: list[tuple[str, str]], task_id: int) -> str:
    """Применить замены, требуя ровно одного вхождения каждой."""
    out = stem
    for old, new in edits:
        found = out.count(old)
        if found != 1:
            raise AssertionError(
                f"id={task_id}: подстрока встречается {found} раз, ожидалась 1:\n  {old[:120]}"
            )
        out = out.replace(old, new, 1)
    return out


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    report: list[str] = ["# tsk-800: было → станет", ""]
    try:
        async with conn.transaction():
            ids = [tid for tid, _, _ in PLAN]
            rows = await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks "
                "WHERE id = ANY($1::int[]) AND is_active = true FOR UPDATE",
                ids,
            )
            if len(rows) != len(ids):
                raise AssertionError(f"ожидал {len(ids)} активных задач, нашёл {len(rows)}")
            by_id = {r["id"]: r for r in rows}

            etalons_before = {
                tid: json.loads(by_id[tid]["solution_rules"])["short_answer"]["accepted_answers"]
                if isinstance(by_id[tid]["solution_rules"], str)
                else dict(by_id[tid]["solution_rules"])["short_answer"]["accepted_answers"]
                for tid in ids
            }

            for tid, label, edits in PLAN:
                row = by_id[tid]
                content = (
                    json.loads(row["task_content"])
                    if isinstance(row["task_content"], str)
                    else dict(row["task_content"])
                )
                old_stem = content.get("stem", "")
                new_stem = transform(old_stem, edits, tid)
                content["stem"] = new_stem

                report.append(f"## Задание {tid} — {label}")
                report.append("")
                for old, new in edits:
                    report.append(f"- **было:** {old}")
                    report.append(f"- **станет:** {new}")
                    report.append("")
                print(f"id={tid} {label}: замен {len(edits)}, длина stem {len(old_stem)} -> {len(new_stem)}")

                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(content, ensure_ascii=False),
                    tid,
                )

            # Проверка ВНУТРИ транзакции: тексты изменились, эталоны — нет.
            verify = await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem, solution_rules FROM tasks "
                "WHERE id = ANY($1::int[])",
                ids,
            )
            for r in verify:
                stem = r["stem"] or ""
                rules = (
                    json.loads(r["solution_rules"])
                    if isinstance(r["solution_rules"], str)
                    else dict(r["solution_rules"])
                )
                if rules["short_answer"]["accepted_answers"] != etalons_before[r["id"]]:
                    raise AssertionError(f"id={r['id']}: эталон изменился — это запрещено")
                for old, _new in dict((tid, e) for tid, _l, e in PLAN)[r["id"]]:
                    if old in stem:
                        raise AssertionError(f"id={r['id']}: старый текст остался: {old[:80]}")
                for _old, new in dict((tid, e) for tid, _l, e in PLAN)[r["id"]]:
                    if new not in stem:
                        raise AssertionError(f"id={r['id']}: новый текст не найден: {new[:80]}")
            print(f"\nПроверка внутри транзакции: {len(verify)}/{len(ids)} обновлены, эталоны не тронуты. OK")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(report), encoding="utf-8")
        print(f"\nЗАПИСАНО И ЗАКОММИЧЕНО. Дифф: {REPORT}")
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
