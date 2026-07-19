"""tsk-210 иллюстрации железа (ImgGen, класс 2): вставка в материалы вуз-курса 1248.

3020 материнская плата (подписи частей), 3023+3033 HDD/SSD/ОЗУ (один образ на два),
3027 устройства ввода, 3127 сетевое оборудование ЛВС.

Образы сгенерил оператор (ImgGen), захостены в прод-S3 CAS. src — /api/v1/media/<sha>.png.
Прод-DSN из .mcp.json (не печатается). dry-run по умолчанию.
Запуск: DBCHECK_OK=1 python scripts/insert_illustrations_vuz_tsk210.py --apply
"""
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

BASE = "https://api.learn.victor-komlev.ru/api/v1/media"
SHA_HDD = "3001876677fefda4ee10e9bda81700a0acecc858cd99a41b2941b55125a2f24c"

MATS = [
    {"id": 3020, "sha": "c4913babf7695a63518810710937e04046042c3ef74d4955a8006691f4d4acb8",
     "alt": ("Материнская плата, вид сверху, с подписанными частями: сокет процессора, "
             "слоты оперативной памяти, слот видеокарты (PCIe), разъёмы питания"),
     "anchor": "не смогут даже поздороваться друг с другом.</p>"},
    {"id": 3023, "sha": SHA_HDD,
     "alt": ("Три носителя рядом: жёсткий диск (HDD) с металлическими блинами и головкой, "
             "твердотельный SSD из микросхем, планка оперативной памяти (ОЗУ)"),
     "anchor": "открываются программы.</p>"},
    {"id": 3033, "sha": SHA_HDD,
     "alt": ("Жёсткий диск (HDD) с металлическими блинами и головкой рядом с твердотельным "
             "SSD из микросхем и планкой оперативной памяти"),
     "anchor": "подвести в любой момент.</p>"},
    {"id": 3027, "sha": "db7327bed19d0ef97548482bba2d516e0533c9d212a891ef7016bc325f497b90",
     "alt": ("Устройства ввода с подписями: клавиатура, мышь, сканер, микрофон, веб-камера, "
             "графический планшет"),
     "anchor": "все они устройства ввода.</p>"},
    {"id": 3127, "sha": "d2f769afab9f4bb30bcf538c070e857bd191cc6cffc673c8271a4c1023f2a851",
     "alt": ("Сетевое оборудование с подписями: сетевая карта, кабель (витая пара, RJ-45), "
             "коммутатор, маршрутизатор (роутер), точка доступа Wi-Fi"),
     "anchor": "по Wi-Fi без проводов.</p>"},
]


def _dsn() -> str:
    cfg = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["learn_prod_db"]["args"]
    return next(a for a in args if a.startswith("postgres")).replace("postgresql+asyncpg://", "postgresql://")


def _figure(sha: str, alt: str) -> str:
    return f'\n<figure class="cb-image"><img src="{BASE}/{sha}.png" alt="{alt}"></figure>'


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for m in MATS:
                text = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", m["id"])
                if text is None:
                    raise RuntimeError(f"мат {m['id']}: не найден")
                if "<img" in text:
                    raise RuntimeError(f"мат {m['id']}: уже содержит <img> — прерываю")
                if text.count(m["anchor"]) != 1:
                    raise RuntimeError(f"мат {m['id']}: якорь != 1 ({text.count(m['anchor'])})")
                fig = _figure(m["sha"], m["alt"])
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    m["id"], text.replace(m["anchor"], m["anchor"] + fig))
                chk = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", m["id"])
                assert f"{m['sha']}.png" in chk and "cb-image" in chk
                print(f"OK мат {m['id']}: +{len(fig)} симв., src /api/v1/media/{m['sha']}.png")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю")
        print("\nЗАПИСАНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
