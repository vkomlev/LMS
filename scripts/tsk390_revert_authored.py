# -*- coding: utf-8 -*-
"""tsk-390: откат ошибочной привязки файлов к авторским заданиям 4780-4789 и 5089-5095.

ЗАЧЕМ
Критерий отбора кандидатов считал «файл есть» только по ссылке `/api/v1/media/...`.
У авторских заданий курса файл был приложен ИНАЧЕ — прямой ссылкой на сайт автора
(`https://victor-komlev.ru/wp-content/uploads/...`), рабочей и правильной. Эти 17 заданий
не были сломаны, и привязывать им ничего не следовало:

  * 4780-4789 (ЕГЭ №3): у автора свой `3.zip` → `3.ods` с листами «Движение_товаров»,
    «Товар», «Магазин» и латинским «M10» — ровно как в тексте заданий. Привязанный
    `03.ods` с sdamgia — ДРУГОЙ файл: листы «Торговля»/«Товар»/«Магазин», «М10» кириллицей.
    Ученик увидел бы две разные ссылки и не сошёлся бы с условием. Это ошибка, её и чиним.
  * 5089-5095 (ЕГЭ №17): файл тот же самый по содержимому (сверено: 5000 чисел, −1000…1000),
    но ссылка на него теперь показывалась дважды. Лишнее — убираем.

Остальные 212 заданий партии внешней ссылки не имели (проверено по бэкапам) — их привязка
верна и здесь не трогается.

ЧТО ДЕЛАЕТ: возвращает `stem` из бэкапа и удаляет добавленные ключи `has_attached_file`
и `attached_file_paths` (у авторских заданий их до правки не было — метаданные ставит
только импорт ContentBackbone). Ничего больше не трогает.

Запуск:
  python scripts/tsk390_revert_authored.py --backup <backup_authored.json> [--apply]
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
from tsk369_collect import dsn  # noqa: E402


async def main(backup_path: Path, apply: bool) -> None:
    rows = json.loads(backup_path.read_text(encoding="utf-8"))
    want = {r["id"]: r["stem"] for r in rows}
    ids = sorted(want)
    print(f"К откату заданий: {len(ids)} -> {ids}")

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        cur = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem, "
            "       task_content->'attached_file_paths' AS paths "
            "FROM tasks WHERE id = ANY($1::int[]) AND is_active", ids)}
        missing = sorted(set(ids) - set(cur))
        if missing:
            raise RuntimeError(f"не нашёл активных заданий: {missing}")

        already = [i for i in ids if (cur[i]["stem"] or "") == want[i]]
        if already:
            print(f"  уже в исходном виде (пропускаю): {already}")
        todo = [i for i in ids if i not in already]
        if not todo:
            print("Нечего откатывать.")
            return

        async with conn.transaction():
            for tid in todo:
                await conn.execute(
                    "UPDATE tasks SET task_content = "
                    "  (jsonb_set(task_content, '{stem}', to_jsonb($2::text)) "
                    "   - 'has_attached_file' - 'attached_file_paths') "
                    "WHERE id = $1",
                    tid, want[tid],
                )
            check = {r["id"]: r for r in await conn.fetch(
                "SELECT id, task_content->>'stem' AS stem, "
                "       task_content ? 'attached_file_paths' AS has_paths, "
                "       task_content ? 'has_attached_file' AS has_flag "
                "FROM tasks WHERE id = ANY($1::int[])", todo)}
            bad = [t for t in todo
                   if check[t]["stem"] != want[t] or check[t]["has_paths"] or check[t]["has_flag"]]
            if bad:
                raise AssertionError(f"проверка внутри транзакции не прошла: {bad}")
            print(f"Внутри транзакции: откачено и проверено {len(todo)} заданий.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю транзакцию (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = {r["id"]: r for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem, "
            "       task_content ? 'attached_file_paths' AS has_paths "
            "FROM tasks WHERE id = ANY($1::int[])", todo)}
        problems = [t for t in todo if after[t]["stem"] != want[t] or after[t]["has_paths"]]
        print(f"  проверено построчно: {len(todo)}; расхождений: {len(problems)}")
        if problems:
            print(f"  ПРОБЛЕМНЫЕ: {problems}")
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
