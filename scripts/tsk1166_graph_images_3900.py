# -*- coding: utf-8 -*-
"""tsk-1166: рисунки графа вместо ASCII-схем в материале 3900 курса 1490.

ЗАЧЕМ
ASCII-схемы графа плохо читаются, а первая из них ещё и неверна: вертикаль «5.5»
вела из вершины 1 в 100, хотя в файле ребро 1 -> 7. Рисунки собраны из списка
рёбер файла и сверены программой (CreateCourses/courses/ege-23-grafy/exports/
build_visuals_tsk1166.py), лежат в WP-медиа 11432 и 11433.

ЧТО ДЕЛАЕТ
Заменяет в content.text материала 3900 ровно два <pre>-блока (весь граф и три
пути) на <figure class="cb-image">. Файл-пример (восемь строк) остаётся текстом —
его ученик копирует. Ставит content_provenance.source='manual_script' (tsk-760),
чтобы переиздание не вернуло ASCII. Исходник уже поправлен и в
tsk740_block23_materials.py (FIG_GRAF / FIG_PUTI).

Протокол /db-check: чтение -> план -> транзакция с блокировкой строки -> проверка.
Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk1166_graph_images_3900.py
  DBCHECK_OK=1 python scripts/tsk1166_graph_images_3900.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
MATERIAL_ID = 3900
COURSE_ID = 1490
START = "<p>Тот же файл, нарисованный как связи."
END_MARK = "&lt;-- кратчайший</code></pre>"


def _source():
    """Модуль-первоисточник блока 23: DSN и новые фрагменты берём оттуда."""
    spec = importlib.util.spec_from_file_location("src", HERE / "tsk740_block23_materials.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def new_fragment(src) -> str:
    """Фрагмент из поправленного первоисточника — от START до конца второго рисунка."""
    m1 = src.M1
    i = m1.index(START)
    j = m1.index(src.FIG_PUTI) + len(src.FIG_PUTI)
    return m1[i:j]


def replace(text: str, fragment: str) -> str:
    """Заменяет участок со старыми ASCII-схемами; ровно одно вхождение, иначе ошибка."""
    if text.count(START) != 1 or text.count(END_MARK) != 1:
        raise RuntimeError("участок со схемами не найден однозначно — материал правили, стоп")
    i = text.index(START)
    j = text.index(END_MARK) + len(END_MARK)
    old = text[i:j]
    if old.count("<pre>") != 2 or "──" not in old:
        raise RuntimeError("в участке не два ASCII-блока — стоп")
    return text[:i] + fragment + text[j:]


async def main(apply: bool) -> None:
    src = _source()
    fragment = new_fragment(src)
    conn = await asyncpg.connect(src._dsn())
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, course_id, content, content_provenance FROM materials "
                "WHERE id = $1 FOR UPDATE", MATERIAL_ID)
            if row is None or row["course_id"] != COURSE_ID:
                raise RuntimeError("материал 3900 не найден или не в курсе 1490")
            content = json.loads(row["content"])
            before = content["text"]
            if "ege23-primer-graf.png" in before:
                log.info("уже заменено — делать нечего")
                return
            after = replace(before, fragment)
            log.info("длина текста: %d -> %d; <pre>: %d -> %d; figure: %d -> %d",
                     len(before), len(after), before.count("<pre>"), after.count("<pre>"),
                     before.count("cb-image"), after.count("cb-image"))
            if not apply:
                log.info("вхолостую: запись не выполнена (добавь --apply)")
                return
            content["text"] = after
            prov = {"source": "manual_script", "fields": ["content"],
                    "edited_at": datetime.now(timezone.utc).isoformat(),
                    "edited_by": "script:tsk1166"}
            n = await conn.execute(
                "UPDATE materials SET content = $2::jsonb, content_provenance = $3::jsonb, "
                "updated_at = now() WHERE id = $1",
                MATERIAL_ID, json.dumps(content, ensure_ascii=False), json.dumps(prov, ensure_ascii=False))
            if n != "UPDATE 1":
                raise RuntimeError(f"ожидалась 1 строка, получено {n}")
            check = json.loads(await conn.fetchval("SELECT content FROM materials WHERE id=$1", MATERIAL_ID))["text"]
            if check != after or "──" in check.split(START)[1][:3000]:
                raise RuntimeError("проверка после записи не прошла — откат")
            log.info("записано и проверено: материал %d", MATERIAL_ID)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(main(ap.parse_args().apply))
