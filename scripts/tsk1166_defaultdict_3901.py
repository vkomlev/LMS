# -*- coding: utf-8 -*-
"""tsk-1166: урок 3901 «Граф в памяти» — сначала обычный словарь, потом defaultdict.

ЗАЧЕМ
Урок подавал defaultdict как знакомый («вспомни»), а ученики его не проходили.
Теперь: чтение файла обычным словарём -> новый тип defaultdict, его особенность
(обращение к отсутствующему ключу создаёт его) -> упрощённое чтение -> почему
дальше в курсе defaultdict и как обойтись без него (граф.get(вершина, [])).

Текст берётся из первоисточника tsk740_block23_materials.py (M2). Запись только
если на проде лежит в точности прежняя версия M2 из git (HEAD) — иначе материал
кто-то правил, стоп. Пометка ручной правки — manual_script (tsk-760).

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk1166_defaultdict_3901.py
  DBCHECK_OK=1 python scripts/tsk1166_defaultdict_3901.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SRC = "scripts/tsk740_block23_materials.py"
MATERIAL_ID = 3901
COURSE_ID = 1494


def _load(code: str, name: str):
    """Исполняет код модуля-первоисточника без запуска main."""
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = str(HERE / "tsk740_block23_materials.py")
    exec(compile(code, name, "exec"), mod.__dict__)
    return mod


def old_and_new() -> tuple[str, object]:
    """Прежний M2 из git HEAD и модуль с новым M2 из рабочей копии."""
    head = subprocess.run(["git", "show", f"HEAD:{SRC}"], cwd=HERE.parent,
                          capture_output=True, check=True).stdout.decode("utf-8")
    new = _load((HERE / "tsk740_block23_materials.py").read_text(encoding="utf-8"), "new")
    return _load(head, "old").M2, new


async def main(apply: bool) -> None:
    old, src = old_and_new()
    new = src.M2
    if old == new:
        raise RuntimeError("M2 в рабочей копии не отличается от HEAD — нечего писать")
    conn = await asyncpg.connect(src._dsn())
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT course_id, content FROM materials WHERE id = $1 FOR UPDATE", MATERIAL_ID)
            if row is None or row["course_id"] != COURSE_ID:
                raise RuntimeError("материал 3901 не найден или не в курсе 1494")
            content = json.loads(row["content"])
            if content["text"] == new:
                log.info("уже записано — делать нечего")
                return
            if content["text"] != old:
                raise RuntimeError("на проде не прежняя версия текста — материал правили, стоп")
            log.info("длина текста: %d -> %d; упоминаний defaultdict: %d -> %d",
                     len(old), len(new), old.count("defaultdict"), new.count("defaultdict"))
            if not apply:
                log.info("вхолостую: запись не выполнена (добавь --apply)")
                return
            content["text"] = new
            prov = {"source": "manual_script", "fields": ["content"],
                    "edited_at": datetime.now(timezone.utc).isoformat(),
                    "edited_by": "script:tsk1166"}
            n = await conn.execute(
                "UPDATE materials SET content = $2::jsonb, content_provenance = $3::jsonb, "
                "updated_at = now() WHERE id = $1",
                MATERIAL_ID, json.dumps(content, ensure_ascii=False), json.dumps(prov, ensure_ascii=False))
            if n != "UPDATE 1":
                raise RuntimeError(f"ожидалась 1 строка, получено {n}")
            check = json.loads(await conn.fetchval(
                "SELECT content FROM materials WHERE id = $1", MATERIAL_ID))["text"]
            if check != new:
                raise RuntimeError("проверка после записи не прошла — откат")
            log.info("записано и проверено: материал %d", MATERIAL_ID)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(main(ap.parse_args().apply))
