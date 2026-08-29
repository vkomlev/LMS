# -*- coding: utf-8 -*-
"""tsk-731, хвост: вернуть заданию 3288 список условий.

ЧТО НЕ ТАК
Три условия задания 9 (курс 160, `tg:ege:580`) слиплись в один абзац через `<br>`
и БЕЗ маркеров, а следом, тем же абзацем, приклеилась фраза про формат ответа:

    четыре числа строки можно разбить на две пары чисел с равными суммами<br>
    максимальное число строки меньше суммы трёх оставшихся чисел<br>
    сумма чисел в строке чётна<br>В ответе запишите только число.

Ученик видит сплошной текст: где кончается третье условие и начинается указание,
что писать в ответ, — непонятно.

СОДЕРЖАНИЕ НЕ ПОТЕРЯНО — в отличие от 3470/3472 здесь пропали только разделители.
Поэтому правка чисто оформительская, и сторож ниже это доказывает: текст без тегов
до и после обязан совпасть слово в слово.

ГРАНИЦЫ ПУНКТОВ СВЕРЕНЫ С ИСТОЧНИКОМ
ТГ-пост @cyberguru_ege №580 (партия `tg:ege`) держит те же три условия, разделённые
переводами строк ровно в этих местах, и отдельной строкой — «В ответе запишите
только число». То есть деление не додумано, а взято из источника. UUID задачи
Яндекса пост не несёт (в отличие от №956), первоисточник по ID не достать — но он
и не нужен: восстанавливать нечего, только разметить.

ПОЧЕМУ ТОЛЬКО ЭТО ЗАДАНИЕ
Активных заданий, где после «условия:» идёт абзац с `<br>` и нет списка, — два:
3288 и 3156. У 3156 каждый пункт начинается с тире, поэтому список читается и без
разметки; трогать его не за что. Остальные два задания класса неактивны.

Запуск: dry-run по умолчанию;
  python scripts/tsk731_fix_3288_list.py
  python scripts/tsk731_fix_3288_list.py --show
  DBCHECK_OK=1 python scripts/tsk731_fix_3288_list.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

TASK_ID = 3288
EXPECTED_COURSE = 160

OLD_BLOCK = (
    "<p>четыре числа строки можно разбить на две пары чисел с равными суммами<br>"
    "максимальное число строки меньше суммы трёх оставшихся чисел<br>"
    "сумма чисел в строке чётна<br>"
    "В ответе запишите только число.</p>"
)
NEW_BLOCK = (
    "<ul>"
    "<li>четыре числа строки можно разбить на две пары чисел с равными суммами</li>"
    "<li>максимальное число строки меньше суммы трёх оставшихся чисел</li>"
    "<li>сумма чисел в строке чётна</li>"
    "</ul>"
    "<p>В ответе запишите только число.</p>"
)


def _prod_dsn_from_mcp() -> str:
    """Строка подключения к прод-`learn` из `.mcp.json` (хост и пароль — не в коде)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg
    raise RuntimeError("В .mcp.json нет строки подключения learn_prod_db.")


def _dsn() -> str:
    """Прод-DSN learn. Из окружения — только если тот же хост и база, что в
    `.mcp.json`: локальный `.env` смотрит на dev, и правка ушла бы молча туда."""
    prod = _prod_dsn_from_mcp()
    p = urlparse(prod)
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    candidate = env.replace("postgresql+asyncpg://", "postgresql://")
    c = urlparse(candidate)
    return candidate if (c.hostname == p.hostname and c.path == p.path) else prod


def _plain(s: str) -> str:
    """Текст без разметки. Теги превращаются в ПРОБЕЛ, а не в пустоту: иначе
    `суммами<br>максимальное` склеилось бы в одно слово и сравнение соврало бы."""
    return re.sub(r"\s+", " ", re.sub(r"<[a-zA-Z/][^>]*>", " ", s)).strip()


async def main() -> int:
    ap = argparse.ArgumentParser(description="tsk-731: список условий у 3288")
    ap.add_argument("--apply", action="store_true", help="записать в прод-БД")
    ap.add_argument("--show", action="store_true", help="показать новый стем целиком")
    args = ap.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT id, course_id, is_active, task_content FROM tasks WHERE id = $1",
            TASK_ID,
        )
        if row is None:
            print(f"СТОП: задание {TASK_ID} не найдено.")
            return 2
        if row["course_id"] != EXPECTED_COURSE:
            print(f"СТОП: {TASK_ID} не в курсе {EXPECTED_COURSE} (сейчас {row['course_id']}).")
            return 2
        if not row["is_active"]:
            print(f"СТОП: {TASK_ID} неактивно.")
            return 2

        content = json.loads(row["task_content"])
        old_stem = content.get("stem") or ""

        if NEW_BLOCK in old_stem:
            print(f"  {TASK_ID}: список уже размечен, пропускаю")
            print("\nDry-run: записи не было. К обновлению 0 заданий.")
            return 0

        found = old_stem.count(OLD_BLOCK)
        if found != 1:
            print(f"СТОП: блок условий найден {found} раз (нужен ровно 1) — "
                  "стем изменился, правку вслепую не делаю.")
            return 2

        new_stem = old_stem.replace(OLD_BLOCK, NEW_BLOCK)

        # Правка меняет ТОЛЬКО оформление: текст без разметки обязан совпасть.
        if _plain(old_stem) != _plain(new_stem):
            print(f"СТОП: {TASK_ID} — замена изменила сам текст, а не оформление.")
            return 2

        content = dict(content)
        content["stem"] = new_stem
        print(f"\n  {TASK_ID}: стем {len(old_stem)} → {len(new_stem)} символов; "
              "три условия стали списком, фраза про формат ответа отделена")
        if args.show:
            print("    новый стем:\n" + new_stem)

        if not args.apply:
            print("\nDry-run: записи не было. К обновлению 1 задание.")
            return 0

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-731', true)")
            await conn.execute(
                "UPDATE tasks SET task_content = $2::jsonb WHERE id = $1",
                TASK_ID, json.dumps(content, ensure_ascii=False),
            )
        print("\nОбновлено заданий: 1")

        print("\n=== ПОСЛЕ ===")
        check = await conn.fetchrow(
            "SELECT length(task_content->>'stem') AS len, "
            "(task_content->>'stem' LIKE '%<li>%') AS est_spisok, "
            "(task_content->>'stem' LIKE '%чётна<br>В ответе%') AS slipsheesya, "
            "solution_rules->'short_answer'->'accepted_answers'->0->>'value' AS etalon "
            "FROM tasks WHERE id = $1",
            TASK_ID,
        )
        print(f"  {TASK_ID}: длина={check['len']} список={check['est_spisok']} "
              f"слипшееся={check['slipsheesya']} эталон={check['etalon']!r}")
        return 0 if (check["est_spisok"] and not check["slipsheesya"]) else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
