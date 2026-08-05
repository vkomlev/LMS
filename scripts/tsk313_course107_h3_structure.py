# -*- coding: utf-8 -*-
"""tsk-313: смысловая разметка подкурса 107 "Работа со словарями" + честный остаток.

Продолжение tsk-414/tsk-313 (см. reviews/2026-07-26-tsk414-tsk313-course88-qa-fixes.md).
Платформенный CSS-фикс (SPW/app/globals.css, коммит 5b48ebb) уже делает h1-h6/ul/ol
визуально выделенными во всех курсах. Здесь — честный остаток: материалы, где
смысловая подтема была подписана НЕ заголовком (<strong>label:</strong> перед блоком)
или перечисление шло текстом без списка. Канон — ContentBackbone/docs/wp-content-contract.md
+ живой эталон курс 823: каждая смысловая подтема — <h3> (вложенная детализация — <h4>),
перечисления — <ul>/<ol>, никогда голый текст/<strong>/<blockquote> вместо заголовка.

Инвентарь прочитан вручную по каждому материалу (не автогенерация regex'ом) — решение,
где проходит смысловая граница подтемы, принято по логике изложения текста, не механически.
Из изначально перечисленных 15 материалов (208,229,240,247-256,276,277) правится 6 —
у остальных 9 либо уже канон соблюдён (276 — 4 h3 в наличии; 253/229 — плоский список
методов без текстовых меток подтем, разбивка на группы была бы придуманной структурой,
не запросом текста), либо материал = единственная подтема, совпадающая с заголовком
материала (247/248/250/251/252/255 — <ol> с примерами без нужды в доп. заголовке).

Правки (текст НЕ переписывается, только структурная разметка):
1. material 208 (курс 103, "Модуль math"): подписи "Функции модуля math:" (дублирована
   дважды подряд — артефакт копипасты) и "Константы модуля math:" были голым текстом
   перед <ul> -> <h3>, дубль убран.
2. material 240 (курс 106, "Типы данных в Python"): два новых <h3> на уже
   существующих текстовых границах подтем ("Основные типы данных Python" перед
   разбором int/float/str/bool, "Динамическая типизация" перед абзацем про
   изменение типа переменной в рантайме).
3. material 249 (курс 107, "Изменяемые и неизменяемые типы данных"): подписи
   "Неизменяемые типы данных:"/"Изменяемые типы данных:" (те же два понятия, что и
   заголовок материала) были <strong> вместо <h3> — материал уже использовал <h3> для
   двух ДРУГИХ подтем ("Хэширование в Python", "Какие типы данных могут быть ключами")
   в этом же тексте, разметка была непоследовательна. "Объекты, которые МОЖНО/НЕЛЬЗЯ
   хэшировать:" — вложенная детализация внутри "Хэширование в Python" -> <h4>, по
   образцу уже существующего в этом материале <h4>"Можно ли хэшировать кортеж...".
4. material 254 (курс 107, "Агрегатные функции и сортировка в словарях"): заголовок
   материала называет ДВЕ подтемы, а текст даёт только первую как <strong>-подпись и
   прячет вторую пятым пунктом внутри того же <ol>. Подпись -> <h3>; пункт 5
   ("Сортировка словаря по ключам и значениям") вынесен из <ol> в свой <h3> + <ul> —
   без потери содержимого (список внутри пункта 5 как был).
5. material 256 (курс 107, "Распаковка словарей в Python"): "Основные способы
   использования распаковки словарей:" было голым текстом перед первым <ol>, тогда как
   вторая подтема материала уже оформлена <h3>"Оператор объединения словарей" — та же
   непоследовательность, что и в 249.
6. material 277 (курс 109, "Методы списков"): перечисление 11 методов списка шло
   текстом (<strong><code>x()</code></strong>: описание) без какой-либо ul/ol-обёртки —
   единственный материал в выборке, где перечисление ПОЛНОСТЬЮ отсутствовало как список
   (аналог уже готовых materials 253/229 "Методы X" — там перечисление уже <ol>).
   Обёрнуто в <ol><li>...</li></ol> по границам между методами (11 li); ни один
   код-пример/текст не тронут, только границы <li>.

Запуск: dry-run по умолчанию;
  python scripts/tsk313_course107_h3_structure.py
  DBCHECK_OK=1 python scripts/tsk313_course107_h3_structure.py --apply
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


# ---------- material 208 ----------

def fix_208(text: str) -> str:
    old_funcs = 'Функции модуля <code>math</code>:\r\n\r\nФункции модуля <code>math</code>:\r\n<ul>'
    assert text.count(old_funcs) == 1, f"метка функций встречается {text.count(old_funcs)} раз, ожидалась 1"
    new_text = text.replace(old_funcs, '<h3>Функции модуля math</h3>\r\n<ul>')

    old_consts = 'Константы модуля <code>math</code>:\r\n<ul>'
    assert new_text.count(old_consts) == 1, f"метка констант встречается {new_text.count(old_consts)} раз, ожидалась 1"
    new_text = new_text.replace(old_consts, '<h3>Константы модуля math</h3>\r\n<ul>')

    assert new_text.count('<h3>') == 2
    assert '<h3>Функции модуля math</h3>' in new_text
    assert '<h3>Константы модуля math</h3>' in new_text
    assert 'math.ceil(x)' in new_text  # содержимое функций цело
    assert 'math.pi' in new_text  # содержимое констант цело
    assert 'Функции модуля <code>math</code>:\r\n\r\n' not in new_text  # дубль убран
    return new_text


# ---------- material 240 ----------

def fix_240(text: str) -> str:
    old_types = '</ul>\r\nВ языках программирования, существует'
    assert text.count(old_types) == 1, f"граница 'Основные типы' встречается {text.count(old_types)} раз, ожидалась 1"
    new_text = text.replace(
        old_types,
        '</ul>\r\n<h3>Основные типы данных Python</h3>\r\nВ языках программирования, существует',
    )

    old_dynamic = (
        'логическими выражениями и условиями</strong>.\r\n\r\n'
        'Особенность типов данных в Python заключается'
    )
    assert new_text.count(old_dynamic) == 1, f"граница 'Динамическая типизация' встречается {new_text.count(old_dynamic)} раз, ожидалась 1"
    new_text = new_text.replace(
        old_dynamic,
        'логическими выражениями и условиями</strong>.\r\n\r\n<h3>Динамическая типизация</h3>\r\nОсобенность типов данных в Python заключается',
    )

    assert new_text.count('<h3>') == 2
    assert '<dfn>Тип данных int</dfn>' in new_text  # теория типов цела
    assert "a = 5 + 4" in new_text  # пример динамической типизации цел
    return new_text


# ---------- material 249 ----------

def fix_249(text: str) -> str:
    replacements = [
        ('<strong>Неизменяемые типы данных:</strong>', '<h3>Неизменяемые типы данных</h3>'),
        ('<strong>Изменяемые типы данных:</strong>', '<h3>Изменяемые типы данных</h3>'),
        ('<strong>Объекты, которые МОЖНО хэшировать:</strong>', '<h4>Объекты, которые МОЖНО хэшировать</h4>'),
        ('<strong>Объекты, которые НЕЛЬЗЯ хэшировать:</strong>', '<h4>Объекты, которые НЕЛЬЗЯ хэшировать</h4>'),
    ]
    new_text = text
    for old, new in replacements:
        assert new_text.count(old) == 1, f"{old!r} встречается {new_text.count(old)} раз, ожидалась 1"
        new_text = new_text.replace(old, new)

    assert new_text.count('<h3>Хэширование в Python</h3>') == 1  # уже существовавший h3 цел
    assert new_text.count('<h3>Какие типы данных могут быть ключами у словарей?</h3>') == 1
    assert '<h4>Можно ли хэшировать кортеж' in new_text  # уже существовавший h4 цел
    assert new_text.count('<h3>') == 4
    assert new_text.count('<h4>') == 3
    assert 'x = 5' in new_text and 'my_list = [1, 2, 3]' in new_text  # примеры типов целы
    return new_text


# ---------- material 254 ----------

def fix_254(text: str) -> str:
    old_head = '<strong>Применение агрегатных функций к словарям:</strong>'
    assert text.startswith(old_head), "материал не начинается с ожидаемой метки"
    new_text = '<h3>Применение агрегатных функций к словарям</h3>' + text[len(old_head):]

    split_old = '</li>\n<li><strong>Сортировка словаря по ключам и значениям:</strong>\n<ul>'
    assert new_text.count(split_old) == 1, f"граница сортировки встречается {new_text.count(split_old)} раз, ожидалась 1"
    split_new = '</li>\n</ol>\n<h3>Сортировка словаря по ключам и значениям</h3>\n<ul>'
    new_text = new_text.replace(split_old, split_new)

    tail_old = 'список кортежей.</li>\n</ul>\n</li>\n</ol>'
    assert new_text.endswith(tail_old), "хвост материала не совпадает с ожидаемым закрытием списков"
    new_text = new_text[: -len(tail_old)] + 'список кортежей.</li>\n</ul>'

    assert new_text.count('<h3>') == 2
    assert new_text.count('<ol>') == 1 and new_text.count('</ol>') == 1  # осталась только agg-functions ol
    assert 'total_price = sum(prices.values())' in new_text  # агрегатные функции целы
    assert 'sorted_prices_by_value' in new_text  # сортировка по значениям цела
    assert 'sorted_prices = dict(sorted(prices.items()))' in new_text  # сортировка по ключам цела
    return new_text


# ---------- material 256 ----------

def fix_256(text: str) -> str:
    old = 'Основные способы использования распаковки словарей:\r\n<ol>'
    assert text.count(old) == 1, f"метка встречается {text.count(old)} раз, ожидалась 1"
    new_text = text.replace(old, '<h3>Основные способы использования распаковки словарей</h3>\r\n<ol>')

    assert new_text.count('<h3>') == 2
    assert '<h3>Оператор объединения словарей</h3>' in new_text  # уже существовавший h3 цел
    assert 'combined_dict = {**dict1, **dict2}' in new_text  # содержимое цело
    return new_text


# ---------- material 277 ----------

MARKERS_277 = [
    '<strong><code>append()</code>:</strong>',
    '<strong><code>insert()</code></strong>:',
    '<code>remove()</code>:',
    '<code><strong>pop()</strong></code><strong>:</strong>',
    '<strong><code>clear()</code></strong>:',
    '<strong><code>index()</code></strong>:',
    '<strong><code>count()</code></strong>:',
    '<strong><code>sort()</code></strong>:',
    '<code><strong>reverse()</strong></code>:',
    '<code><strong>copy()</strong></code> -',
    '<code><strong>extend()</strong></code> -',
]


def fix_277(text: str) -> str:
    positions = []
    for marker in MARKERS_277:
        assert text.count(marker) == 1, f"маркер {marker!r} встречается {text.count(marker)} раз, ожидалась 1"
        positions.append(text.index(marker))
    assert positions == sorted(positions), "маркеры методов не по порядку в тексте"

    intro = text[: positions[0]]
    segments = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        segments.append(text[start:end].rstrip())

    body = "<ol>\n" + "".join(f"<li>{seg}</li>\n" for seg in segments) + "</ol>"
    new_text = intro + body

    assert new_text.count('<li>') == 11 and new_text.count('</li>') == 11
    assert new_text.count('<ol>') == 1 and new_text.count('</ol>') == 1
    assert 'Списки - это изменяемый тип данных' in new_text  # интро цело
    for method in ('append(', 'insert(', 'remove(', 'pop(', 'clear(', 'index(', 'count(', 'sort(', 'reverse(', 'copy(', 'extend('):
        assert method in new_text
    assert 'lst1.extend(lst2)' in new_text  # последний пример цел
    return new_text


FIXES = {
    208: fix_208,
    240: fix_240,
    249: fix_249,
    254: fix_254,
    256: fix_256,
    277: fix_277,
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
                print(f"--- material {material_id} ({row['title']}) course={row['course_id']} ---")
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
