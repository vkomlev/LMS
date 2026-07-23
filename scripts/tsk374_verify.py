# -*- coding: utf-8 -*-
"""tsk-374, шаг 3: независимая построчная проверка правки условий (read-only).

ЗАЧЕМ ОТДЕЛЬНЫМ СКРИПТОМ
Проверка внутри `tsk374_fix_stems.py` сравнивает базу с планом, который сама же и
записала: общая ошибка в сборке правки такой проверкой не ловится. Здесь ожидаемое
значение собирается заново — из БЭКАПА прежних условий, — и сверяется с тем, что
реально лежит в базе, по каждой строке отдельно, а не агрегатом (урок [[tsk-317]]).

Проверяется по каждому из 133 заданий:
  * условие в базе совпадает с ожидаемым посимвольно (md5 + длина);
  * мусора не осталось: ни маркера критериев, ни поля ввода, ни пути разработчика;
  * условие не опустело и в нём осталась постановка задачи;
  * то, что мусором не было, не пострадало: текст условия после правки является
    подпоследовательностью прежнего текста — то есть ничего не переписано, только
    удалено (для 2324 проверка не применяется, там текст добавлен осознанно).

Запуск: python scripts/tsk374_verify.py --backup <файл.json>
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from tsk374_fix_stems import FIX_2324_ID, FIX_3128_ID, fix_2324, fix_3128  # noqa: E402
from tsk374_scan import CRIT_RE, cut_answer_form, cut_criteria, has_content, strip_soft  # noqa: E402


def expected(tid: int, old: str, answer: str | None) -> str:
    """Каким условие должно стать — собирается заново из прежнего значения."""
    if tid == FIX_3128_ID:
        return fix_3128(old)
    if tid == FIX_2324_ID:
        return fix_2324(old, answer)
    new = old
    if CRIT_RE.search(strip_soft(strip_html(new))):
        new, _ = cut_criteria(new)
    if re.search(r"(?i)<input\b", new):
        new, _ = cut_answer_form(new)
    return new


def words(text: str) -> list[str]:
    """Слова текста без знаков препинания.

    Пунктуацию приходится снимать: удаление блока смыкает соседние теги, и на стыке
    `</p>` снятие разметки даёт лишнюю точку — слово «программу.» становится
    «программу..». Смысла это не меняет, а посимвольное сравнение слов рушит.
    """
    return re.findall(r"[^\W_]+", text, re.UNICODE)


def is_subsequence(small: str, big: str) -> bool:
    """Каждое слово нового текста встречается в старом в том же порядке."""
    it = iter(words(big))
    return all(w in it for w in words(small))


async def main(backup_path: Path) -> None:
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    ids = [r["id"] for r in backup]
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, is_active, task_content->>'stem' AS stem, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        leftover = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE is_active AND ("
            "     replace(task_content->>'stem', U&'\\00AD', '') "
            "       ~ '(Критерии оценивани|Максимальный балл)'"
            "  OR task_content->>'stem' ILIKE '%<input%'"
            "  OR task_content->>'stem' LIKE '%D:/Work%')")
    finally:
        await conn.close()

    bad: list[str] = []
    for rec in backup:
        tid, old = rec["id"], rec["stem"] or ""
        row = rows.get(tid)
        if row is None or not row["is_active"]:
            bad.append(f"{tid}: задания нет или оно неактивно")
            continue
        now = row["stem"] or ""
        want = expected(tid, old, row["answer"])
        if now != want:
            bad.append(f"{tid}: условие не совпало с ожидаемым "
                       f"(md5 {hashlib.md5(now.encode()).hexdigest()[:8]} "
                       f"!= {hashlib.md5(want.encode()).hexdigest()[:8]})")
            continue
        text = strip_soft(strip_html(now))
        if CRIT_RE.search(text):
            bad.append(f"{tid}: остались критерии оценивания")
        if re.search(r"(?i)<input\b", now):
            bad.append(f"{tid}: осталось поле ввода")
        if "D:/Work" in now:
            bad.append(f"{tid}: остался путь разработчика")
        if not has_content(text):
            bad.append(f"{tid}: условие осталось без текста")
        if not ASK_RE.search(text):
            bad.append(f"{tid}: в условии нет постановки задачи")
        if tid != FIX_2324_ID and not is_subsequence(text, strip_soft(strip_html(old))):
            bad.append(f"{tid}: текст условия не подпоследовательность прежнего — "
                       f"что-то переписано, а не удалено")

    print(f"Сверено заданий: {len(backup)} (построчно, ожидаемое пересобрано из бэкапа)")
    print(f"Расхождений: {len(bad)}")
    for b in bad:
        print(f"  {b}")
    print(f"Заданий с мусором во всей базе: {leftover}")
    if bad or leftover:
        sys.exit(1)
    print("\nПроверка пройдена.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", type=Path, required=True)
    asyncio.run(main(ap.parse_args().backup))
