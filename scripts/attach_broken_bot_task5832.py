# -*- coding: utf-8 -*-
"""Прикрепить файл с ошибкой к заданию 5832 («Бот молчит? Чиним», курс 928).

ПРОБЛЕМА (оператор, живой разбор)
Stem задания 5832 отсылает ученика "взять файл с ошибкой из материалов урока", но
файла нет НИГДЕ: ни в task_content.media, ни в одном из 9 материалов курса 928
(все текстовые, без ссылок/вложений). Задание физически невыполнимо — начинать
нечего.

КАКОЙ БАГ ЗАШИТ В ФАЙЛ
Подсказка задания ("проверь, есть ли в самом низу строка bot.infinity_polling()")
и материал курса ("Причина 4: забыли запустить поллинг") прямо указывают на
конкретную, единственную ошибку. Файл — рабочий echo-бот из ПРЕДЫДУЩЕГО урока
курса (id=927, "Бот-Эхо: повторяй за мной", материал id=1347 "Порядок имеет
значение" — эталонный код скопирован оттуда буква в букву для стилевой
согласованности: тот же импорт, `config.TOKEN`, порядок обработчиков), у которого
убрана ТОЛЬКО последняя строка `bot.infinity_polling()`. Скрипт при запуске
пройдёт сверху вниз без исключения и завершится, не начав слушать апдейты —
это и есть "бот запускается, но молчит" (не крашится, просто не слушает).

ФОРМАТ ФАЙЛА
LMS media allowlist (app/api/v1/media.py) не включает расширение .py — только
.txt/.zip/... из соображений allowlist-безопасности. Кладём как zip с bot.py
внутри (прецедент — tsk-164 CB ADR-0049, .zip уже в allowlist), чтобы ученик
получил рабочий на вид файл без переименования.

ПРИВЯЗКА К STEM
Файл технически привязывается к ЗАДАНИЮ (attached_file_paths + ссылка в stem) —
единственный проверенный рабочий механизм в кодовой базе (материалы такого не
поддерживают). Формулировка "из материалов урока" в исходном stem заменена на
"к этому заданию" — техническая точность, смысл задания не меняется.

Stem в plain-режиме (без HTML-тегов) — ссылка вставляется ПОЛНЫМ URL
(https://api.learn.victor-komlev.ru/...), чтобы сработал plain-autolink (см.
assignment-rules.md §4a); относительный /api/v1/media/... автолинком не станет.

Безопасность (/db-check Режим записи): dry-run по умолчанию. --apply сначала
кладёт файл в CAS/S3 и проверяет боевым эндпоинтом (ссылка осмысленна только
если реально отдаётся), ЗАТЕМ пишет БД в транзакции с verify внутри и
независимой проверкой после commit.

Запуск (из корня LMS):
  python scripts/attach_broken_bot_task5832.py
  DBCHECK_OK=1 python scripts/attach_broken_bot_task5832.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402
from tsk369_store_cas import check_public, load_cb_env  # noqa: E402

import asyncpg  # noqa: E402

TASK_ID = 5832
MEDIA_BASE_PUBLIC = "https://api.learn.victor-komlev.ru/api/v1/media"

# Копия эталонного кода курса 927, материал 1347 "Порядок имеет значение",
# с убранной последней строкой bot.infinity_polling() (Причина 4).
BOT_PY = """import telebot
from config import TOKEN

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def say_hi(message):
    bot.send_message(message.chat.id, "Привет! Я повторяю за тобой.")

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)
"""

OLD_STEM = (
    "Возьми файл с ошибкой из материалов урока (бот запускается, но молчит). "
    "Найди причину, назови её словами и почини. После правки бот должен "
    "отвечать. Потом прокачай Бот-Эхо: пусть на слово «привет» в любом виде "
    "он отвечает по-особенному, а всё остальное по-прежнему повторяет."
)


def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bot.py", BOT_PY)
    return buf.getvalue()


def new_stem(sha_ext: str) -> str:
    link = f"{MEDIA_BASE_PUBLIC}/{sha_ext}"
    return (
        f"Файл к заданию: {link}\n\n"
        "Возьми файл с ошибкой к этому заданию (бот запускается, но молчит). "
        "Найди причину, назови её словами и почини. После правки бот должен "
        "отвечать. Потом прокачай Бот-Эхо: пусть на слово «привет» в любом виде "
        "он отвечает по-особенному, а всё остальное по-прежнему повторяет."
    )


async def main(apply: bool) -> int:
    cas_root = load_cb_env()
    from monolith.external_tasks.media.cas_downloader import store_bytes_to_cas  # noqa: E402

    data = build_zip()
    sha_ext = f"{hashlib.sha256(data).hexdigest()}.zip"
    print(f"bot_broken.zip: {len(data)} байт, sha_ext={sha_ext[:16]}…")
    print("\n--- Содержимое bot.py внутри архива ---")
    print(BOT_PY)
    print(f"--- Ожидаемый bug: нет bot.infinity_polling() в самом низу ---")

    if apply:
        ok, note = check_public(sha_ext)
        if not ok:
            got = await store_bytes_to_cas(data, "zip", cas_root)
            if got != sha_ext:
                raise RuntimeError(f"CAS вернул {got!r} вместо {sha_ext!r}")
            ok, note = check_public(sha_ext)
        if not ok:
            raise RuntimeError(f"файл не отдаётся боевым эндпоинтом: {note}")
        print(f"  в хранилище и доступен: {note}")

    stem_after = new_stem(sha_ext)
    print(f"\n--- STEM AFTER ---\n{stem_after}")

    if not apply:
        print("\nDRY-RUN: в CAS и БД ничего не записано. Для записи — "
              "DBCHECK_OK=1 ... --apply.")
        return 0

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        row = await conn.fetchrow(
            "SELECT task_content->>'stem' AS stem FROM tasks WHERE id = $1", TASK_ID
        )
        if row is None:
            raise RuntimeError(f"задание {TASK_ID} не найдено")
        if (row["stem"] or "").strip() != OLD_STEM.strip():
            print("--- ТЕКУЩИЙ STEM ---")
            print(row["stem"])
            raise RuntimeError("текущий stem не совпал дословно с ожидаемым "
                                "(мог измениться с момента диагностики)")

        link = f"{MEDIA_BASE_PUBLIC}/{sha_ext}"
        async with conn.transaction():
            await conn.execute(
                "UPDATE tasks SET task_content = "
                "  jsonb_set("
                "    jsonb_set("
                "      jsonb_set(task_content, '{stem}', to_jsonb($2::text)),"
                "      '{has_attached_file}', 'true'::jsonb),"
                "    '{attached_file_paths}', $3::jsonb) "
                "WHERE id = $1",
                TASK_ID, stem_after, json.dumps([link]),
            )
            check = await conn.fetchrow(
                "SELECT task_content->>'stem' AS stem, "
                "       task_content->'attached_file_paths' AS paths "
                "FROM tasks WHERE id = $1", TASK_ID,
            )
            if check["stem"] != stem_after or json.loads(check["paths"] or "[]") != [link]:
                raise AssertionError("проверка внутри транзакции не прошла")
            print("Внутри транзакции: обновлено и проверено.")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetchrow(
            "SELECT task_content->>'stem' AS stem, "
            "       task_content->'has_attached_file' AS has_file, "
            "       task_content->'attached_file_paths' AS paths "
            "FROM tasks WHERE id = $1", TASK_ID,
        )
        ok = (after["stem"] == stem_after
              and after["has_file"] is True
              and json.loads(after["paths"] or "[]") == [link])
        print(f"  stem совпал: {after['stem'] == stem_after}; "
              f"has_attached_file: {after['has_file']}; "
              f"attached_file_paths: {after['paths']}")
        if not ok:
            print("  ПРОБЛЕМА: расхождение после commit")
            return 1
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(a.apply)))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(1)
