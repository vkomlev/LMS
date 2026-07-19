"""tsk-210 P1: вставка трёх учебных визуалов в материалы вуз-курса 1248.

Закрывает часть визуального голода (аудит 2026-07-15):
  3048 «Что происходит при увеличении» — растр рассыпается ↔ вектор гладкий (SVG-образ);
  3082 «Лента, её вкладки и группы»    — лента Word: вкладки + группы + назначение;
  3101 «Книга, лист, ячейка, адрес…»   — сетка Excel: адрес C7, диапазоны A1:A10 и B2:D5.

Образы захостены в прод-S3 CAS, src — канонический LMS media endpoint (/api/v1/media/<sha>.png
-> 307 -> S3, переживает смену бакета). Конвенция курса — <figure class="cb-image">.

Прод-DSN читается из D:\\Work\\LMS\\.mcp.json (learn_prod_db) и НЕ печатается.
Запуск: dry-run по умолчанию; DBCHECK_OK=1 python scripts/insert_visuals_p1_vuz_tsk210.py --apply
"""
import asyncio
import json
import re
import sys
from pathlib import Path

import asyncpg

BASE = "https://api.learn.victor-komlev.ru/api/v1/media"

MATS = [
    {
        "id": 3048,
        "sha": "d956e774bbc207990c738676c88eb4725596f909c7ccb0716a7d1d03741fc873",
        "alt": ("Одна картинка увеличена в 8 раз: слева растровая рассыпалась на "
                "квадраты-пиксели, справа векторная осталась гладким кругом"),
        "anchor": "хоть на визитке, хоть на стене дома.</p>",
    },
    {
        "id": 3082,
        "sha": "91b34bd78cc2f695b3f82f393ea46ee93faf3bbcfaa7d69ee4d64ad6cd1d9747",
        "alt": ("Лента Word: верхняя строка вкладок (Файл, Главная, Вставка и другие), "
                "а под открытой вкладкой «Главная» её группы — Буфер обмена, Шрифт, Абзац, Стили"),
        "anchor": "как показать документ на экране).</p>",
    },
    {
        "id": 3101,
        "sha": "5b9fe551bc9df30ed7a088b7dded6ca3c20d8a46969142a5a9e2a9fcec3c8d56",
        "alt": ("Сетка Excel: адрес ячейки C7 в поле имени, диапазон A1:A10 — подсвеченный "
                "столбец, диапазон B2:D5 — подсвеченный прямоугольник"),
        "anchor": "а сразу весь прямоугольник.</p>",
    },
]


def _dsn() -> str:
    cfg = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["learn_prod_db"]["args"]
    dsn = next(a for a in args if a.startswith("postgres"))
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def _figure(sha: str, alt: str) -> str:
    return f'\n<figure class="cb-image"><img src="{BASE}/{sha}.png" alt="{alt}"></figure>'


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for m in MATS:
                text = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", m["id"]
                )
                if text is None:
                    raise RuntimeError(f"мат {m['id']}: не найден")
                if "<img" in text:
                    raise RuntimeError(f"мат {m['id']}: уже содержит <img> — прерываю (не дублировать)")
                cnt = text.count(m["anchor"])
                if cnt != 1:
                    raise RuntimeError(f"мат {m['id']}: якорь встречается {cnt} раз (нужно 1)")
                fig = _figure(m["sha"], m["alt"])
                new_text = text.replace(m["anchor"], m["anchor"] + fig)
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    m["id"], new_text,
                )
                check = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", m["id"]
                )
                assert f"{m['sha']}.png" in check and "cb-image" in check, "вставка не подтвердилась"
                print(f"OK мат {m['id']}: image-блок вставлен (+{len(fig)} симв.), src /api/v1/media/{m['sha']}.png")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
