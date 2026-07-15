"""tsk-218: вставка скриншотов turtle-графики в LMS 1228/1229/1232/1233 (Python-подростки).

По аудиту визуального голода (К7): 4 графических урока разделов 10-11 описывают
нарисованный результат ТЕКСТОМ в скобках «(на экране: ...)» вместо изображения. Фикс
аддитивный: рядом с текстом-описанием ставим детерминированный скриншот результата
(симуляция черепашки → SVG → S3 CAS), ASCII-логику кода не трогаем.

Figure ставится сразу после блока вывода «(на экране: ...)</code></pre>», перед cb-ascii:
код → вывод-описание → ОБРАЗ результата → ASCII-логика → callout.

Прод-DSN: LMS/.env=localhost(dev); прод через env DATABASE_URL из LMS/.mcp.json.
Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

BASE = "https://api.learn.victor-komlev.ru/api/v1/media"

# (mat_id, sha, alt, anchor) — figure ставится сразу после anchor (ровно 1 вхождение)
ITEMS = [
    (
        1228,
        "8953aa8eaf37927d5cf36d8169bb74c02f20dc4e02be0a56f836f1c1c134ab48",
        "Окно turtle: синий угол из двух линий под прямым углом — черепашка прошла вперёд, "
        "повернула на 90° и снова прошла вперёд.",
        "(на экране: синий угол — две линии под прямым углом)</code></pre>",
    ),
    (
        1229,
        "bf5f95c57310c91a2c9d9d88d7ab4bf92ba11e6e2097c43ca3fe1283439ff5cf",
        "Окно turtle: зелёный треугольник (поворот 360/3=120°), а рядом квадрат, пятиугольник "
        "и 12-угольник — чем больше сторон, тем фигура ближе к кругу.",
        "(на экране: зелёный треугольник)</code></pre>",
    ),
    (
        1232,
        "72a189b9cf2178a5d5477ed3f41c345cf38f3dfbdfe04295f0a2dbec791047a1",
        "Окно turtle: узор-спирограф — 36 синих квадратов, повёрнутых веером на 10° каждый, "
        "сложились в круговой цветок.",
        "(на экране: узор-цветок из 36 повёрнутых квадратов)</code></pre>",
    ),
    (
        1233,
        "02ef38b65256cf7d3351afc9a7bee89a19cc0ee8d61522d6b90db4310fec0aaf",
        "Окно turtle: чёрное небо с 50 разноцветными точками-звёздами (белые, жёлтые, голубые) "
        "в случайных местах — один из вариантов результата.",
        "(на экране: 50 разноцветных звёзд на чёрном небе)</code></pre>",
    ),
]


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _figure(sha: str, alt: str) -> str:
    url = f"{BASE}/{sha}.png"
    return f'\n<figure class="cb-image"><img src="{url}" alt="{alt}"></figure>'


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for mat_id, sha, alt, anchor in ITEMS:
                text = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", mat_id
                )
                if text is None:
                    raise RuntimeError(f"мат {mat_id}: не найден")
                if "<img" in text:
                    raise RuntimeError(f"мат {mat_id}: уже содержит <img> — прерываю (не дублировать)")
                cnt = text.count(anchor)
                if cnt != 1:
                    raise RuntimeError(f"мат {mat_id}: якорь встречается {cnt} раз (нужно 1)")
                figure = _figure(sha, alt)
                new_text = text.replace(anchor, anchor + figure)
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    mat_id, new_text,
                )
                check = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", mat_id
                )
                url = f"{BASE}/{sha}.png"
                assert url in check and "cb-image" in check, f"мат {mat_id}: вставка не подтвердилась"
                print(f"OK мат {mat_id}: image-блок вставлен (+{len(figure)} симв.)  src={url}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО (4 материала).")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
