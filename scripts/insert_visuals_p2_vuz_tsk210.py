"""tsk-210 P2: вставка шести SVG-образов класса 3 в материалы вуз-курса 1248.

3083 Сохранить как (диалог), 3091 Вставка->Таблица, 3095 галерея объектов «Вставка»,
3105 формула =A1*B1 (было/стало), 3113 условное форматирование, 3120 таблица vs диаграмма.

Образы в прод-S3 CAS, src — /api/v1/media/<sha>.png. Прод-DSN из .mcp.json (не печатается).
Запуск: dry-run по умолчанию; DBCHECK_OK=1 python scripts/insert_visuals_p2_vuz_tsk210.py --apply
"""
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

BASE = "https://api.learn.victor-komlev.ru/api/v1/media"

MATS = [
    {"id": 3083, "sha": "692da84ee93890f3172dbd48564fd025eb808ee2e8764b24028d13e84cddcfe9",
     "alt": ("Диалог «Сохранение документа»: поле «Имя файла», список «Тип файла» с форматами "
             "(.docx, .doc, RTF, TXT, PDF) и кнопка «Сохранить»"),
     "anchor": "оставив исходник нетронутым.</p>"},
    {"id": 3091, "sha": "c8c8475c478e95a902e93e56317c6bd48e1d5eed6cba440aaaa2a9a08946ef2d",
     "alt": ("Word: на вкладке «Вставка» открыто меню «Таблица» с сеткой-квадратиками (выбрано "
             "3×2), рядом готовая таблица и появившиеся вкладки «Конструктор» и «Макет»"),
     "anchor": "хватает сетки-квадратиков.</p>"},
    {"id": 3095, "sha": "9e6639570322ead71a88362ad2e92a41a3e2e92547b0a3e3ba870408a3773f7a",
     "alt": ("Галерея объектов вкладки «Вставка» в Word: рисунок, фигуры, значки, SmartArt, "
             "диаграмма, надпись, WordArt, формула, снимок экрана — каждый с подписью"),
     "anchor": "вставит картинкой.</p>"},
    {"id": 3105, "sha": "fc5d05d57f25ee2a2d19c99759a7590542174245bd270c164a762fd5ca87ead3",
     "alt": ("Excel: формула =A1*B1 в ячейке C1; слева было A1=5 и C1=50, справа поменяли A1 на 7 "
             "— C1 сама стала 70"),
     "anchor": "а не сотней.</p>"},
    {"id": 3113, "sha": "25de6e15cf2a94f90db22fdcf0c9afaafccdd744fe2149bb0bb1b306bce256bc",
     "alt": ("Excel, условное форматирование: в столбце баллов ячейки ниже 60 подсвечены красным, "
             "выше 85 — зелёным"),
     "anchor": "ничего не раскрашивая вручную.</p>"},
    {"id": 3120, "sha": "33bf6d124251729586075bf053ef582d4f027ed0c7a40ba7951a8a482f9a99eb",
     "alt": ("Слева таблица с точными числами выручки филиалов, справа та же выручка столбиками "
             "диаграммы — соотношение видно с одного взгляда"),
     "anchor": "диаграмма делает их наглядными.</p>"},
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
                    "SELECT content->>'text' FROM materials WHERE id=$1", m["id"])
                if text is None:
                    raise RuntimeError(f"мат {m['id']}: не найден")
                if "<img" in text:
                    raise RuntimeError(f"мат {m['id']}: уже содержит <img> — прерываю")
                cnt = text.count(m["anchor"])
                if cnt != 1:
                    raise RuntimeError(f"мат {m['id']}: якорь встречается {cnt} раз (нужно 1)")
                fig = _figure(m["sha"], m["alt"])
                new_text = text.replace(m["anchor"], m["anchor"] + fig)
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    m["id"], new_text)
                check = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", m["id"])
                assert f"{m['sha']}.png" in check and "cb-image" in check, "вставка не подтвердилась"
                print(f"OK мат {m['id']}: +{len(fig)} симв., src /api/v1/media/{m['sha']}.png")
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
