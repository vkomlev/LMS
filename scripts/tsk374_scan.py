# -*- coding: utf-8 -*-
"""tsk-374, шаг 1: разбор мусора со страниц источников в условии заданий (read-only).

ЗАЧЕМ
В `task_content.stem` осел мусор со страниц источников. Автопроверке он не мешает —
проверяется только ответ, — поэтому ни один существующий контроль его не видит. Ученик
же читает его наравне с условием, а часть мусора прямо подсказывает ответ.

КЛАССЫ

A. КРИТЕРИИ ОЦЕНИВАНИЯ ЭКСПЕРТА — таблица «Критерии оценивания выполнения задания /
   Баллы» со страницы sdamgia (партии `sdamgia:oge:13..16`). Пересказывает, что должно
   быть в верном ответе: «Получены правильные ответы на два вопроса и верно построена
   диаграмма».

B. ФОРМА ОТВЕТА SDAMGIA — хвост страницы источника: «Ответ:» и поля ввода
   `<input class="test_inp" name="answer_part_N">`, которые в LMS ничего не делают.

Мягкие переносы `U+00AD` внутри слов sdamgia обязательны к учёту: без нормализации
поиск «Критерии оценивания» не находит ни одного задания из партии.

ПОЧЕМУ РЕЗКА ПО ТАБЛИЦЕ, А НЕ «ОТ МАРКЕРА ДО КОНЦА»
Слепой срез «от слова „Критерии“ до конца условия» отрезал бы часть задачи там, где
после критериев идёт содержательный хвост (урок [[tsk-370]]: обрыв условия не виден
снаружи, потому что и текст, и ответ на месте). Поэтому вырезается ровно элемент
`<table>…</table>`, а всё, что осталось после него, скрипт показывает глазами.

Ничего не пишет в БД. На выходе JSON для разбора и шага 2 (`tsk374_fix_stems.py`).

Запуск:  python scripts/tsk374_scan.py --out <файл.json>
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

SOFT = dict.fromkeys(map(ord, "­​﻿"), None)

# Маркеры экспертной шапки. Ищутся по тексту БЕЗ мягких переносов.
CRIT_RE = re.compile(r"Критери\w* оценивани|Максимальный балл", re.IGNORECASE)

# Хвост страницы sdamgia: надпись «Ответ:» и поля ввода. Забирается вместе с обёртками
# `<p>` и разделителем `<p>&nbsp;</p>`, иначе от формы остаются пустые абзацы.
ANSWER_FORM_RE = re.compile(
    r"(?:\s*<p>\s*(?:&nbsp;|\s)*</p>)?"                   # разделитель перед формой
    r"\s*<p>\s*<span[^>]*>\s*Ответ:\s*</span>\s*"         # надпись «Ответ:»
    r"(?:<p>\s*)?"                                        # sdamgia не закрывает <p>
    r"(?:<input\b[^>]*>\s*)+",                            # одно или несколько полей ввода
    re.IGNORECASE,
)

TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
# Пустые абзацы-обёртки и разделители, которыми sdamgia отбивает таблицу критериев
FILLER_RE = re.compile(
    r"(?:\s*<p[^>]*>\s*(?:&nbsp;|\s)*</p>)+\s*$", re.IGNORECASE)


def strip_soft(s: str) -> str:
    """Строка без мягких переносов и невидимых пробелов."""
    return (s or "").translate(SOFT)


def has_content(plain: str) -> bool:
    """В тексте есть хоть одна буква или цифра.

    Проверять на пустую строку недостаточно: у задания с одной картинкой от снятия
    разметки остаётся точка-разделитель абзаца, и «пустое условие» проходит мимо.
    """
    return bool(re.search(r"[^\W_]", plain, re.UNICODE))


def cut_criteria(stem: str) -> tuple[str, list[str]]:
    """Условие без таблиц с критериями оценивания. Возвращает (новое условие, что срезано).

    Вырезается ровно элемент `<table>…</table>`, чей текст содержит маркер экспертной
    шапки, плюс пустые абзацы-обёртки непосредственно перед ним. Вложенных таблиц в
    разметке sdamgia нет — это проверяется явно.
    """
    removed: list[str] = []
    out = stem
    while True:
        hit = None
        for m in TABLE_RE.finditer(out):
            block = m.group(0)
            if "<table" in block[6:].lower():
                raise RuntimeError("вложенная таблица — резать регуляркой нельзя")
            if CRIT_RE.search(strip_soft(strip_html(block))):
                hit = m
                break
        if hit is None:
            return out, removed
        removed.append(hit.group(0))
        head = FILLER_RE.sub("", out[:hit.start()])
        out = head + out[hit.end():]


def after_criteria(stem: str) -> str:
    """Читаемый текст, идущий ПОСЛЕ последней таблицы критериев.

    Если он непуст — слепой срез «от маркера до конца условия» отрезал бы часть задачи.
    Ради этой проверки резка и сделана по границам элемента `<table>`.
    """
    last = None
    for m in TABLE_RE.finditer(stem):
        if CRIT_RE.search(strip_soft(strip_html(m.group(0)))):
            last = m
    return "" if last is None else strip_soft(strip_html(stem[last.end():]))


def cut_answer_form(stem: str) -> tuple[str, list[str]]:
    """Условие без формы ответа sdamgia («Ответ:» + поля ввода)."""
    removed = [m.group(0) for m in ANSWER_FORM_RE.finditer(stem)]
    return ANSWER_FORM_RE.sub("", stem), removed


def tail_text(stem: str, n: int = 220) -> str:
    """Хвост читаемого текста условия — чтобы глазами увидеть, чем оно теперь кончается."""
    return strip_soft(strip_html(stem))[-n:]


async def main(out_path: Path) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, "
            "       task_content->>'stem' AS stem, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE is_active ORDER BY id")
    finally:
        await conn.close()

    report: dict[str, list[dict]] = {"criteria": [], "answer_form": [], "empty_stem": []}
    for r in rows:
        stem = r["stem"] or ""
        plain = strip_soft(strip_html(stem))
        item = {"id": r["id"], "external_uid": r["external_uid"], "len_before": len(stem)}

        if not has_content(plain):
            report["empty_stem"].append(item | {"answer": r["answer"], "stem": stem})
            continue

        new = stem
        if CRIT_RE.search(plain):
            new, removed = cut_criteria(new)
            left = strip_soft(strip_html(new))
            report["criteria"].append(item | {
                "tables_removed": len(removed),
                "len_after": len(new),
                "residual_marker": bool(CRIT_RE.search(left)),   # маркер остался — разбирать руками
                "has_ask": bool(ASK_RE.search(left)),            # постановка задачи на месте
                "text_len_before": len(plain),
                "text_len_after": len(left),
                # что стояло ПОСЛЕ таблицы критериев — слепой срез это бы потерял
                "after_criteria": after_criteria(stem)[:300],
                "tail_after": tail_text(new),
            })

        if re.search(r"(?i)<input\b", new):
            new2, removed = cut_answer_form(new)
            left = strip_soft(strip_html(new2))
            report["answer_form"].append(item | {
                "forms_removed": len(removed),
                "len_after": len(new2),
                "residual_input": bool(re.search(r"(?i)<input\b", new2)),
                "residual_answer_label": left.rstrip().endswith("Ответ:"),
                "has_ask": bool(ASK_RE.search(left)),
                "tail_after": tail_text(new2),
            })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Активных заданий: {len(rows)}")
    for key, title in (("criteria", "критерии оценивания"),
                       ("answer_form", "форма ответа sdamgia"),
                       ("empty_stem", "условие без текста")):
        items = report[key]
        print(f"\n{title}: {len(items)}")
        if key == "criteria":
            print(f"  маркер остался после среза: "
                  f"{[i['id'] for i in items if i['residual_marker']] or 'нет'}")
            print(f"  постановка задачи не найдена: "
                  f"{[i['id'] for i in items if not i['has_ask']] or 'нет'}")
            print(f"  срезано таблиц: {sum(i['tables_removed'] for i in items)}")
            tail = [i["id"] for i in items if has_content(i["after_criteria"])]
            print(f"  есть текст ПОСЛЕ критериев (слепой срез потерял бы): {tail or 'нет'}")
        elif key == "answer_form":
            print(f"  поле ввода осталось: "
                  f"{[i['id'] for i in items if i['residual_input']] or 'нет'}")
            print(f"  висящее «Ответ:» осталось: "
                  f"{[i['id'] for i in items if i['residual_answer_label']] or 'нет'}")
        else:
            print(f"  {[i['id'] for i in items]}")
    print(f"\nОтчёт: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    asyncio.run(main(ap.parse_args().out))
