"""tsk-218: вставка ASCII-схем (cb-ascii) в LMS-материалы 957/961/924 (Python-подростки).

По решению оператора: три «пустых» материала (сравнения / count-find-replace / регистр) —
ASCII-территория (точные схемы, не картинка). Схемы собраны и проверены глазами на рендере
(build_ascii_957_961_924.py + Edge). Здесь — только вставка готовых <pre class="cb-ascii">
после блока вывода, по конвенции курса (как в 918/921/922).

Готовые <pre> берутся из scratchpad/ascii_pres.json (экранирование сущностей уже сделано).
assert уникальный якорь (1), защита от повторного cb-ascii, verify в транзакции.
Все три — в ОДНОЙ транзакции. Правится только LMS-прод.

Прод-DSN: LMS/.env = localhost(dev); прод брать через env DATABASE_URL из LMS/.mcp.json
(load_dotenv override=False). Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

PRES_JSON = r"C:\Users\user\AppData\Local\Temp\claude\D--Work-SPW\0437f822-6eb0-4be4-9abe-cb3eaad8db96\scratchpad\ascii_pres.json"

# mat_id -> anchor (ровно 1 раз; cb-ascii ставится сразу после якоря, как в 918/921/922)
ANCHORS = {
    957: "False</code></pre>",
    961: "момо мыло рому</code></pre>",
    924: "АЛЕКС_2024</code></pre>",
}


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    with open(PRES_JSON, encoding="utf-8") as f:
        pres = {int(k): v for k, v in json.load(f).items()}

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for mat_id, anchor in ANCHORS.items():
                pre = pres[mat_id]
                text = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", mat_id
                )
                if text is None:
                    raise RuntimeError(f"мат {mat_id}: не найден")
                if "cb-ascii" in text:
                    raise RuntimeError(f"мат {mat_id}: уже содержит cb-ascii — прерываю (не дублировать)")
                cnt = text.count(anchor)
                if cnt != 1:
                    raise RuntimeError(f"мат {mat_id}: якорь встречается {cnt} раз (нужно 1)")
                new_text = text.replace(anchor, anchor + "\n" + pre)
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    mat_id, new_text,
                )
                check = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", mat_id
                )
                assert "cb-ascii" in check, f"мат {mat_id}: вставка не подтвердилась"
                print(f"OK мат {mat_id}: cb-ascii вставлен (+{len(pre) + 1} симв.)")
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
