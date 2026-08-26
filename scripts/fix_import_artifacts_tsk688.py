# -*- coding: utf-8 -*-
"""tsk-688: следы импорта, которые ученик читает каждый день.

ЧТО ЧИНИТ (четыре класса, найдены по боевой базе 26.08)

1. СКЛЕЙКА — в названии материала пропал пробел вокруг латиницы
   («Основные команды модуляturtle»). Пришло из WP: у заголовка сняли теги
   (`<code>turtle</code>`), а пробел на их месте не поставили. Что должно
   стоять — доказано телом соседнего материала 427, где те же названия
   написаны с пробелами («Рисование с помощью turtle», «Модуль Turtle в
   Python»). 8 материалов.

2. ПОДМЕНА БУКВЫ (гомоглиф) — внутри слова стоит буква другого алфавита,
   неотличимая на глаз: «Cоздание» с латинской C, «Ассистent», «rezultаt» с
   русской «а», «СBA» с русской «С». Опасна не видом, а тем, что ученик
   копирует такую строку в программу: латинское «CBA» и «СBA» с русской С —
   разные строки, поиск/сравнение молча не сработает. 8 мест.

3. СЛИПШИЕСЯ ТИРЕ АЗБУКИ МОРЗЕ — 4 задания курса 1112. Здесь порчи нет:
   символов ровно столько, сколько нужно. Слипается ПРИ ОТРИСОВКЕ — знак
   U+2013 EN DASH в засечковом шрифте кабинета идёт вплотную к соседнему, и
   «тире тире» выглядит одной линией (проверено глазами на боевой странице и
   отдельным рендером кандидатов). Меняем знак на U+2212 MINUS SIGN, который
   в том же шрифте показывает просвет между знаками. Правка знака в условии
   безопасна для проверки ответа: в эталонах этих заданий только русские
   буквы, тире там не участвуют (сверено).

4. НЕРАСКРЫТЫЕ ВСТАВКИ WORDPRESS — при переносе не развернулись `[gallery]` и
   `[caption]`, и в уроке осталась служебная строка вместо картинок:
   - `[gallery ids="5844,5843,…"]` — 8 вставок в 3 материалах. Картинок нет
     ВООБЩЕ: галерея ссылается на вложения по номеру, тега `<img>` в тексте
     нет. Ученик читает `[gallery type="slideshow" … ids="…"]` (проверено
     глазами на боевой странице материала 320). Это и есть жалоба «не
     грузится картинка» — только в других уроках, не в «Установке Python».
     Адреса 24 картинок восстановлены по номерам через открытый API сайта
     (`/wp-json/wp/v2/media/<номер>`), порядок сохранён как в исходной
     вставке, каждый адрес проверен на доступность.
   - `[caption id="attachment_5038" …]…[/caption]` — 6 вставок в 3 материалах.
     Здесь картинка видна (тег `<img>` на месте), но вокруг неё напечатана
     служебная строка. Убираем обёртку, подпись оставляем курсивом.

   Почему подпись именно `<p><em>`, а не `<figure>/<figcaption>`: кабинет чистит
   HTML перед показом (`spw/lib/material/sanitize.ts`) и вырезает запрещённые
   теги ВМЕСТЕ с содержимым (`KEEP_CONTENT: false`). `p`, `em`, `a`, `img` стоят
   в явном списке разрешённых — за них можно ручаться; `figure`/`figcaption` в
   явном списке нет, и держатся они только на общем профиле HTML, то есть на
   умолчании библиотеки. Ставить подпись урока в зависимость от умолчания не
   стоит: поменяется настройка — подпись исчезнет молча.

ЧЕГО НЕ ДЕЛАЕТ
- Не трогает эталоны (`solution_rules`) — это tsk-687, задача закрыта.
- Не трогает `order_position` и структуру блоков — это tsk-689.
- Не чинит порчу `N` → `№`: все 35 заданий с ней выключены (`is_active=false`,
  импорт PDF Крылова, 440 заданий, активных ноль) — ученик их не видит.
- Не правит импортёр — это соседний чип tsk-691.

ЗАМЕНЫ ТОЧЕЧНЫЕ, НЕ РЕГУЛЯРНЫМ ВЫРАЖЕНИЕМ ПО БАЗЕ
Каждая правка адресована по id и сверяется с ожидаемым исходным значением:
если в базе лежит не то, что мы прочитали при разборе, строка пропускается и
попадает в отчёт. Повторный прогон ничего не меняет (после первого прохода
исходное значение уже не совпадает) — идемпотентно.

Запуск (ничего не пишет, показывает план и выборку):
    PYTHONIOENCODING=utf-8 python scripts/fix_import_artifacts_tsk688.py

Запись на прод (после разбора выборки):
    DBCHECK_OK=1 python scripts/fix_import_artifacts_tsk688.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

#: Материалы: (id, было, стало) для колонки `title`.
MATERIAL_TITLES: list[tuple[int, str, str]] = [
    (266, "Модульstring", "Модуль string"),
    (427, "Основные команды модуляturtle", "Основные команды модуля turtle"),
    (
        311,
        "МодульTurtleв Python. Работаем с черепашьей графикой.",
        "Модуль Turtle в Python. Работаем с черепашьей графикой.",
    ),
    (312, "Рисование с помощьюturtle", "Рисование с помощью turtle"),
    (
        314,
        "Рисование сложных узоров в модулеturtle",
        "Рисование сложных узоров в модуле turtle",
    ),
    (316, "Обработка событий вturtle", "Обработка событий в turtle"),
    (317, "Анимация вturtle", "Анимация в turtle"),
    (225, "Модульfunctools", "Модуль functools"),
    # Гомоглиф: латинская C (U+0043) в начале русского слова.
    (
        237,
        "Cоздание файла"
        " с программой"
        " на языке Python.",
        "Создание файла"
        " с программой"
        " на языке Python.",
    ),
]

#: Материалы: точечная замена внутри `content->>'text'`: (id, фрагмент, замена).
#: Фрагмент обязан встречаться в тексте ровно один раз — иначе правка не идёт.
MATERIAL_TEXT_FRAGMENTS: list[tuple[int, str, str]] = [
    # «Ассистent»: русское «Ассист» + латинское «ent».
    (2533, "Ассистent", "Ассистент"),
    # Голый знак «меньше» перед латинской буквой. Браузер читает «<N» как начало
    # тега и молча съедает текст до ближайшего «>» — вместе с остатком урока.
    # Проверено глазами: материал 278 обрывался на «Если i», материал 389 — на
    # «квадрат N×N (1». Лечится экранированием: «&lt;» рисуется как «<», но
    # тегом уже не выглядит. Пары «>» экранируем заодно, чтобы запись была
    # однородной (сам по себе «>» в тексте безопасен).
    (278, "Если i<j (элемент выше диагонали)", "Если i&lt;j (элемент выше диагонали)"),
    (278, "Если i>j (элемент ниже диагонали)", "Если i&gt;j (элемент ниже диагонали)"),
    (389, "квадрат N×N (1<N<17)", "квадрат N×N (1&lt;N&lt;17)"),
    (3646, "#include <DHT.h>", "#include &lt;DHT.h&gt;"),
]

#: Задания: точечная замена внутри `task_content->>'title'`.
TASK_TITLE_FRAGMENTS: list[tuple[int, str, str]] = [
    # «rezultаt»: русская «а» (U+0430) в латинском имени переменной.
    (5636, "rezultаt", "rezultat"),
]

#: Задания: точечная замена внутри `task_content->>'stem'`.
TASK_STEM_FRAGMENTS: list[tuple[int, str, str]] = [
    # Русская «С» (U+0421) в примере «ABC + СBA + ABC + CAB».
    (4094, "+ СBA +", "+ CBA +"),
    # Русская «А» (U+0410) в начале образца «АBBCCCAAAADDDDD».
    (4167, "АBBCCCAAAADDDDD", "ABBCCCAAAADDDDD"),
    # Русская «А» (U+0410) как цифра A в записи числа по основанию 18.
    (4239, "5xyА", "5xyA"),
    # Русская «Т» (U+0422) в примерах «ТEN» и «NUТ».
    (4247, "ТEN", "TEN"),
    (4247, "NUТ", "NUT"),
    # Русская «е» (U+0435) в конце имени функции на C.
    (9663, "poluchitSredneе", "poluchitSrednee"),
]

#: Задания азбуки Морзе: заменить U+2013 EN DASH на U+2212 MINUS SIGN во всём
#: условии. Проверено: других тире (в роли знака препинания) в этих условиях нет.
MORSE_TASK_IDS: list[int] = [6400, 6401, 6402, 6408]

EN_DASH = "–"
MINUS_SIGN = "−"

#: Материалы с нераскрытыми вставками WordPress.
SHORTCODE_MATERIAL_IDS: list[int] = [320, 321, 336, 358, 360, 389]

#: Номер вложения WP -> адрес и подпись. Снято 26.08 через открытый API сайта
#: (`/wp-json/wp/v2/media/<номер>`), каждый адрес проверен на доступность (200).
WP_MEDIA: dict[str, tuple[str, str]] = {
    "5841": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Itog.jpg", "Итог"),
    "5842": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Poisk-rajona-s-pomoshhyu-VPR.jpg",
        "Поиск района с помощью ВПР",
    ),
    "5843": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/VPR-dlya-tovara.jpg",
        "ВПР для товара",
    ),
    "5844": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Sozdaem-novye-kolonki.jpg",
        "Создаем новые колонки",
    ),
    "5846": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Svodnaya-tablitsa.jpg",
        "Сводная таблица",
    ),
    "5847": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Razmetka-svodnoj-tablitsy.jpg",
        "Разметка сводной таблицы",
    ),
    "5848": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Obogashhenie-dannyh.jpg",
        "Обогащение данных",
    ),
    "5849": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/VPR-dlya-kategorii.jpg",
        "ВПР для категории",
    ),
    "5850": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Novye-stolbtsy.jpg",
        "Новые столбцы",
    ),
    "5851": (
        "https://victor-komlev.ru/wp-content/uploads/2025/09/Poisk-rajona-s-pomoshhyu-VPR-1.jpg",
        "Поиск района с помощью ВПР",
    ),
    "6022": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie1_2.png", "Задание 1 (2)"),
    "6023": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie1.png", "Задание 1"),
    "6024": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-2_4.png", "Задание 2 (4)"),
    "6025": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-2_3.png", "Задание 2 (3)"),
    "6026": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-2_2.png", "Задание 2 (2)"),
    "6027": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie2_1.png", "Задание 2 (1)"),
    "6028": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-3_2.png", "Задание 3 (2)"),
    "6029": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-3.png", "Задание 3"),
    "6030": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-4_2.png", "Задание 4 (2)"),
    "6031": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-4.png", "Задание 4"),
    "6032": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-5_2.png", "Задание 5 (2)"),
    "6033": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-5.png", "Задание 5"),
    "6035": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-7_2.png", "Задание 7 (2)"),
    "6036": ("https://victor-komlev.ru/wp-content/uploads/2025/09/Zadanie-7.png", "Задание 7"),
}

_GALLERY_RE = re.compile(r"\[gallery\b[^\]]*\bids=\"([0-9,\s]+)\"[^\]]*\]")
#: Открывающая часть, картинка (со ссылкой или без), подпись, закрывающая часть.
_CAPTION_RE = re.compile(
    r"\[caption\b[^\]]*\]\s*(?P<body><a\b.*?</a>|<img\b[^>]*/?>)\s*(?P<caption>[^\[]*?)\s*\[/caption\]",
    re.DOTALL,
)


def expand_galleries(text: str) -> tuple[str, int, list[str]]:
    """Развернуть `[gallery ids=…]` в картинки. Возвращает (текст, сколько, что не нашли)."""
    missing: list[str] = []
    replaced = 0

    def one(match: re.Match[str]) -> str:
        nonlocal replaced
        ids = [i.strip() for i in match.group(1).split(",") if i.strip()]
        parts: list[str] = []
        for mid in ids:
            item = WP_MEDIA.get(mid)
            if item is None:
                missing.append(mid)
                return match.group(0)  # не трогаем вставку, если хоть один номер не известен
            src, caption = item
            # Экранируем и адрес тоже: он приходит из внешнего источника (API
            # сайта), и кавычка в нём разорвала бы атрибут. У сегодняшних 24
            # адресов спецсимволов нет, так что на вывод это не влияет —
            # правило нужно, если списком воспользуются повторно.
            href = html.escape(src, quote=True)
            alt = html.escape(caption, quote=True)
            parts.append(
                f'<p><a href="{href}" target="_blank" rel="noopener noreferrer">'
                f'<img src="{href}" alt="{alt}"/></a></p>\n'
                f"<p><em>{html.escape(caption)}</em></p>"
            )
        replaced += 1
        return "\n".join(parts)

    return _GALLERY_RE.sub(one, text), replaced, missing


def unwrap_captions(text: str) -> tuple[str, int]:
    """Снять обёртку `[caption …]…[/caption]`, оставив картинку и подпись курсивом."""
    count = 0

    def one(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        body = match.group("body").strip()
        caption = match.group("caption").strip()
        if not caption:
            return body
        return f"{body}\n<p><em>{caption}</em></p>"

    return _CAPTION_RE.sub(one, text), count


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


def _show(label: str, before: str, after: str) -> None:
    """Напечатать одну строку плана: что было и что станет."""
    print(f"  {label}")
    print(f"    было : {before!r}")
    print(f"    стало: {after!r}")


async def plan(conn: asyncpg.Connection) -> list[tuple[str, int, str, str, str]]:
    """Собрать список правок, сверив текущее состояние базы с ожидаемым.

    Возвращает элементы вида (вид, id, поле, текущее значение, новое значение).
    Строки, где база уже не совпадает с ожидаемым, пропускаются с пометкой.
    """
    todo: list[tuple[str, int, str, str, str]] = []

    print("=== 1. Названия материалов (склейка и подмена буквы) ===")
    for mid, expect, new in MATERIAL_TITLES:
        row = await conn.fetchrow("SELECT id, title, is_active FROM materials WHERE id=$1", mid)
        if row is None:
            print(f"  ПРОПУСК material {mid}: строки нет")
            continue
        current = row["title"]
        if current == new:
            print(f"  УЖЕ ИСПРАВЛЕНО material {mid}")
            continue
        if current != expect:
            print(f"  ПРОПУСК material {mid}: в базе не то, что ожидали")
            print(f"    ожидали: {expect!r}")
            print(f"    в базе : {current!r}")
            continue
        _show(f"material {mid} (активен={row['is_active']})", current, new)
        todo.append(("material_title", mid, "title", current, new))

    print("\n=== 2. Текст материала (подмена буквы, голый знак «меньше») ===")
    # Как и с заданиями: все правки одного материала сводим в ОДНУ запись,
    # иначе вторая не совпадёт с уже изменённым текстом и откатит транзакцию.
    by_material: dict[int, list[tuple[str, str]]] = {}
    for mid, frag, repl in MATERIAL_TEXT_FRAGMENTS:
        by_material.setdefault(mid, []).append((frag, repl))

    for mid, pairs in by_material.items():
        row = await conn.fetchrow("SELECT id, title, content->>'text' AS body FROM materials WHERE id=$1", mid)
        if row is None or row["body"] is None:
            print(f"  ПРОПУСК material {mid}: нет текста")
            continue
        body = row["body"]
        new_body = body
        for frag, repl in pairs:
            count = new_body.count(frag)
            if count == 0:
                print(f"  ПРОПУСК material {mid}: фрагмент {frag!r} не найден (возможно, уже исправлен)")
                continue
            if count != 1:
                print(f"  ПРОПУСК material {mid}: фрагмент {frag!r} встречается {count} раз, ожидали 1")
                continue
            _show(f"material {mid} «{row['title']}» — текст", frag, repl)
            new_body = new_body.replace(frag, repl)
        if new_body != body:
            todo.append(("material_text", mid, "content.text", body, new_body))

    print("\n=== 3. Названия заданий (подмена буквы) ===")
    for tid, frag, repl in TASK_TITLE_FRAGMENTS:
        row = await conn.fetchrow(
            "SELECT id, task_content->>'title' AS title, is_active FROM tasks WHERE id=$1", tid
        )
        if row is None or row["title"] is None:
            print(f"  ПРОПУСК task {tid}: нет названия")
            continue
        title = row["title"]
        if frag not in title:
            print(f"  ПРОПУСК task {tid}: фрагмент {frag!r} не найден (возможно, уже исправлен)")
            continue
        _show(f"task {tid} — название (активно={row['is_active']})", title, title.replace(frag, repl))
        todo.append(("task_title", tid, "task_content.title", title, title.replace(frag, repl)))

    print("\n=== 4. Условия заданий (подмена буквы) ===")
    # Правки одного задания сводим в ОДНУ запись: у задания 4247 их две («ТEN» и
    # «NUТ»), и если слать их отдельно, вторая не совпадёт с уже изменённым
    # условием — сторож в apply() честно откатит всю транзакцию.
    by_task: dict[int, list[tuple[str, str]]] = {}
    for tid, frag, repl in TASK_STEM_FRAGMENTS:
        by_task.setdefault(tid, []).append((frag, repl))

    for tid, pairs in by_task.items():
        row = await conn.fetchrow(
            "SELECT id, task_content->>'stem' AS stem, is_active FROM tasks WHERE id=$1", tid
        )
        if row is None or row["stem"] is None:
            print(f"  ПРОПУСК task {tid}: нет условия")
            continue
        stem = row["stem"]
        new_stem = stem
        applied = 0
        for frag, repl in pairs:
            count = new_stem.count(frag)
            if count == 0:
                print(f"  ПРОПУСК task {tid}: фрагмент {frag!r} не найден (возможно, уже исправлен)")
                continue
            if count != 1:
                print(f"  ПРОПУСК task {tid}: фрагмент {frag!r} встречается {count} раз, ожидали 1")
                continue
            _show(f"task {tid} — условие (активно={row['is_active']})", frag, repl)
            new_stem = new_stem.replace(frag, repl)
            applied += 1
        if applied:
            todo.append(("task_stem", tid, "task_content.stem", stem, new_stem))

    print("\n=== 5. Тире азбуки Морзе (правка отображения через знак) ===")
    for tid in MORSE_TASK_IDS:
        row = await conn.fetchrow(
            "SELECT id, task_content->>'stem' AS stem, is_active FROM tasks WHERE id=$1", tid
        )
        if row is None or row["stem"] is None:
            print(f"  ПРОПУСК task {tid}: нет условия")
            continue
        stem = row["stem"]
        n = stem.count(EN_DASH)
        if n == 0:
            print(f"  ПРОПУСК task {tid}: знаков U+2013 нет (возможно, уже исправлено)")
            continue
        new_stem = stem.replace(EN_DASH, MINUS_SIGN)
        print(f"  task {tid} (активно={row['is_active']}): заменить {n} знак(ов) U+2013 на U+2212")
        head_old = stem[:110]
        head_new = new_stem[:110]
        print(f"    было : {head_old}")
        print(f"    стало: {head_new}")
        todo.append(("task_stem", tid, "task_content.stem", stem, new_stem))

    print("\n=== 6. Нераскрытые вставки WordPress ([gallery] и [caption]) ===")
    for mid in SHORTCODE_MATERIAL_IDS:
        row = await conn.fetchrow(
            "SELECT id, title, is_active, content->>'text' AS body FROM materials WHERE id=$1", mid
        )
        if row is None or row["body"] is None:
            print(f"  ПРОПУСК material {mid}: нет текста")
            continue
        body = row["body"]
        new_body, n_gal, missing = expand_galleries(body)
        if missing:
            print(f"  ВНИМАНИЕ material {mid}: нет адресов для вложений {sorted(set(missing))}"
                  f" — эти вставки оставлены как есть")
        new_body, n_cap = unwrap_captions(new_body)
        if new_body == body:
            print(f"  БЕЗ ИЗМЕНЕНИЙ material {mid} (возможно, уже исправлено)")
            continue
        left_gal = new_body.count("[gallery")
        left_cap = new_body.count("[caption") + new_body.count("[/caption]")
        print(f"  material {mid} «{row['title']}» (активен={row['is_active']}):"
              f" развёрнуто галерей {n_gal}, снято обёрток подписи {n_cap};"
              f" осталось служебных строк: gallery={left_gal}, caption={left_cap}")
        # Образец: показываем место первого расхождения, чтобы разметку можно
        # было прочитать глазами до записи.
        head = next((i for i, (a, b) in enumerate(zip(body, new_body)) if a != b), 0)
        print(f"    было : ...{body[max(head - 80, 0):head + 220]}...")
        print(f"    стало: ...{new_body[max(head - 80, 0):head + 320]}...")
        todo.append(("material_text", mid, "content.text", body, new_body))

    # Один объект — одна запись. Две записи на одну строку означают, что вторая
    # сверяется с УЖЕ устаревшим значением и обязательно откатит транзакцию;
    # ловим это здесь, до записи, а не по факту отката.
    seen: set[tuple[str, int]] = set()
    for kind, obj_id, _field, _old, _new in todo:
        if (kind, obj_id) in seen:
            raise RuntimeError(
                f"две правки на один объект: {kind} id={obj_id}."
                " Сведите их в одну — иначе вторая не совпадёт с изменённым значением."
            )
        seen.add((kind, obj_id))

    return todo


async def apply(conn: asyncpg.Connection, todo: list[tuple[str, int, str, str, str]]) -> int:
    """Применить правки одной транзакцией. Возвращает число изменённых строк."""
    changed = 0
    async with conn.transaction():
        for kind, obj_id, _field, old, new in todo:
            if kind == "material_title":
                res = await conn.execute(
                    "UPDATE materials SET title=$2, updated_at=now() WHERE id=$1 AND title=$3",
                    obj_id, new, old,
                )
            elif kind == "material_text":
                res = await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content, '{text}', to_jsonb($2::text)),"
                    " updated_at=now()"
                    " WHERE id=$1 AND content->>'text' = $3",
                    obj_id, new, old,
                )
            elif kind == "task_title":
                res = await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{title}', to_jsonb($2::text))"
                    " WHERE id=$1 AND task_content->>'title' = $3",
                    obj_id, new, old,
                )
            elif kind == "task_stem":
                res = await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb($2::text))"
                    " WHERE id=$1 AND task_content->>'stem' = $3",
                    obj_id, new, old,
                )
            else:
                raise RuntimeError(f"неизвестный вид правки: {kind}")
            n = int(res.split()[-1])
            if n != 1:
                raise RuntimeError(
                    f"{kind} id={obj_id}: обновлено строк {n}, ожидали 1 — откатываю всю транзакцию"
                )
            changed += n
    return changed


async def verify(conn: asyncpg.Connection) -> None:
    """Прочитать состояние после записи и показать его построчно."""
    print("\n=== Проверка после записи ===")
    for mid, _expect, new in MATERIAL_TITLES:
        row = await conn.fetchrow("SELECT title FROM materials WHERE id=$1", mid)
        mark = "OK " if row and row["title"] == new else "НЕ СОШЛОСЬ"
        print(f"  {mark} material {mid}: {row['title'] if row else '<нет строки>'!r}")

    for mid, frag, repl in MATERIAL_TEXT_FRAGMENTS:
        row = await conn.fetchrow("SELECT content->>'text' AS body FROM materials WHERE id=$1", mid)
        body = row["body"] if row else ""
        mark = "OK " if frag not in body and repl in body else "НЕ СОШЛОСЬ"
        print(f"  {mark} material {mid} текст: осталось вхождений {body.count(frag)}")

    for tid, frag, repl in TASK_TITLE_FRAGMENTS:
        row = await conn.fetchrow("SELECT task_content->>'title' AS title FROM tasks WHERE id=$1", tid)
        title = (row["title"] if row else "") or ""
        mark = "OK " if frag not in title and repl in title else "НЕ СОШЛОСЬ"
        print(f"  {mark} task {tid} название: {title!r}")

    for tid, frag, repl in TASK_STEM_FRAGMENTS:
        row = await conn.fetchrow("SELECT task_content->>'stem' AS stem FROM tasks WHERE id=$1", tid)
        stem = (row["stem"] if row else "") or ""
        mark = "OK " if frag not in stem and repl in stem else "НЕ СОШЛОСЬ"
        print(f"  {mark} task {tid} условие: фрагмент {repl!r} на месте, старого осталось {stem.count(frag)}")

    for tid in MORSE_TASK_IDS:
        row = await conn.fetchrow("SELECT task_content->>'stem' AS stem FROM tasks WHERE id=$1", tid)
        stem = (row["stem"] if row else "") or ""
        mark = "OK " if EN_DASH not in stem and MINUS_SIGN in stem else "НЕ СОШЛОСЬ"
        print(
            f"  {mark} task {tid}: U+2013 осталось {stem.count(EN_DASH)},"
            f" U+2212 стало {stem.count(MINUS_SIGN)}"
        )

    for mid in SHORTCODE_MATERIAL_IDS:
        row = await conn.fetchrow("SELECT content->>'text' AS body FROM materials WHERE id=$1", mid)
        body = (row["body"] if row else "") or ""
        left = body.count("[gallery") + body.count("[caption") + body.count("[/caption]")
        imgs = body.count("<img")
        mark = "OK " if left == 0 else "НЕ СОШЛОСЬ"
        print(f"  {mark} material {mid}: служебных строк осталось {left}, картинок в тексте {imgs}")

    # Эталоны не трогали — показываем, что они на месте и без тире.
    rows = await conn.fetch(
        "SELECT id, solution_rules->'short_answer'->'accepted_answers' AS answers"
        " FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
        MORSE_TASK_IDS,
    )
    print("\n  Эталоны заданий Морзе (не менялись):")
    for row in rows:
        print(f"    task {row['id']}: {row['answers']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-688: чистка следов импорта в боевой базе LMS")
    parser.add_argument("--apply", action="store_true", help="записать правки (по умолчанию только план)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только разбор и выборка, ничего не пишет (поведение по умолчанию)",
    )
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        todo = await plan(conn)
        print(f"\nВсего правок к применению: {len(todo)}")
        if args.dry_run or not args.apply:
            print("Это разбор без записи. Для записи: DBCHECK_OK=1 python "
                  "scripts/fix_import_artifacts_tsk688.py --apply")
            return 0
        if not todo:
            print("Менять нечего.")
            return 0
        changed = await apply(conn, todo)
        print(f"Записано строк: {changed}")
        await verify(conn)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
