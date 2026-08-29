# -*- coding: utf-8 -*-
"""tsk-731, хвост: разобрать Markdown-курсив в стемах 3470 и 3472.

ЧТО НЕ ТАК
Условия этих двух заданий пришли из API Яндекс Учебника Markdown-подобной строкой.
Разбор (`tsk558_yandex_wp_nav_19_21.py::_markdown_to_html`) знал только
`**жирный**`, поэтому одиночные звёздочки доехали до ученика как есть: на экране
видно «*S* камней; *1 ≤ S ≤ 33*» и «игрок имеет *выигрышную стратегию*».
Условие от этого целое и решаемое — это вид, а не содержание.

ПОЧЕМУ ТОЧЕЧНО, А НЕ РЕГУЛЯРКОЙ ПО БАЗЕ
Активных заданий, где есть одиночная `*…*`, — 41. Но почти во всех звёздочка
означает **умножение** («12 = 3 * 4», «символ «*»», «2 * 8 = 40») либо это обломок
табличной вёрстки sdamgia (`*</td><td>*`). Слепая замена на курсив сломала бы там
математику. Курсив Markdown — только у этих двух заданий, потому что только их
текст пришёл из Markdown Яндекса. Поэтому список замен задан поимённо.

Порядок замен важен: сперва длинный фрагмент `*1 ≤ S ≤ 33*`, потом короткий `*S*`
(иначе короткий откусил бы часть длинного). Пересечения проверены — но скрипт
всё равно сверяет результат: после правки в стеме не должно остаться ни одной
одиночной звёздочки.

Запуск: dry-run по умолчанию;
  python scripts/tsk731_fix_yandex_italics.py
  python scripts/tsk731_fix_yandex_italics.py --show
  DBCHECK_OK=1 python scripts/tsk731_fix_yandex_italics.py --apply
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

# id -> список (что заменить, на что). Порядок внутри списка соблюдается.
ITALICS: dict[int, list[tuple[str, str]]] = {
    3470: [
        ("*выигрышную стратегию*", "<em>выигрышную стратегию</em>"),
    ],
    3472: [
        ("*1 ≤ S ≤ 33*", "<em>1 ≤ S ≤ 33</em>"),
        ("*S*", "<em>S</em>"),
    ],
}

EXPECTED_COURSE = {3470: 147, 3472: 147}

# Одиночная звёздочка: не часть `**` ни слева, ни справа.
LONE_STAR = re.compile(r"(?<!\*)\*(?!\*)")


def _prod_dsn_from_mcp() -> str:
    """Строка подключения к прод-`learn` из `.mcp.json` (хост и пароль — не в коде)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://"):
            return arg
    raise RuntimeError("В .mcp.json нет строки подключения learn_prod_db.")


def _dsn() -> str:
    """Прод-DSN learn. Из окружения — только если это тот же хост и база, что в
    `.mcp.json`: локальный `.env` смотрит на dev, и правка ушла бы молча туда."""
    prod = _prod_dsn_from_mcp()
    p = urlparse(prod)
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    candidate = env.replace("postgresql+asyncpg://", "postgresql://")
    c = urlparse(candidate)
    return candidate if (c.hostname == p.hostname and c.path == p.path) else prod


def build_new_stem(task_id: int, stem: str) -> str:
    out = stem
    for src, dst in ITALICS[task_id]:
        out = out.replace(src, dst)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description="tsk-731: курсив в стемах 3470/3472")
    ap.add_argument("--apply", action="store_true", help="записать в прод-БД")
    ap.add_argument("--show", action="store_true", help="показать новый стем целиком")
    args = ap.parse_args()

    ids = sorted(EXPECTED_COURSE)
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, is_active, task_content FROM tasks WHERE id = ANY($1::int[])",
            ids,
        )
        by_id = {int(r["id"]): r for r in rows}
        if sorted(by_id) != ids:
            print(f"СТОП: не найдены задания {sorted(set(ids) - set(by_id))}.")
            return 2

        updates: list[tuple[int, str]] = []
        for task_id in ids:
            row = by_id[task_id]
            if row["course_id"] != EXPECTED_COURSE[task_id]:
                print(f"СТОП: {task_id} не в курсе {EXPECTED_COURSE[task_id]}.")
                return 2
            if not row["is_active"]:
                print(f"СТОП: {task_id} неактивно.")
                return 2

            content = json.loads(row["task_content"])
            old_stem = content.get("stem") or ""
            new_stem = build_new_stem(task_id, old_stem)
            if new_stem == old_stem:
                print(f"  {task_id}: курсив уже разобран, пропускаю")
                continue

            # Замена меняет только оформление: текст без тегов обязан совпасть.
            def _plain(s: str) -> str:
                return re.sub(r"<[a-zA-Z/][^>]*>", "", s).replace("*", "")

            if _plain(old_stem) != _plain(new_stem):
                print(f"СТОП: {task_id} — замена изменила сам текст, а не оформление.")
                return 2
            # Ни одной одиночной звёздочки остаться не должно.
            left = LONE_STAR.findall(new_stem)
            if left:
                print(f"СТОП: {task_id} — осталось одиночных звёздочек: {len(left)}.")
                return 2

            new_content = dict(content)
            new_content["stem"] = new_stem
            print(f"\n  {task_id}: заменено пар — "
                  f"{sum(old_stem.count(s) for s, _ in ITALICS[task_id])}")
            if args.show:
                print("    новый стем:\n" + new_stem)
            updates.append((task_id, json.dumps(new_content, ensure_ascii=False)))

        if not args.apply:
            print(f"\nDry-run: записи не было. К обновлению {len(updates)} заданий.")
            return 0

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.audit_actor', 'tsk-731', true)")
            for task_id, content_json in updates:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb WHERE id = $1",
                    task_id, content_json,
                )
        print(f"\nОбновлено заданий: {len(updates)}")

        print("\n=== ПОСЛЕ ===")
        check = await conn.fetch(
            "SELECT id, length(task_content->>'stem') AS len, "
            "(task_content->>'stem' LIKE '%<em>%') AS est_kursiv, "
            "(task_content->>'stem' LIKE '%<li>%') AS est_spisok "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        ok = True
        for r in check:
            good = r["est_kursiv"] and r["est_spisok"]
            ok = ok and good
            print(f"  {r['id']}: длина={r['len']} курсив={r['est_kursiv']} "
                  f"список={r['est_spisok']}")
        return 0 if ok else 3
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
