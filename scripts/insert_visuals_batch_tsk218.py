"""tsk-218: батч-вставка учебных визуалов в LMS-материалы (Python-подростки).

Продолжение флагмана insert_screenshot_843: три образа под «визуальный голод»
(два проекта-результата + образ-накопитель). Каждый визуал захостен в прод-S3 CAS,
URL — канонический LMS media endpoint (/api/v1/media/<sha>.png -> 307 -> S3).

Для каждого материала: assert уникальный якорь (ровно 1), защита от повторного <img>,
вставка figure по конвенции курса <figure class="cb-image">, verify в транзакции.
Все три — в ОДНОЙ транзакции: либо все, либо ни одного.

Правится только LMS-прод. Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

BASE = "https://api.learn.victor-komlev.ru/api/v1/media"

# (mat_id, sha, alt, anchor) — anchor должен встречаться РОВНО 1 раз; figure ставится сразу после него.
ITEMS = [
    (
        1238,
        "e657a38941ef958a3f1565c9da5a66b178189e8a3b5a1c99c1aeab535f47ebc3",
        "Окно turtle: узор-цветок из 12 квадратов, повёрнутых вокруг одной общей точки — "
        "именно это рисует код проекта; рядом сам код (цикл for внутри for).",
        "окно не нужно</code></pre>",
    ),
    (
        1237,
        "b6a9ff4b944fe67b4d8d44b10b753055258fd9c8fa382e154390b1d6093291c3",
        "Тёмное окно программы: квест печатает «Ты здесь: Тёмный коридор», «Зал с сундуком», "
        "«Выход» и спрашивает «Идти вперёд (в) или назад (н)?»; игрок отвечает буквой «в», "
        "в конце появляется «Ты дошёл до выхода!».",
        "загадки.&quot;)</code></pre>",
    ),
    (
        997,
        "7a88ec0f35cb93b08c07db3338edbea74a978593c43ef27d9a63afa51e8b8010",
        "Четыре копилки в ряд: сначала пустая (ochki = 0), затем с одной, двумя и тремя "
        "монетами (ochki = 1, 2, 3) — счётчик прибавляет по 1 на каждом круге цикла.",
        "Очков: 3</code></pre>",
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
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО (3 материала).")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
