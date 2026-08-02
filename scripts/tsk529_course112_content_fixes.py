# -*- coding: utf-8 -*-
"""tsk-529: контентные правки курса 112 (ЕГЭ по информатике) по итогам tsk-425/tsk-528.

22 пункта декомпозиции (docs/qa/2026-08-02-tsk425-course112-*.md) — определения,
примеры, дедуп, ссылки, артефакты генерации, пометки «для кругозора», активация
скрытого контента. Курс 112 — прод, реальные ученики; материалы импортированы
из WordPress (external_uid `wp:mat:komlev:...`), поэтому каждая контентная правка
(content/caption) помечается `content_provenance={"source":"manual_web",...}`
(tsk-433) — иначе ближайшее переиздание из WP молча вернёт прежний текст.
Служебные поля (`is_active`) провенанс не защищает и не требует (импорт их и так
не трогает без явной передачи — tsk-377/378/407).

Источники решений:
- docs/qa/2026-08-02-tsk425-course112-naive-learner-review.md
- docs/qa/2026-08-02-tsk425-course112-expert-course-review.md
- D:\\Work\\Root\\tasks\\tsk-528-...md (верификация п.1/2/3/4, История движения)

Запуск: dry-run по умолчанию;
  python scripts/tsk529_course112_content_fixes.py
  DBCHECK_OK=1 python scripts/tsk529_course112_content_fixes.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

OACITE_RE = re.compile(r":contentReference\[oaicite:\d+\]\{index=\d+\}")


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


def strip_oacite(text: str, expect: int) -> str:
    found = len(OACITE_RE.findall(text))
    assert found == expect, f"artefact count={found}, ожидалось {expect}"
    return OACITE_RE.sub("", text)


# =====================================================================
# И1 — материал 358 (тема 140): определение «информационная модель»
# =====================================================================
def fix_358(text: str) -> str:
    anchor = "<dfn>Граф</dfn> представляет собой набор вершин"
    assert text.startswith(anchor), "материал 358: не начинается с ожидаемого текста"
    intro = (
        '<p><dfn>Информационная модель</dfn> — упрощённое представление объекта, '
        'процесса или системы, которое отражает только те свойства и связи, '
        'что важны для решения конкретной задачи (второстепенные детали '
        'отбрасываются). Информационные модели бывают разными: таблица, схема, '
        'формула, граф — общий принцип один: реальный объект заменяется его '
        'описанием, удобным для анализа и расчётов.</p>\n'
        '<p>Ниже разбирается один из самых частых видов информационной модели '
        'в заданиях ЕГЭ — <strong>граф</strong> (сеть связей между объектами).</p>\n'
    )
    return intro + text


# =====================================================================
# И2+И16 — материал 404 (тема 149): синтаксис Excel-функций + чистка артефакта
# =====================================================================
EXCEL_SYNTAX_404 = (
    '<h3>Синтаксис Excel-функций, которые встречаются ниже</h3>\n'
    '<ul>\n'
    '<li><code>ВПР(искомое_значение; таблица; номер_столбца; 0)</code> — ищет '
    'значение в первом столбце «таблицы» и возвращает значение из указанного '
    'столбца той же строки.</li>\n'
    '<li><code>СЧЁТЕСЛИ(диапазон; условие)</code> — считает, сколько ячеек '
    'диапазона удовлетворяют условию (например, <code>"&gt;=8"</code>).</li>\n'
    '<li><code>СУММПРОИЗВ(массив1; массив2; …)</code> — перемножает элементы '
    'массивов поэлементно и суммирует результат; удобна для подсчёта '
    'пересечений условий без вспомогательных столбцов.</li>\n'
    '</ul>\n'
)


def fix_404(text: str) -> str:
    text = strip_oacite(text, expect=1)
    anchor = '<h3>1) «Сколько процессов'
    assert text.startswith(anchor), "материал 404: не начинается с ожидаемого h3"
    return EXCEL_SYNTAX_404 + text


# =====================================================================
# И2 — материал 412 (тема 154): синтаксис Excel-функций
# =====================================================================
EXCEL_SYNTAX_412 = (
    '<h3>Синтаксис Excel-функций, которые встречаются ниже</h3>\n'
    '<ul>\n'
    '<li><code>СРЗНАЧЕСЛИ(диапазон_условия; условие; диапазон_среднего)</code> '
    '— считает среднее по «диапазону_среднего», но только для строк, где '
    '«диапазон_условия» удовлетворяет «условию».</li>\n'
    '<li><code>СЧЁТЕСЛИ(диапазон; условие)</code> — считает, сколько ячеек '
    'диапазона удовлетворяют условию.</li>\n'
    '<li><code>СУММПРОИЗВ(массив1; массив2; …)</code> — перемножает элементы '
    'массивов поэлементно и суммирует результат.</li>\n'
    '</ul>\n'
)


def fix_412(text: str) -> str:
    anchor = "<h3>Файл A (2 кластера"
    assert text.startswith(anchor), "материал 412: не начинается с ожидаемого h3"
    return EXCEL_SYNTAX_412 + text


# =====================================================================
# И3 — материал 407 (тема 151): приём «префикс-сумма по модулю»
# =====================================================================
PREFIX_SUM_INTRO = (
    '<h4>Приём «префикс-сумма по модулю» (пригодится в B1)</h4>\n'
    '<p>Если нужно быстро находить подстроки, где сумма «значений» символов '
    'делится на <code>k</code>, удобно идти по строке и вести <em>префиксную '
    'сумму по модулю k</em>: <code>pref = (pref + значение_символа) % k</code>. '
    'Как только у двух позиций префиксная сумма по модулю совпала — сумма '
    'символов МЕЖДУ ними делится на k (разница двух одинаковых остатков даёт '
    'остаток 0). Приём хранит первый индекс, где встретился каждый остаток '
    '(словарь <code>first[остаток] = индекс</code>), и за один проход находит '
    'самый длинный такой блок.</p>\n'
)


def fix_407(text: str) -> str:
    anchor = "<h3>Раздел B. Дополнительные задачи из этого урока</h3>"
    assert text.count(anchor) == 1
    return text.replace(anchor, anchor + "\n" + PREFIX_SUM_INTRO)


# =====================================================================
# И4+И16 — материал 385 (тема 144): трассировка рекурсии + чистка артефакта
# =====================================================================
TRACE_385 = (
    '<strong>Трассировка вручную (пример для n = 6):</strong>\n'
    '<pre>F(6): 6 чётно &#8594; F(6) = F(3)\n'
    'F(3): 3 нечётно &#8594; F(3) = 1 + F(2)\n'
    'F(2): 2 чётно &#8594; F(2) = F(1)\n'
    'F(1): 1 нечётно &#8594; F(1) = 1 + F(0)\n'
    'F(0) = 0  (по определению)\n'
    'Разворачиваем обратно: F(1) = 1 + 0 = 1\n'
    'F(2) = F(1) = 1\n'
    'F(3) = 1 + F(2) = 1 + 1 = 2\n'
    'F(6) = F(3) = 2</pre>\n'
    '<p>Рекурсия «спускается» к F(0), а затем результат «поднимается» обратно '
    '— так же, как в задачах на теорию игр ниже. Теперь то же самое — кодом:</p>\n'
)


def fix_385(text: str) -> str:
    text = strip_oacite(text, expect=7)
    anchor = '<pre><code class="language-python">def f(n):\r\n    if n == 0:\r\n        return 0\r\n    elif n % 2 == 0:'
    assert text.count(anchor) == 1, "материал 385: код Задания 1 не найден однозначно"
    return text.replace(anchor, TRACE_385 + anchor)


# =====================================================================
# И5 — материал 378 (тема 142): визуальный пример позиционной системы
# =====================================================================
VISUAL_378 = (
    '\n<h3>Наглядный пример</h3>\n'
    '<p>Возьмём число <code>3052&#8328;</code> (запись в восьмеричной системе, '
    'N=8) и разложим его по разрядам:</p>\n'
    '<pre>  3      0      5      2\n'
    '  &#215;8&#179;    &#215;8&#178;    &#215;8&#185;    &#215;8&#8304;\n'
    ' 1536  +  0   + 40   +  2   = 1578&#8321;&#8320;</pre>\n'
    '<p>Каждая цифра «весит» тем больше, чем левее её позиция — так же, как в '
    'привычной десятичной системе (сравните: 3052 в десятичной = '
    '3&#183;10&#179;+0&#183;10&#178;+5&#183;10&#185;+2&#183;10&#8304;), только '
    'вместо степеней десятки используются степени основания N.</p>\n'
)


def fix_378(text: str) -> str:
    anchor = "<code>a<sup>N</sup>−1</code> в системе с основанием a записывается как N старших цифр (a−1).</li>\n</ul>"
    assert text.endswith(anchor), "материал 378: не заканчивается ожидаемым списком"
    return text + VISUAL_378


# =====================================================================
# И6 — материал 413 (тема 154): медоид vs среднее
# =====================================================================
MEDOID_INTRO = (
    '<h3>Чем медоид отличается от среднего (материал «Примеры решений заданий»)</h3>\n'
    '<p>В предыдущем материале центр кластера находили как <strong>среднее</strong> '
    'координат (функция <code>СРЗНАЧЕСЛИ</code>) — точка, которая обычно не '
    'совпадает ни с одной реальной точкой из данных, зато быстро считается. '
    'Здесь используется <strong>медоид</strong> — это <em>реальная точка из '
    'набора данных</em>, у которой сумма расстояний до всех остальных точек '
    'кластера минимальна. Медоид всегда «настоящая» точка (в отличие от '
    'среднего), поэтому он устойчивее к выбросам и подходит, когда центр '
    'обязан быть одним из наблюдений.</p>\n'
)


def fix_413(text: str) -> str:
    anchor = '<pre><code class="language-python">from math import hypot'
    assert text.startswith(anchor), "материал 413: не начинается с ожидаемого кода"
    return MEDOID_INTRO + text


# =====================================================================
# И7 — материал 331 (тема 161): общий признак 7 агрегатных функций
# =====================================================================
COMMON_FEATURE_331 = (
    '<p>Общий признак всех функций ниже: каждая берёт <strong>диапазон '
    'ячеек</strong> и сворачивает его в <strong>одно число</strong> — сумму, '
    'среднее, максимум, минимум или количество, — то есть агрегирует много '
    'значений в один результат.</p>\n'
)


def fix_331(text: str) -> str:
    anchor = "В доступны агрегатные функции"
    assert text.startswith(anchor), "материал 331: не начинается с ожидаемого текста"
    return COMMON_FEATURE_331 + text


# =====================================================================
# И8 — материал 394 (тема 148): рабочий пример тождества + сводная таблица
# =====================================================================
BROKEN_EXAMPLE_394 = (
    'Например, выражение "x = y ≡ y &gt; x" вернет значение false.'
)
FIXED_EXAMPLE_394 = (
    'Например, при <code>x = 5</code> и <code>y = 3</code>: выражение '
    '<code>(x &gt; y) &#8801; (y &gt; x)</code> — слева <code>5 &gt; 3</code> '
    'это true, справа <code>3 &gt; 5</code> это false; true &#8801; false '
    'даёт false (части не совпадают).'
)
SUMMARY_TABLE_394 = (
    '\n<h3>Сводная таблица логических операций</h3>\n'
    '<table>\n<thead><tr><th>Операция</th><th>Обозначение</th>'
    '<th>Когда истина</th></tr></thead>\n<tbody>\n'
    '<tr><td>Конъюнкция (И)</td><td>&#8743;, &amp;&amp;</td>'
    '<td>оба операнда истинны</td></tr>\n'
    '<tr><td>Дизъюнкция (ИЛИ)</td><td>&#8744;, ||</td>'
    '<td>хотя бы один операнд истинен</td></tr>\n'
    '<tr><td>Отрицание (НЕ)</td><td>&#172;, !</td>'
    '<td>инвертирует значение операнда</td></tr>\n'
    '<tr><td>Тождественное равенство</td><td>&#8801;</td>'
    '<td>оба операнда имеют одинаковое значение</td></tr>\n'
    '<tr><td>Импликация (следование)</td><td>&#8594;</td>'
    '<td>ложно только когда из истины следует ложь</td></tr>\n'
    '</tbody>\n</table>\n'
)


def fix_394(text: str) -> str:
    assert text.count(BROKEN_EXAMPLE_394) == 1, "материал 394: пример тождества не найден однозначно"
    text = text.replace(BROKEN_EXAMPLE_394, FIXED_EXAMPLE_394)
    tail = "Здесь операция \"НЕ\" используется для инвертирования утверждения о том, что мы будем есть мясо.</li>\n</ul>"
    assert text.endswith(tail), "материал 394: не заканчивается ожидаемым списком примеров"
    return text + SUMMARY_TABLE_394


# =====================================================================
# И9 — материал 428 (тема 157): встроить 5 картинок (сгенерированы и залиты
# в прод CAS отдельным шагом — scripts/../scratchpad/tsk529_upload_turtle_images.py)
# =====================================================================
MEDIA_BASE = "https://api.learn.victor-komlev.ru/api/v1/media"
TASK_IMAGES = [
    ("<strong>Ответ:</strong> 38 точек.\r\n\r\n</article>",
     "e4a76e0e4c85fe6dbfef8dfd3c7a1095759055dd7be49d5fa73a3fe3577166e8",
     "Фигура задачи 1 (звёздчатая ломаная) с координатной сеткой; красные точки — 38 целочисленных точек внутри без учёта границы."),
    ("<strong>Ответ:</strong> 36 точек.\r\n\r\n</article>",
     "8baef13a17c161cc41f78dca27cc035c1ee9757921baa41792eb0d3d8ba01740",
     "Прямоугольник задачи 2 с координатной сеткой; красные точки — 36 целочисленных точек внутри без учёта границы."),
    ("<strong>Ответ:</strong> 80 точек.\r\n\r\n</article>",
     "2c8f194435a7823b60e217465e53a985a6d7f97a845b53c19b91852eb5dd7faf",
     "Ромбовидная звезда задачи 3 с координатной сеткой; красные точки — 80 целочисленных точек внутри без учёта границы."),
    ("<strong>Ответ:</strong> 148 точек (включая точки на линии).\r\n\r\n</article>",
     "583f004c6e9ff45070eba9b8c533cafc1c5bd0c8e78d2306ad4f262ffe9b82e1",
     "Самопересекающаяся звезда задачи 4 с координатной сеткой (12 вершин); точное число точек 148 с учётом границы — см. текст, фигура самопересекается, поэтому точки на рисунке не размечены цветом."),
    ("<strong>Ответ:</strong> 51 точка.\r\n\r\n</article>",
     "3b6875e0b27d6beae012783f865ef60dcdcc5e9bfda8c52b5af1092dcb97289e",
     "Квадрат и повёрнутый ромб задачи 5 с координатной сеткой; красные точки — 51 точка внутри квадрата, но вне ромба."),
]


def fix_428(text: str) -> str:
    assert "<img" not in text, "материал 428: уже содержит <img> — не дублировать"
    for anchor, sha, alt in TASK_IMAGES:
        cnt = text.count(anchor)
        assert cnt == 1, f"материал 428: якорь {anchor[:40]!r} встречается {cnt} раз (нужно 1)"
        figure = f'\n<figure class="cb-image"><img src="{MEDIA_BASE}/{sha}.png" alt="{alt}"></figure>\n'
        text = text.replace(anchor, anchor.replace("</article>", figure + "</article>"))
    return text


# =====================================================================
# И10 — материал 382 (тема 143): отсылка назад к теме 148 (таблица истинности)
# =====================================================================
BACKLINK_382 = (
    '<p><em>Напоминание:</em> определения импликации, эквивалентности и '
    'таблицы истинности для них разобраны в теме «<strong>Задание 2. '
    'Таблицы истинности</strong>» (материалы «Логические операции» и '
    '«Таблица истинности логических выражений») — если термины ниже '
    'подзабылись, стоит туда заглянуть.</p>\n'
)


def fix_382(text: str) -> str:
    anchor = "<strong>Импликация</strong>"
    assert text.startswith(anchor), "материал 382: не начинается с ожидаемого текста"
    return BACKLINK_382 + text


# =====================================================================
# И11 — материал 420 (тема 156): убрать дословное дублирование 421-424
# =====================================================================
def fix_420(text: str) -> str:
    start_marker = '<section id="py-prereqs">'
    end_marker = '<h2>Задания для подготовки</h2>'
    start = text.index(start_marker)
    end = text.index(end_marker)
    assert start < end, "материал 420: неверный порядок маркеров"
    removed = text[start:end]
    for must in ("py-techniques", "task-simple", "task-advanced", "Вводные задачи"):
        assert must in removed, f"материал 420: в удаляемом блоке нет {must!r} — не тот диапазон"
    new_text = text[:start] + text[end:]
    assert "py-prereqs" not in new_text and "task-advanced" not in new_text
    assert "Функция <strong>bin(</strong>" in new_text, "материал 420: потерян уникальный интро-блок"
    assert "Задания для подготовки" in new_text, "материал 420: потерян уникальный блок ссылок"
    return new_text


# =====================================================================
# И12a — материал 427 (тема 157): сократить дублирование теории Turtle
# =====================================================================
POINTER_427 = (
    '<p>Основные команды <code>turtle</code> (движение, перо, вид) уже '
    'разобраны в дочерней теме «Черепашья графика с использованием модуля '
    'Turtle в Python»: <code>forward()</code>/<code>backward()</code>, '
    '<code>left()</code>/<code>right()</code>, <code>goto()</code> — в '
    'материале «Рисование с помощью turtle»; <code>penup()</code>/'
    '<code>pendown()</code>, <code>shape()</code>, <code>pensize()</code>, '
    '<code>pencolor()</code> — в материале «Модуль Turtle в Python».</p>\n'
    '<p>Специально для задания 6 пригодится ещё одна команда, которой нет в '
    'общей теории: <code>dot(radius)</code> — рисует закрашенную точку '
    'заданного диаметра. Она нужна для подсчёта целочисленных точек внутри '
    'фигуры (см. следующий материал «Разбор заданий»).</p>\n'
)


def fix_427(text: str) -> str:
    anchor = 'import turtle as t\r\n\r\nt.left(90)'
    assert anchor in text, "материал 427: не тот текст (ожидался список команд)"
    return POINTER_427


# =====================================================================
# И12b/И13 — материал 375 (тема 163): убрать дублирование класса TuringMachine
# =====================================================================
POINTER_375 = (
    '<p>Эталонный пример класса <code>TuringMachine</code> и всё, что с ним '
    'делать (моделирование +1/&#8722;1/&#215;2/&#247;2, копирование и т. д.), '
    'разобран в материале «<strong>Порядок устного решения</strong>» этой же '
    'темы, в разделе «Универсальный Python-симулятор Машины Тьюринга» — '
    'чтобы не держать один и тот же код в двух местах.</p>\n'
)


def fix_375(text: str) -> str:
    anchor = "class TuringMachine:"
    assert anchor in text, "материал 375: не тот текст (ожидался класс TuringMachine)"
    return POINTER_375


# =====================================================================
# И15+И18 — материал 355 (тема 139): структурные блоки + «для кругозора»
# =====================================================================
HEADING_355_1 = '<h2>Часть 1. Вручную: как посчитать адрес подсети</h2>\n'
HEADING_355_2 = (
    '<h2>Часть 2. То же самое в Python: функция и разбор числового задания ЕГЭ</h2>\n'
)
HEADING_355_3 = (
    '<h2>Часть 3. Справочник: объект IPv4Network (для общего кругозора)</h2>\n'
    '<p><em>Дальше — обзор методов объекта <code>IPv4Network</code> (перебор '
    'адресов, <code>.hosts()</code>, <code>is_private</code> и т. п.). Он не '
    'проверяется заданиями этой темы напрямую: банку заданий 13 достаточно '
    'вычисления адреса подсети вручную или через '
    '<code>calculate_subnet_address()</code> выше. Изучайте дальше, если '
    'интересно, как это устроено «изнутри».</em></p>\n'
)


def fix_355(text: str) -> str:
    assert text.startswith("Адрес подсети и маска подсети"), "материал 355: не тот текст в начале"
    anchor2 = '<h3>Реализация расчета адреса сети с помощью модуля <code>ipaddress</code> Python</h3>'
    anchor3 = '<h3>Создание объекта типа "сеть" в ipaddress</h3>'
    assert text.count(anchor2) == 1
    assert text.count(anchor3) == 1
    text = HEADING_355_1 + text
    text = text.replace(anchor2, HEADING_355_2 + anchor2)
    text = text.replace(anchor3, HEADING_355_3 + anchor3)
    return text


# =====================================================================
# И16 — чистые чистки артефакта (без прочих правок)
# =====================================================================
def fix_319(text: str) -> str:
    return strip_oacite(text, expect=1)


def fix_322(text: str) -> str:
    return strip_oacite(text, expect=1)


def fix_391(text: str) -> str:
    return strip_oacite(text, expect=12)


def fix_444(text: str) -> str:
    return strip_oacite(text, expect=1)


# =====================================================================
# И17 — материалы 320/321 (тема 138): файл через /api/v1/media
# =====================================================================
def fix_320(text: str) -> str:
    old = ('<strong>Условие:</strong> см. <a href="https://kompege.ru/task?id=1956" '
           'rel="noopener" target="_blank">kompege.ru/task?id=1956</a>. Используем '
           'входной файл из задания')
    new = ('<strong>Условие:</strong> задание взято с <a href="https://kompege.ru/task?id=1956" '
           'rel="noopener" target="_blank">kompege.ru/task?id=1956</a>. Файл к заданию: '
           '<a href="/api/v1/media/25c78c1c54f7df5916729277784ac491b8d2a6a7db11ac7968479e8bec0aec7d.xlsx" '
           'target="_blank" rel="noopener noreferrer">03.xlsx</a> (тот же файл использует '
           'соответствующее задание в банке). Используем входной файл из задания')
    assert text.count(old) == 1, "материал 320: якорь не найден однозначно"
    return text.replace(old, new)


def fix_321(text: str) -> str:
    old = ('<strong>Условие:</strong> <a href="https://inf-ege.sdamgia.ru/problem?id=75240" '
           'rel="noopener" target="_blank">sdamgia.ru/problem?id=75240</a>. Вложение: '
           '<code>03.ods</code>. Классический кейс')
    new = ('<strong>Условие:</strong> <a href="https://inf-ege.sdamgia.ru/problem?id=75240" '
           'rel="noopener" target="_blank">sdamgia.ru/problem?id=75240</a>. Файл к заданию: '
           '<a href="/api/v1/media/b2fd940a8c9c57e94fd8f166f73043b93655e0a74a67eb3faee5a39ca10b5081.ods" '
           'target="_blank" rel="noopener noreferrer">03.ods</a>. Классический кейс')
    assert text.count(old) == 1, "материал 321: якорь не найден однозначно"
    return text.replace(old, new)


# =====================================================================
# И19 — курс 165 (Turtle): «для общего кругозора» (4 материала)
# =====================================================================
KRUGOZOR_165 = (
    '<p><em>Для общего кругозора: этот материал не отрабатывается заданиями '
    'темы «Задание 6. Исполнитель Черепаха» — задания проверяют только базовые '
    'команды перемещения (целочисленные координаты точек), без цвета/анимации/'
    'интерактива. Изучайте дальше, если интересно.</em></p>\n'
)


# Фиксеры для 313/315/316/317 строятся динамически в main() (нужно сперва
# прочитать текст материала, чтобы взять точный якорь начала — см. ниже).


# =====================================================================
# И21 — материал 439 (тема 159): убрать блок «Вероятностные формулы»
# =====================================================================
def fix_439(text: str) -> str:
    start_marker = "<h3>📙 Вероятностные формулы</h3>"
    end_marker = "<h3>📘 Модуль Python <code>itertools</code></h3>"
    start = text.index(start_marker)
    end = text.index(end_marker)
    assert start < end
    removed = text[start:end]
    assert "Шеннон" in removed, "материал 439: удаляемый блок не про Шеннона — не тот диапазон"
    new_text = text[:start] + text[end:]
    assert "Шеннон" not in new_text
    assert "itertools" in new_text
    return new_text


CONTENT_FIXES = {
    358: fix_358,
    404: fix_404,
    412: fix_412,
    407: fix_407,
    385: fix_385,
    378: fix_378,
    413: fix_413,
    331: fix_331,
    394: fix_394,
    428: fix_428,
    382: fix_382,
    420: fix_420,
    427: fix_427,
    375: fix_375,
    355: fix_355,
    319: fix_319,
    322: fix_322,
    391: fix_391,
    444: fix_444,
    320: fix_320,
    321: fix_321,
    439: fix_439,
}

# И19 — динамические фиксеры для 313/315/316/317, префикс определяется по факту
# чтения материала в main() (см. ниже) — добавляются в CONTENT_FIXES там же.
KRUGOZOR_IDS = [313, 315, 316, 317]

# И14 — правка caption (не content) видео 631/632
CAPTION_FIXES = {
    631: "Это видео вы уже видели в теме «Задание 24. Обработка текста» — здесь "
         "просто напоминание техники работы с файлами перед новой темой.",
    632: "Тоже повтор из темы «Задание 24. Обработка текста» — как правильно "
         "указать путь к файлу.",
}

# И22 — активация (is_active=true), без content_provenance (служебное поле).
# И20 (материалы 408/410) уже выполнен tsk-530 (см. Root/tasks/tsk-530-...md,
# История движения П.5) — здесь не дублируем, только пакет 3 (И22).
ACTIVATE_IDS = [356, 371, 380]


def _provenance(fields: list[str]) -> str:
    return json.dumps({
        "source": "manual_web",
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "edited_by": "script:tsk529",
        "fields": fields,
    }, ensure_ascii=False)


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        # И19: читаем 313/315/316/317, чтобы определить точный якорь начала текста
        krugozor_rows = await conn.fetch(
            "SELECT id, content->>'text' AS text FROM materials WHERE id = ANY($1::int[])",
            KRUGOZOR_IDS,
        )
        krugozor_texts = {r["id"]: r["text"] for r in krugozor_rows}
        for mid in KRUGOZOR_IDS:
            text = krugozor_texts[mid]
            first_tag_end = text.index(">") + 1
            anchor = text[:first_tag_end] if text.startswith("<") else text[:20]

            def _fix(t: str, _anchor=anchor) -> str:
                assert t.startswith(_anchor), f"материал: не тот текст в начале ({_anchor!r})"
                return KRUGOZOR_165 + t
            CONTENT_FIXES[mid] = _fix

        async with conn.transaction():
            print("=" * 78)
            print(f"tsk-529 · курс 112 · {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
            print("=" * 78)

            # ---- контентные правки ----
            for material_id, fixer in CONTENT_FIXES.items():
                row = await conn.fetchrow(
                    "SELECT id, course_id, title, content, content_provenance "
                    "FROM materials WHERE id = $1",
                    material_id,
                )
                if row is None:
                    raise AssertionError(f"материал {material_id} не найден")
                content = json.loads(row["content"]) if isinstance(row["content"], str) else dict(row["content"])
                text = content.get("text", "")
                new_text = fixer(text)
                print(f"--- material {material_id} ({row['title']}) course={row['course_id']} ---")
                print(f"    длина: {len(text)} -> {len(new_text)} (Δ{len(new_text) - len(text):+d})")

                if apply:
                    new_content = dict(content)
                    new_content["text"] = new_text
                    prev = row["content_provenance"]
                    if isinstance(prev, str):
                        prev = json.loads(prev)
                    prev_fields = prev.get("fields") if isinstance(prev, dict) and prev.get("source") == "manual_web" else []
                    merged_fields = sorted(set((prev_fields or []) + ["content"]))
                    await conn.execute(
                        "UPDATE materials SET content = $1::jsonb, "
                        "content_provenance = $2::jsonb, updated_at = now() WHERE id = $3",
                        json.dumps(new_content, ensure_ascii=False),
                        _provenance(merged_fields),
                        material_id,
                    )
                    after = await conn.fetchval(
                        "SELECT length(content->>'text') FROM materials WHERE id = $1", material_id
                    )
                    if after != len(new_text):
                        raise AssertionError(f"material {material_id}: после UPDATE длина={after}, ожидалось {len(new_text)}")

            # ---- caption-правки (И14) ----
            for material_id, caption in CAPTION_FIXES.items():
                row = await conn.fetchrow(
                    "SELECT id, title, caption, content_provenance FROM materials WHERE id = $1",
                    material_id,
                )
                if row is None:
                    raise AssertionError(f"материал {material_id} не найден")
                print(f"--- caption material {material_id} ({row['title']}) ---")
                print(f"    caption: {row['caption']!r} -> {caption!r}")
                if apply:
                    prev = row["content_provenance"]
                    if isinstance(prev, str):
                        prev = json.loads(prev)
                    prev_fields = prev.get("fields") if isinstance(prev, dict) and prev.get("source") == "manual_web" else []
                    merged_fields = sorted(set((prev_fields or []) + ["caption"]))
                    await conn.execute(
                        "UPDATE materials SET caption = $1, content_provenance = $2::jsonb, "
                        "updated_at = now() WHERE id = $3",
                        caption, _provenance(merged_fields), material_id,
                    )

            # ---- активация (И20 + И22) ----
            act_rows = await conn.fetch(
                "SELECT id, course_id, title, is_active FROM materials WHERE id = ANY($1::int[])",
                ACTIVATE_IDS,
            )
            for r in act_rows:
                print(f"--- activate material {r['id']} ({r['title']}) course={r['course_id']} "
                      f"is_active {r['is_active']} -> true ---")
            if apply:
                await conn.execute(
                    "UPDATE materials SET is_active = true, updated_at = now() WHERE id = ANY($1::int[])",
                    ACTIVATE_IDS,
                )

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
