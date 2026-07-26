# -*- coding: utf-8 -*-
"""tsk-414 + tsk-313: точечные правки content->>'text' материалов курса 88.

Все правки — read-verify-apply-verify по одному материалу за раз, с явным
"ДО"/"ПОСЛЕ" и ассертами на сохранность окружающего текста.

1. material 237 (курс 106): убрать мёртвый хвост-артефакт WP-парсинга
   "<h3>Задание 1</h3>\n<blockquote class=\"info\">Задание 1</blockquote>" —
   пустой дубль-заголовок без содержимого (реальное задание 1 уже есть
   отдельной сущностью в tasks, id=111..121). QA: "убрать дубль".
2. material 238 (курс 106): тот же паттерн для "Задание 2" (QA: "задание 2
   отсутствует" — потому что там буквально пусто) + добавить "?" в конец
   заголовка "Какие имена можно давать переменным в Python" (QA: "выделить
   заголовок, в конце заголовка поставить знак вопроса").
3. material 239 (курс 106, tsk-313): "Ввод"/"Вывод" сейчас
   <blockquote class="warning"><strong>Термин</strong> - ...</blockquote>
   вместо <h3> — обернуть в заголовки согласно канону навигатора.
4. material 263 (курс 108, tsk-313): убрать мусорную вставку HTML-обёртки
   чат-интерфейса (div.react-scroll-to-bottom и т.п.), вставленную поверх
   легитимного <ol> списка методов строк при копипасте из ChatGPT.

Запуск: dry-run по умолчанию;
  python scripts/tsk414_material_content_fixes.py
  DBCHECK_OK=1 python scripts/tsk414_material_content_fixes.py --apply
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


# ---------- material 237 ----------
# QA "убрать дубль": <h3>Задание 1</h3> и сразу под ним
# <blockquote class="info">Задание 1</blockquote> — блокквот дословно повторяет
# заголовок и не несёт информации (в отличие от 238, здесь ЕСТЬ реальный
# контент задания дальше — шаги настройки IDLE — его не трогаем).

DUP_BLOCKQUOTE_237 = '\n<blockquote class="info">Задание 1</blockquote>'


def fix_237(text: str) -> str:
    assert text.count(DUP_BLOCKQUOTE_237) == 1, f"дубль-блокквот встречается {text.count(DUP_BLOCKQUOTE_237)} раз, ожидался 1"
    new_text = text.replace(DUP_BLOCKQUOTE_237, "")
    assert "<h3>Задание 1</h3>" in new_text  # заголовок остаётся
    assert 'blockquote class="info">Задание 1' not in new_text  # дубль убран
    assert "Заранее создайте рабочую папку" in new_text  # реальный контент задания цел
    return new_text


# ---------- material 238 ----------

STUB_238 = '\n<h3>Задание 2</h3>\n<blockquote class="info">Задание 2</blockquote>'
OLD_HEADING_238 = "<h3>Какие имена можно давать переменным в Python</h3>"
NEW_HEADING_238 = "<h3>Какие имена можно давать переменным в Python?</h3>"


def fix_238(text: str) -> str:
    assert text.count(STUB_238) == 1, f"стаб встречается {text.count(STUB_238)} раз, ожидался 1"
    assert text.count(OLD_HEADING_238) == 1, "заголовок без '?' встречается не 1 раз"
    new_text = text.replace(STUB_238, "").rstrip()
    new_text = new_text.replace(OLD_HEADING_238, NEW_HEADING_238)
    assert "Задание 2" not in new_text
    assert NEW_HEADING_238 in new_text
    assert "Имя переменной должно начинаться" in new_text  # теория цела
    return new_text


# ---------- material 239 ----------

OLD_VVOD = '<blockquote class="warning"><strong>Ввод</strong> - это когда, программа ждет каких либо данных от пользователя.</blockquote>'
NEW_VVOD = '<h3>Ввод</h3>\n<p>Ввод - это когда, программа ждет каких либо данных от пользователя.</p>'
OLD_VYVOD = '<blockquote class="warning"><strong>Вывод</strong> - программа выдает информацию пользователю для дальнейшего использования.</blockquote>'
NEW_VYVOD = '<h3>Вывод</h3>\n<p>Вывод - программа выдает информацию пользователю для дальнейшего использования.</p>'


def fix_239(text: str) -> str:
    assert text.count(OLD_VVOD) == 1
    assert text.count(OLD_VYVOD) == 1
    new_text = text.replace(OLD_VVOD, NEW_VVOD).replace(OLD_VYVOD, NEW_VYVOD)
    assert "<h3>Ввод</h3>" in new_text
    assert "<h3>Вывод</h3>" in new_text
    assert "<h3>Функции в Python</h3>" in new_text  # остальная теория цела
    return new_text


# ---------- material 263 ----------

GARBAGE_OPEN = (
    '<div class="flex-1 overflow-hidden">\n'
    '<div class="react-scroll-to-bottom--css-xtigk-79elbk h-full dark:bg-gray-800">\n'
    '<div class="react-scroll-to-bottom--css-xtigk-1n7m0yu">\n'
    '<div class="flex flex-col items-center text-sm dark:bg-gray-800">\n'
    '<div class="group w-full text-gray-800 dark:text-gray-100 border-b border-black/10 dark:border-gray-900/50 bg-gray-50 dark:bg-[#444654]">\n'
    '<div class="text-base gap-4 md:gap-6 md:max-w-2xl lg:max-w-xl xl:max-w-3xl p-4 md:py-6 flex lg:px-0 m-auto">\n'
    '<div class="relative flex w-[calc(100%-50px)] flex-col gap-1 md:gap-3 lg:w-[calc(100%-115px)]">\n'
    '<div class="flex flex-grow flex-col gap-3">\n'
    '<div class="min-h-[20px] flex flex-col items-start gap-4 whitespace-pre-wrap">\n'
    '<div class="markdown prose w-full break-words dark:prose-invert dark">\n'
)
GARBAGE_CLOSE_SUFFIX = "\n" + ("</div>\n" * 9) + "</div>"


def fix_263(text: str) -> str:
    assert text.count(GARBAGE_OPEN) == 1, "открывающий мусорный блок встречается не 1 раз"
    assert text.endswith(GARBAGE_CLOSE_SUFFIX), "хвост материала не совпадает с ожидаемым мусорным закрытием"
    start = text.index(GARBAGE_OPEN)
    inner_start = start + len(GARBAGE_OPEN)
    inner_end = len(text) - len(GARBAGE_CLOSE_SUFFIX)
    inner = text[inner_start:inner_end]
    assert inner.startswith("<ol>") and inner.endswith("</ol>"), "внутренний список не найден на ожидаемых границах"
    new_text = text[:start] + inner
    assert "react-scroll-to-bottom" not in new_text
    assert "flex-1 overflow-hidden" not in new_text
    assert "<code>maketrans(x[, y[, z]])</code>" in new_text  # содержимое списка цело
    assert "<h3>Прочие строковые методы</h3>" in new_text  # остальная теория цела
    return new_text


FIXES = {
    237: fix_237,
    238: fix_238,
    239: fix_239,
    263: fix_263,
}


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for material_id, fixer in FIXES.items():
                row = await conn.fetchrow(
                    "SELECT id, course_id, title, is_active, content FROM materials WHERE id = $1",
                    material_id,
                )
                if row is None:
                    raise AssertionError(f"материал {material_id} не найден")
                content = json.loads(row["content"]) if isinstance(row["content"], str) else dict(row["content"])
                text = content.get("text", "")
                new_text = fixer(text)
                print(f"--- material {material_id} ({row['title']}) ---")
                print(f"ДО:    длина={len(text)}")
                print(f"ПОСЛЕ: длина={len(new_text)} (Δ{len(new_text) - len(text):+d})")

                if apply:
                    new_content = dict(content)
                    new_content["text"] = new_text
                    await conn.execute(
                        "UPDATE materials SET content = $1::jsonb, updated_at = now() WHERE id = $2",
                        json.dumps(new_content, ensure_ascii=False), material_id,
                    )
                    after = await conn.fetchval(
                        "SELECT length(content->>'text') FROM materials WHERE id = $1", material_id
                    )
                    if after != len(new_text):
                        raise AssertionError(f"material {material_id}: после UPDATE длина={after}, ожидалось {len(new_text)}")
                    print(f"Проверка после UPDATE: длина={after} OK")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
