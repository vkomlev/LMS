# -*- coding: utf-8 -*-
"""tsk-740, партия 10: правка текстов уроков блока 23 по находкам ревью.

ЗАЧЕМ
Повторный прогон ревью 02.09 показал, что P0 и три P1 закрыты, но остались
находки по понятности (К7), которые правятся только текстом:

1. Урок «Дейкстра»: подпись обещала «жирным отмечена вершина», а в моноширинном
   блоке жирного нет и быть не может — ученик ищет несуществующее выделение.
2. Уроки «Дейкстра» и «Беллман-Форд»: таблицы показывают только вершины,
   достижимые из старта, а вершины 6 и 12 того же примера пропущены без оговорки.
3. Урок «Словарь смежности»: фраза «вершины 12 и 100 в ключах либо есть, либо
   нет» стояла рядом с конкретным словарём, где у этих двух вершин разная судьба.
4. Там же: цель обещала «четыре строки», а в блоке кода их восемь.

ПОЧЕМУ ОТДЕЛЬНЫЙ СКРИПТ, А НЕ ПЕРЕЗАПУСК ПАРТИИ 6
Скрипт партии 6 создавал структуру «глава + два листа» и проверяет состав узлов
на точное совпадение. С тех пор структура ушла вперёд: появился третий лист,
добавились девять заданий, два урока переехали. Перезапуск партии 6 падает на
своих же проверках, и подгонять их под каждое изменение — плодить связность.
Поэтому тексты уроков правятся здесь: скрипт ничего не двигает и не создаёт,
только обновляет `content` по `external_uid` материала.

Запуск: вхолостую по умолчанию;
  DBCHECK_OK=1 python scripts/tsk740_block23_lesson_text.py
  DBCHECK_OK=1 python scripts/tsk740_block23_lesson_text.py --apply
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
sys.path.insert(0, str(project_root / "scripts"))

# Тексты уроков живут в модуле партии 6 — здесь они не дублируются, иначе две
# копии разъедутся при следующей правке.
from tsk740_block23_materials import MATERIALY  # noqa: E402


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        plan = []
        for uid, _gde, _poryadok, zagolovok, tekst in MATERIALY:
            tekushchij = await conn.fetchrow(
                "SELECT id, course_id, title, content->>'text' AS telo "
                "FROM materials WHERE external_uid = $1", uid,
            )
            if tekushchij is None:
                raise RuntimeError(f"Материал {uid} не найден — сперва партии 6-7.")
            if (tekushchij["telo"] or "") == tekst and tekushchij["title"] == zagolovok:
                continue
            plan.append({"uid": uid, "id": tekushchij["id"], "kurs": tekushchij["course_id"],
                         "zagolovok": zagolovok, "tekst": tekst,
                         "bylo": len(tekushchij["telo"] or ""), "stanet": len(tekst)})

        print("=== ПЛАН ===")
        if not plan:
            print("Все тексты уроков уже совпадают с исходником — правок нет.")
            return
        for p in plan:
            print(f"  {p['uid']} (материал {p['id']}, курс {p['kurs']}): "
                  f"{p['bylo']} -> {p['stanet']} символов")
        print(f"Уроков к обновлению: {len(plan)} из {len(MATERIALY)}")

        if not apply:
            print("\nВхолостую. Записи не было.")
            return

        async with conn.transaction():
            for p in plan:
                await conn.execute(
                    "UPDATE materials SET content = $2::jsonb, title = $3 WHERE id = $1",
                    p["id"], json.dumps({"text": p["tekst"], "format": "html"},
                                        ensure_ascii=False), p["zagolovok"],
                )

            # Верификация до коммита: тексты записались, узлы не поехали.
            for p in plan:
                r = await conn.fetchrow(
                    "SELECT course_id, content->>'text' AS telo FROM materials WHERE id = $1",
                    p["id"],
                )
                if r["telo"] != p["tekst"]:
                    raise RuntimeError(f"{p['uid']}: текст не записался.")
                if r["course_id"] != p["kurs"]:
                    raise RuntimeError(f"{p['uid']}: материал сменил узел — недопустимо.")
            # Находки, которые правка обязана закрыть.
            ostalos = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE external_uid LIKE 'lms:tsk740:m23:%' "
                "AND (content::text ILIKE '%жирным отмечена%' "
                "     OR content::text ILIKE '%либо есть, либо нет%' "
                "     OR content::text ILIKE '%написать четыре строки%')"
            )
            if ostalos:
                raise RuntimeError(f"В {ostalos} уроках остались исправляемые формулировки.")
            print(f"\nПроверено до коммита: {len(plan)} уроков обновлено, "
                  "узлы не менялись, старые формулировки не остались.")

        print("Готово.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tsk-740 партия 10: тексты уроков блока 23")
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
