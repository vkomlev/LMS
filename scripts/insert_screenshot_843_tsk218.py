"""tsk-218: вставка скриншота «Как запустить» в LMS-материал 843 (Python-подростки).

Материал 843 «Где писать код: онлайн-песочница» словами описывает online-python.com,
но картинки нет. Вставляем детерминированный скриншот песочницы (кнопка Run + окно вывода),
захостенный в прод-S3 CAS, по канонической конвенции курса <figure class="cb-image">.

URL — канонический LMS media endpoint (/api/v1/media/<sha>.png -> 307 -> S3): переживает
смену бакета. Проверен: 307->200 image/png.

Правится только LMS-прод. Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

MAT_ID = 843
SHA = "728406f275810d33deb3d924386d654d230781f67e39d2e45a211d30b6fbe629"
IMG_URL = f"https://api.learn.victor-komlev.ru/api/v1/media/{SHA}.png"
ALT = (
    "Онлайн-песочница online-python.com: вверху редактор с кодом (две строки print), "
    "справа зелёная кнопка Run, внизу тёмное окно вывода с результатом «Привет!» и "
    "«Меня зовут Алекс»"
)
FIGURE = f'\n<figure class="cb-image"><img src="{IMG_URL}" alt="{ALT}"></figure>'

ANCHOR = (
    '<a href="https://replit.com" target="_blank" rel="noopener">replit.com</a>.</p>'
)


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            text = await conn.fetchval(
                "SELECT content->>'text' FROM materials WHERE id=$1", MAT_ID
            )
            if text is None:
                raise RuntimeError(f"мат {MAT_ID}: не найден")
            if "<img" in text:
                raise RuntimeError(f"мат {MAT_ID}: уже содержит <img> — прерываю (не дублировать)")
            cnt = text.count(ANCHOR)
            if cnt != 1:
                raise RuntimeError(f"мат {MAT_ID}: якорь встречается {cnt} раз (нужно 1)")
            new_text = text.replace(ANCHOR, ANCHOR + FIGURE)
            await conn.execute(
                "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                MAT_ID, new_text,
            )
            check = await conn.fetchval(
                "SELECT content->>'text' FROM materials WHERE id=$1", MAT_ID
            )
            assert IMG_URL in check and 'cb-image' in check, "вставка не подтвердилась"
            print(f"OK мат {MAT_ID}: image-блок вставлен (+{len(FIGURE)} симв.)")
            print(f"  src={IMG_URL}")
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
