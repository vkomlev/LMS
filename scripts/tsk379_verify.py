# -*- coding: utf-8 -*-
"""tsk-379, шаг 3: независимая построчная проверка среза шапки импорта (read-only).

ЗАЧЕМ ОТДЕЛЬНЫМ СКРИПТОМ
Проверка внутри `tsk379_fix_stems.py` сравнивает базу с планом, который сама же и
записала: общая ошибка в сборке правки такой проверкой не ловится. Здесь ожидаемое
значение собирается заново — из БЭКАПА прежних условий через `cut_header()`, — и
сверяется с тем, что реально лежит в базе, по каждой строке отдельно, а не агрегатом
(урок [[tsk-317]]).

Проверяется по каждому из 170 заданий:
  * условие в базе совпадает с ожидаемым посимвольно;
  * шапки не осталось: ни «Задание … Уровень X», ни «Решение задания N»;
  * условие не опустело и в нём осталась постановка задачи (ASK_RE);
  * то, что шапкой не было, не пострадало: новый текст — подпоследовательность
    прежнего (только удаление, ничего не переписано).

Запуск: python scripts/tsk379_verify.py --backup <файл.json>
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
from tsk379_scan import LEVEL_WORD, cut_header  # noqa: E402

HEADER_LEFT_RE = re.compile(
    r"Уровень\s+" + LEVEL_WORD + r"|Решение\s+задания\s+\d", re.IGNORECASE)


def words(text: str) -> list[str]:
    """Слова текста без знаков препинания (см. tsk374_verify.py)."""
    return re.findall(r"[^\W_]+", text, re.UNICODE)


def is_subsequence(small: str, big: str) -> bool:
    it = iter(words(big))
    return all(w in it for w in words(small))


async def main(backup_path: Path) -> None:
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    ids = [r["id"] for r in backup]
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, is_active, task_content->>'stem' AS stem "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        leftover = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE is_active AND ("
            "     task_content->>'stem' ~* 'Уровень\\s+"
            "(простой|лёгкий|легкий|средний|сложный)'"
            "  OR task_content->>'stem' ~* 'Решение\\s+задания\\s+\\d')"
            "  AND (split_part(external_uid, ':', 1) IN ('crylov', 'tg')"
            "       OR task_content->>'stem' ~* 'Решение\\s+задания\\s+\\d')")
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
        want, removed = cut_header(old)
        if not removed:
            bad.append(f"{tid}: в бэкапе не нашёл шапку заново — расхождение с планом")
            continue
        if now != want:
            bad.append(f"{tid}: условие не совпало с ожидаемым "
                       f"(md5 {hashlib.md5(now.encode()).hexdigest()[:8]} "
                       f"!= {hashlib.md5(want.encode()).hexdigest()[:8]})")
            continue
        text = strip_html(now)
        if HEADER_LEFT_RE.search(now):
            bad.append(f"{tid}: осталась шапка")
        if not text:
            bad.append(f"{tid}: условие осталось без текста")
        if not ASK_RE.search(text):
            bad.append(f"{tid}: в условии нет постановки задачи")
        if not is_subsequence(text, strip_html(old)):
            bad.append(f"{tid}: текст условия не подпоследовательность прежнего — "
                       f"что-то переписано, а не удалено")

    print(f"Сверено заданий: {len(backup)} (построчно, ожидаемое пересобрано из бэкапа)")
    print(f"Расхождений: {len(bad)}")
    for b in bad:
        print(f"  {b}")
    print(f"Заданий с шапкой во всей затронутой области (crylov/tg/«Решение задания»): {leftover}")
    if bad or leftover:
        sys.exit(1)
    print("\nПроверка пройдена.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", type=Path, required=True)
    asyncio.run(main(ap.parse_args().backup))
