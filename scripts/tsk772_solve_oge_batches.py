"""tsk-772: решатели партий ОГЭ ``oge:reshu`` — сверка НАШЕГО условия с эталоном.

Зачем отдельно от сверки с первоисточником: та проверяет ответ и данные, но НЕ видит
перевёрнутый вопрос — числа те же, эталон верный, а спрошено обратное. Ровно так было
у 6486 («наибольшее» вместо «наименьшее» в источнике), и нашёл это только решатель.

Партии и типы заданий:

* ``t1``  — информационный объём текста (из списка вычеркнули слово);
* ``t3``  — истинность логического высказывания о числе X;
* ``t5``  — исполнитель с двумя командами, найти параметр b;
* ``t6``  — программа с условием, 9 запусков, сколько напечатало YES/NO;
* ``t8``  — язык поисковых запросов (включения-исключения);
* ``t10`` — системы счисления.

Задания, которые решатель не разобрал уверенно, помечаются ``??`` и НЕ считаются
ни сошедшимися, ни расходящимися: молчаливых выводов не делаем.

Запуск::

    python scripts/tsk772_solve_oge_batches.py             # все партии
    python scripts/tsk772_solve_oge_batches.py --batch t5  # одна партия
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Callable

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk772")

#: В условиях встречается типографский минус U+2212, а не ASCII-дефис.
MINUS = "−"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(MINUS, "-")).strip()


# ------------------------------------------------------------- t1: объём текста

def _bytes_per_char(body: str) -> int | None:
    bits = re.search(r"(\d+)\s*бит", body)
    if not bits:
        return None
    per_char = int(bits.group(1)) // 8
    return per_char or None


def solve_t1(stem: str) -> tuple[str | None, str]:
    """Информационный объём текста. Четыре подтипа задания 1 ОГЭ."""
    body = _norm(stem.split("Источник:")[0])
    per_char = _bytes_per_char(body)
    if per_char is None:
        return None, "не нашёл разрядность кодировки"

    # объём статьи: страниц × строк × символов
    pages = re.search(r"(\d+)\s*страниц", body)
    lines = re.search(r"(\d+)\s*строк", body)
    chars = re.search(r"(\d+)\s*символ", body)
    if pages and lines and chars:
        total = int(pages.group(1)) * int(lines.group(1)) * int(chars.group(1)) * per_char
        if re.search(r"Кбайт", body, re.I):
            if total % 1024:
                return None, f"объём {total} байт не делится на 1024 нацело"
            return str(total // 1024), f"{total} байт = {total // 1024} Кбайт"
        return str(total), f"{total} байт"

    quote = re.search(r"«([^»]+)»", body)

    # размер предложения целиком
    if re.search(r"[Оо]пределите размер", body) and quote:
        text = quote.group(1).strip()
        return str(len(text) * per_char), f"{len(text)} символов × {per_char} байт"

    delta = re.search(r"на\s+(\d+)\s*байт", body)
    if not delta:
        return None, "не нашёл изменение размера"
    diff_chars = int(delta.group(1)) // per_char

    # слово написано дважды: лишними стали слово и один пробел
    if re.search(r"два раза подряд|дважды", body):
        target = diff_chars - 1
        # берём слова САМОГО отрывка, а не всего условия: иначе в кандидаты
        # попадают «Windows», «написал», «отрывок» из вводной фразы
        fragment = body.split("Но одно слово")[0]
        for marker in ("отрывок:", "текст:", "предложение:"):
            if marker in fragment:
                fragment = fragment.split(marker, 1)[1]
                break
        words = re.findall(r"[А-Яа-яЁёA-Za-z]+", fragment)
        hits = sorted({w for w in words if len(w) == target}, key=str.lower)
        if len(hits) != 1:
            return None, f"слов длины {target}: {hits}"
        return hits[0].lower(), f"удвоенное слово длины {target}"

    if not quote:
        return None, "не нашёл текст в кавычках"
    listing_raw = quote.group(1)
    listing = re.split(r"\s+[—–-]\s+|\s+относятся\s+", listing_raw)[0]
    # у части заданий перечень предваряется заголовком: «Школьные предметы: ОБЖ, …»
    if ":" in listing:
        listing = listing.split(":", 1)[1]

    # Список или связный текст? По структуре, а не по слову «список» в условии:
    # у перечисления три и более элемента через запятую, и каждый — одно-два слова.
    # У стихотворной цитаты («Мой дядя самых честных правил, Когда не в шутку…»)
    # запятая тоже есть, но элементы длинные — это текст, и уходит один пробел.
    items = [w.strip() for w in listing.split(",") if w.strip()]
    is_listing = len(items) >= 3 and all(len(i.split()) <= 2 for i in items)

    # вычеркнуто слово ИЗ СПИСКА: уходит слово и разделитель «, » (два символа)
    if is_listing:
        target = diff_chars - 2
        hits = sorted({w for w in items if len(w) == target})
        if len(hits) != 1:
            return None, f"слов длины {target}: {hits} (список из {len(items)})"
        return hits[0], f"единственное слово длины {target} в списке"

    # вычеркнуто слово ИЗ ТЕКСТА: уходит слово и один пробел
    target = diff_chars - 1
    words = re.findall(r"[А-Яа-яЁёA-Za-z]+", listing_raw)
    hits = sorted({w for w in words if len(w) == target}, key=str.lower)
    if len(hits) != 1:
        # запасной разбор: вдруг это всё-таки список с разделителем «, »
        alt = sorted({w.strip() for w in listing.split(",")
                      if len(w.strip()) == diff_chars - 2})
        if len(alt) == 1:
            return alt[0], f"слово длины {diff_chars - 2} в списке"
        return None, f"слов длины {target}: {hits}"
    return hits[0].lower(), f"единственное слово длины {target} в тексте"


# --------------------------------------------------------- t3: логика о числе X

def _logic_predicate(expr: str) -> str:
    text = expr.replace("≤", "<=").replace("≥", ">=")
    # «нечётн…» — первым, иначе правило для «чётн…» найдёт его внутри слова
    text = re.sub(r"нечётн\w*|нечетн\w*", "@ODD@", text)
    text = re.sub(r"чётн\w*|четн\w*", "@EVEN@", text)
    text = re.sub(r"кратно\s*(\d+)", r"@MULT\1@", text)
    text = re.sub(r"Первая цифра", "@FIRST@", text, flags=re.I)
    text = re.sub(r"Последняя цифра", "@LAST@", text, flags=re.I)
    text = re.sub(r"Сумма цифр", "@DIGSUM@", text, flags=re.I)
    text = re.sub(r"\b[XxХх]\b", "n", text)
    text = re.sub(r"\bЧисло\b", "n", text, flags=re.I)
    text = re.sub(r"\bНЕ\b", " not ", text)
    text = re.sub(r"\bИЛИ\b", " or ", text)
    text = re.sub(r"\bИ\b", " and ", text)
    text = text.replace("@EVEN@", "% 2 == 0").replace("@ODD@", "% 2 == 1")
    text = re.sub(r"@MULT(\d+)@", r"% \1 == 0", text)
    for marker, code in (("@FIRST@", "int(str(abs(n))[0])"),
                         ("@LAST@", "int(str(abs(n))[-1])"),
                         ("@DIGSUM@", "sum(int(d) for d in str(abs(n)))")):
        text = text.replace(f"{marker} n", code).replace(marker, code)
    return _norm(text)


SAFE = {"__builtins__": {"int": int, "str": str, "sum": sum, "abs": abs}}


def solve_t3(stem: str) -> tuple[str | None, str]:
    body = _norm(stem.split("Источник:")[0]).split("Ответ запишите")[0].rstrip(".?")
    negate = bool(re.search(r"ложно", body, re.I))

    listed = re.search(r"Дано\s+\S+\s+числ\w*\s*:\s*([\d,\s]+)\.", body)
    if "высказывание" not in body:
        return None, "нет слова «высказывание»"
    expr = body.split("высказывание", 1)[1].lstrip(":").strip().rstrip(".?")
    pred = _logic_predicate(expr)

    if listed:
        domain = [int(x) for x in re.findall(r"\d+", listed.group(1))]
    elif re.search(r"двузначн", body):
        domain = list(range(10, 100))
    elif re.search(r"натуральн", body):
        domain = list(range(1, 1001))
    else:
        domain = list(range(-500, 1001))

    try:
        hits = [n for n in domain if bool(eval(pred, SAFE, {"n": n})) != negate]  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        return None, f"предикат не вычисляется ({exc}): {pred!r}"
    if not hits:
        return None, f"ни одно число не подходит: {pred!r}"
    if re.search(r"количество", body, re.I):
        return str(len(hits)), f"подходящих чисел {len(hits)}"
    if re.search(r"наибольш", body, re.I):
        # множество не ограничено сверху — наибольшего не существует, это дефект
        if not listed and max(hits) == max(domain):
            return None, f"множество не ограничено сверху: {pred!r}"
        return str(max(hits)), "наибольшее подходящее"
    if re.search(r"наименьш", body, re.I):
        if not listed and min(hits) == min(domain):
            return None, f"множество не ограничено снизу: {pred!r}"
        return str(min(hits)), "наименьшее подходящее"
    if len(hits) == 1:
        return str(hits[0]), "единственное подходящее"
    return None, f"подходит {len(hits)} чисел, а спрошено одно"


# ------------------------------------------------- t5: исполнитель, параметр b

def _make_command(text: str) -> Callable[[int, int], int | None] | None:
    """Превратить описание команды в функцию (значение, b) -> значение."""
    text = _norm(text).lower()
    if "возведи в квадрат" in text:
        return lambda x, b: x * x
    m = re.search(r"прибавь\s+(\d+)", text)
    if m:
        step = int(m.group(1))
        return lambda x, b, step=step: x + step
    if "прибавь b" in text:
        return lambda x, b: x + b
    m = re.search(r"вычти\s+(\d+)", text)
    if m:
        step = int(m.group(1))
        return lambda x, b, step=step: x - step
    if "вычти b" in text:
        return lambda x, b: x - b
    m = re.search(r"умножь на\s+(\d+)", text)
    if m:
        factor = int(m.group(1))
        return lambda x, b, factor=factor: x * factor
    if "умножь на b" in text:
        return lambda x, b: x * b
    m = re.search(r"раздели на\s+(\d+)", text)
    if m:
        div = int(m.group(1))
        return lambda x, b, div=div: x // div if x % div == 0 else None
    if "раздели на b" in text:
        return lambda x, b: x // b if b and x % b == 0 else None
    return None


def solve_t5(stem: str) -> tuple[str | None, str]:
    body = _norm(stem.split("Источник:")[0])
    cmds = re.search(r"1\)\s*(.+?);\s*2\)\s*(.+?)(?:\.|\(|Программа)", body)
    run = re.search(r"Программа\s+([12]+)\s+переводит число\s+(\d+)\s+в число\s+(\d+)",
                    body)
    if not (cmds and run):
        return None, "не разобрал команды или программу"
    first = _make_command(cmds.group(1))
    second = _make_command(cmds.group(2))
    if first is None or second is None:
        return None, f"неизвестная команда: {cmds.group(1)!r} / {cmds.group(2)!r}"

    program, start, target = run.group(1), int(run.group(2)), int(run.group(3))
    lower = 2 if re.search(r"b\s*(?:≥|>=)\s*2", body) else 1
    hits = []
    for b in range(lower, 501):
        value: int | None = start
        for step in program:
            if value is None:
                break
            value = (first if step == "1" else second)(value, b)
        if value == target:
            hits.append(b)
    if len(hits) != 1:
        return None, f"подходит b: {hits} (программа {program}, {start}->{target})"
    return str(hits[0]), f"программа {program}: {start} -> {target}"


# ------------------------------------------- t6: программа с условием, 9 запусков

def solve_t6(stem: str) -> tuple[str | None, str]:
    body = _norm(stem.split("Источник:")[0])
    pairs = re.findall(r"\((-?\d+)\s*,\s*(-?\d+)\)", body)
    # «напечатали» в обычных заданиях и «напечатает» в заданиях с параметром A
    asked = re.search(r"напечата\w*\s*«([^»]+)»", body)
    if not pairs or not asked:
        return None, "не нашёл пары запусков или искомый вывод"
    # первая пара в тексте — это подпись «(s, t)», а не запуск: она нечисловая,
    # поэтому в findall не попадает; оставшиеся — сами запуски
    runs = [(int(a), int(b)) for a, b in pairs]

    names = re.search(r"\(\s*([stk])\s*,\s*([stk])\s*\)", body)
    var1, var2 = (names.group(1), names.group(2)) if names else ("s", "t")

    cond = re.search(r"if\s+(.+?):\s*print", body)
    if cond:
        expr = cond.group(1)
        true_word = re.search(r"print\(«([^»]+)»\)", body)
        true_value = true_word.group(1) if true_word else "YES"
    else:
        # словесная запись: «если (s > 10) ИЛИ (t > A), печатается «YES»»
        cond = re.search(r"если\s+(.+?),\s*печатается", body, re.I)
        if not cond:
            return None, "не нашёл условие программы"
        expr = cond.group(1)
        true_value = "YES"
    expr = (expr.replace("ИЛИ", " or ").replace(" И ", " and ")
            .replace("«", "").replace("»", ""))

    wanted_true = asked.group(1) == true_value
    has_param = re.search(r"\bA\b", expr) is not None

    def count(param: int | None) -> int | None:
        total = 0
        for value1, value2 in runs:
            scope = {var1: value1, var2: value2}
            if param is not None:
                scope["A"] = param
            try:
                ok = bool(eval(expr, SAFE, scope))  # noqa: S307
            except Exception:  # noqa: BLE001
                return None
            if ok == wanted_true:
                total += 1
        return total

    if not has_param:
        result = count(None)
        if result is None:
            return None, f"условие не вычисляется: {expr!r}"
        return str(result), f"{len(runs)} запусков, условие {expr!r}"

    # задания с параметром A: «сколько значений A» либо «наименьшее A»
    target = re.search(r"(?:ровно|)\s*(\w+)\s+раз", body)
    words = {"один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
             "шесть": 6, "семь": 7, "восемь": 8, "девять": 9}
    if not target:
        return None, "не нашёл требуемое число печатей"
    key = target.group(1).lower()
    need = words.get(key, int(key) if key.isdigit() else None)
    if need is None:
        return None, f"не понял требуемое число печатей: {target.group(1)!r}"

    good = [a for a in range(-100, 101) if count(a) == need]
    if not good:
        return None, f"нет A, дающих {need} печатей"
    if re.search(r"наименьшее", body, re.I):
        return str(min(good)), f"A от {min(good)}, всего подходящих {len(good)}"
    if re.search(r"количество целых значений", body, re.I):
        return str(len(good)), f"подходящих A: {good[:6]}{'…' if len(good) > 6 else ''}"
    return None, "не понял, что спрашивают про A"


# ------------------------------------------------ t8: язык поисковых запросов

def _parse_query(text: str) -> tuple | None:
    """Разобрать выражение запроса в каноническую форму.

    Формы: одиночный запрос, «A & B», «A | B», «A & B & C», «A & (B | C)»,
    «(A | B) & C», «A | B | C».
    """
    text = _norm(text).replace("«", "").replace("»", "").replace('"', "")
    text = text.strip(" .,;:?")
    if not text:
        return None

    outer = re.fullmatch(r"([\w-]+)\s*&\s*\(\s*([\w-]+)\s*\|\s*([\w-]+)\s*\)", text)
    if outer:
        a, b, c = (g.lower() for g in outer.groups())
        return ("and_or", a, frozenset({b, c}))
    outer = re.fullmatch(r"\(\s*([\w-]+)\s*\|\s*([\w-]+)\s*\)\s*&\s*([\w-]+)", text)
    if outer:
        a, b, c = (g.lower() for g in outer.groups())
        return ("or_and", frozenset({a, b}), c)

    if "(" in text or ")" in text:
        return None
    if "&" in text and "|" in text:
        return None
    if "&" in text:
        terms = [t.strip().lower() for t in text.split("&") if t.strip()]
        if len(terms) == 2:
            return ("and", frozenset(terms))
        if len(terms) == 3:
            return ("and3", frozenset(terms))
        return None
    if "|" in text:
        terms = [t.strip().lower() for t in text.split("|") if t.strip()]
        if len(terms) == 2:
            return ("or", frozenset(terms))
        if len(terms) == 3:
            return ("or3", frozenset(terms))
        return None
    if re.fullmatch(r"[\w-]+", text):
        return ("single", text.lower())
    return None


def _derive(known: dict) -> None:
    """Доопределить величины по тождествам включения-исключения (до замыкания).

    |A ∪ B| = |A| + |B| − |A ∩ B|
    |A ∩ (B ∪ C)| = |A∩B| + |A∩C| − |A∩B∩C|
    |(A ∪ B) ∩ C| = |A∩C| + |B∩C| − |A∩B∩C|
    |A ∪ B ∪ C| = |A ∪ B| + |C| − |(A ∪ B) ∩ C|
    """
    changed = True
    while changed:
        changed = False

        def put(key, value):
            nonlocal changed
            if key not in known and value is not None:
                known[key] = value
                changed = True

        names = {n for k in known for n in (k[1] if k[0] != "single" else [k[1]])
                 if isinstance(n, str)}
        names |= {n for k in known if k[0] == "and_or" for n in (k[1],)}
        names |= {n for k in known if k[0] == "or_and" for n in (k[2],)}

        for key, value in list(known.items()):
            kind = key[0]
            if kind in ("and", "or"):
                pair = key[1]
                a, b = sorted(pair)
                other = ("or", pair) if kind == "and" else ("and", pair)
                sa, sb = known.get(("single", a)), known.get(("single", b))
                if sa is not None and sb is not None:
                    put(other, sa + sb - value)
                # одиночная величина: |B| = |A ∪ B| - |A| + |A ∩ B|
                union = known.get(("or", pair))
                inter = known.get(("and", pair))
                if union is not None and inter is not None:
                    if sa is not None:
                        put(("single", b), union - sa + inter)
                    if sb is not None:
                        put(("single", a), union - sb + inter)
            if kind == "and_or":
                a, pair = key[1], key[2]
                b, c = sorted(pair)
                ab = known.get(("and", frozenset({a, b})))
                ac = known.get(("and", frozenset({a, c})))
                abc = known.get(("and3", frozenset({a, b, c})))
                if ab is not None and abc is not None:
                    put(("and", frozenset({a, c})), value - ab + abc)
                if ac is not None and abc is not None:
                    put(("and", frozenset({a, b})), value - ac + abc)
                if ab is not None and ac is not None:
                    put(("and3", frozenset({a, b, c})), ab + ac - value)
            if kind == "or_and":
                pair, c = key[1], key[2]
                a, b = sorted(pair)
                ac = known.get(("and", frozenset({a, c})))
                bc = known.get(("and", frozenset({b, c})))
                abc = known.get(("and3", frozenset({a, b, c})))
                if ac is not None and bc is not None:
                    put(("and3", frozenset({a, b, c})), ac + bc - value)
                if ac is not None and abc is not None:
                    put(("and", frozenset({b, c})), value - ac + abc)

        # Пустое пересечение пары влечёт пустое тройное: A∩B∩C ⊆ A∩B.
        # Без этого не решаются задания, где |A∩B| выводится нулём из |A∪B|.
        for key, value in list(known.items()):
            if key[0] == "and" and value == 0:
                a, b = sorted(key[1])
                for c in {n for k in known if k[0] == "single" for n in (k[1],)}:
                    if c not in (a, b):
                        put(("and3", frozenset({a, b, c})), 0)

        # из троек выводим составные величины
        for key, value in list(known.items()):
            if key[0] != "and3":
                continue
            trio = key[1]
            for a in trio:
                b, c = sorted(trio - {a})
                ab = known.get(("and", frozenset({a, b})))
                ac = known.get(("and", frozenset({a, c})))
                if ab is not None and ac is not None:
                    put(("and_or", a, frozenset({b, c})), ab + ac - value)
                bc = known.get(("and", frozenset({b, c})))
                if bc is not None and ac is not None:
                    put(("or_and", frozenset({b, a}), c), bc + ac - value)
        # |(X ∪ Y) ∩ Z| = |X∩Z| + |Y∩Z| − |X∩Y∩Z| — прямо из тройки и пар
        for key, value in list(known.items()):
            if key[0] != "and3":
                continue
            trio = sorted(key[1])
            for z in trio:
                x, y = [t for t in trio if t != z]
                xz = known.get(("and", frozenset({x, z})))
                yz = known.get(("and", frozenset({y, z})))
                if xz is not None and yz is not None:
                    put(("or_and", frozenset({x, y}), z), xz + yz - value)
                    put(("and_or", z, frozenset({x, y})), xz + yz - value)

        # тройное объединение
        for key, value in list(known.items()):
            if key[0] != "or":
                continue
            a, b = sorted(key[1])
            for c in {n for k in known if k[0] == "single" for n in (k[1],)} - {a, b}:
                inter = known.get(("or_and", frozenset({a, b}), c))
                sc = known.get(("single", c))
                if inter is not None and sc is not None:
                    put(("or3", frozenset({a, b, c})), value + sc - inter)


def solve_t8(stem: str) -> tuple[str | None, str]:
    """Язык поисковых запросов: считает по включениям-исключениям."""
    body = _norm(stem.split("Источник:")[0])
    question = re.search(r"(?:по запросу|Сколько сайтов по запросу)\s*(.+?)\s*[?.]",
                         body)
    # «…(Сканер | Принтер) & Монитор, если Сканер | Принтер = 450; …» — условие
    # перечислено ПОСЛЕ вопроса, сам запрос кончается на «, если»
    if question and ", если" in question.group(1):
        head = question.group(1).split(", если")[0]
        tail = question.group(1)[len(head):]
        body = body.replace(question.group(1), head + " . " + tail)
        question = re.search(r"(?:по запросу|Сколько сайтов по запросу)\s*(.+?)\s*[?.]",
                             body)
    # часть с данными — до вопроса
    data_part = body[:question.start()] if question else body
    for marker in ("Дана таблица", "Даны запросы", "Даны результаты", "В таблице",
                   "Дано:", "Сегмент сети"):
        if marker in data_part:
            data_part = data_part[data_part.index(marker):]
            break

    # Данные обычно идут до вопроса, но бывает и «…, если A | B = 450; …» после него.
    # Если из головной части ничего не набралось, разбираем весь текст.
    known: dict = {}
    # Берём каждое «<выражение> <разделитель> <число>». Выражение обрезаем слева по
    # последнему разделителю фразы — иначе в имя утекает хвост соседней записи
    # («страниц, Швеция» вместо «Швеция»).
    for raw_expr, number in re.finditer(
        r"([^;:.]{2,60}?)\s*(?:=|—|–|-|найдено)\s*(\d+)", data_part
    ).__iter__() and [(m.group(1), m.group(2)) for m in re.finditer(
        r"([^;:.]{2,60}?)\s*(?:=|—|–|-|найдено)\s*(\d+)", data_part)]:
        # strip ДО отсечения преамбулы: иначе ведущий пробел не даёт якорю ^ сработать
        expr = re.split(r",\s*|:\s*", raw_expr)[-1].strip()
        expr = re.sub(r"^(?:Дано|Даны запросы|Даны результаты|В таблице|Дана таблица|"
                      r"По запросу|Сколько сайтов по запросу|"
                      r"Сегмент сети из \d+ сайтов)\s*:?\s*", "",
                      expr, flags=re.I).strip()
        parsed = _parse_query(expr)
        if parsed is not None:
            known.setdefault(parsed, int(number))

    if not any(k[0] in ("and", "or") for k in known):
        for raw_expr, number in [(m.group(1), m.group(2)) for m in re.finditer(
            r"([^;:.]{2,60}?)\s*(?:=|—|–|-|найдено)\s*(\d+)", body)]:
            expr = re.split(r",\s*|:\s*|если\s+", raw_expr)[-1].strip()
            parsed = _parse_query(expr)
            if parsed is not None:
                known.setdefault(parsed, int(number))

    if not question:
        return None, "не нашёл искомый запрос"
    asked = _parse_query(question.group(1))
    if asked is None:
        return None, f"не разобрал запрос: {question.group(1)!r}"

    _derive(known)
    if asked in known:
        return str(known[asked]), f"известных величин: {len(known)}"
    return None, (f"не вывелось: спрошено {asked}, известно "
                  f"{sorted(str(k) for k in known)[:4]}")


# --------------------------------------------------------- t10: системы счисления

def _to_base(number: int, base: int) -> str:
    if number == 0:
        return "0"
    digits = "0123456789ABCDEF"
    out = ""
    while number:
        out = digits[number % base] + out
        number //= base
    return out


def solve_t10(stem: str) -> tuple[str | None, str]:
    body = _norm(stem.split("Источник:")[0])
    bases = {"двоичн": 2, "восьмеричн": 8, "шестнадцатеричн": 16, "десятичн": 10}

    if "арифметического выражения" in body:
        parts = re.findall(r"([0-9A-Fa-f]+)\s*\(\s*в\s+(\w+)", body)
        if not parts:
            return None, "не нашёл слагаемых"
        total, pieces = 0, []
        for value, base_word in parts:
            base = next((b for key, b in bases.items() if base_word.startswith(key)), None)
            if base is None:
                return None, f"неизвестная система: {base_word}"
            total += int(value, base)
            pieces.append(f"{value}(осн.{base})")
        return str(total), " + ".join(pieces)

    if "Среди чисел" in body:
        nums = [int(x) for x in re.findall(r"\d+", body.split("(")[0])]
        if not nums:
            return None, "не нашёл перечня чисел"
        if "сумма цифр" in body:
            base = 8 if "восьмеричн" in body else 2
            values = {n: sum(int(d, base) for d in _to_base(n, base)) for n in nums}
        else:
            values = {n: _to_base(n, 2).count("1") for n in nums}
        best = min(values.values()) if "наименьш" in body else max(values.values())
        return str(best), f"{values} -> {best}"

    if "Сколько единиц" in body:
        m = re.search(r"число\s+(\d+)\s+из десятичной", body)
        if not m:
            return None, "не нашёл исходное число"
        return str(_to_base(int(m.group(1)), 2).count("1")), f"{m.group(1)} в двоичной"

    m = re.search(r"(?:число|записывается как)\s+([0-9A-Fa-f]+)", body)
    if not m:
        return None, "не нашёл число для перевода"
    from_base = 10 if "из десятичной" in body else 2
    to_base = 2 if "в двоичную" in body else 10
    number = int(m.group(1), from_base)
    result = _to_base(number, to_base) if to_base != 10 else str(number)
    return result, f"{m.group(1)} (осн.{from_base}) -> осн.{to_base}"


SOLVERS: dict[str, Callable[[str], tuple[str | None, str]]] = {
    "t1": solve_t1, "t3": solve_t3, "t5": solve_t5,
    "t6": solve_t6, "t8": solve_t8, "t10": solve_t10,
}


def dsn(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=sorted(SOLVERS), help="только одна партия")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    conn = psycopg2.connect(dsn())
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    total_mismatch = 0
    for batch in ([args.batch] if args.batch else sorted(SOLVERS, key=lambda b: int(b[1:]))):
        cur.execute(
            """
            SELECT id, replace(task_content->>'stem', E'\n', ' ') AS stem,
                   solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon
            FROM tasks WHERE external_uid LIKE %s AND is_active ORDER BY id
            """,
            (f"oge:reshu:{batch}:%",),
        )
        rows = cur.fetchall()
        ok, mismatch, unparsed = 0, [], []
        for row in rows:
            try:
                answer, note = SOLVERS[batch](row["stem"])
            except Exception as exc:  # noqa: BLE001
                unparsed.append((row["id"], f"сбой решателя: {exc}"))
                continue
            if answer is None:
                unparsed.append((row["id"], note))
            elif answer.strip().lower() == (row["etalon"] or "").strip().lower():
                ok += 1
            else:
                mismatch.append((row["id"], row["etalon"], answer, note, row["stem"]))

        total_mismatch += len(mismatch)
        logger.info("\n=== %s: заданий %d, сошлось %d, расхождений %d, не разобрано %d ===",
                    batch, len(rows), ok, len(mismatch), len(unparsed))
        for tid, etalon, answer, note, stem in mismatch:
            logger.info("  ХХХ [%s] эталон=%s посчитано=%s | %s", tid, etalon, answer, note)
            logger.info("        %s", _norm(stem)[:200])
        for tid, note in unparsed:
            logger.info("  ??  [%s] %s", tid, note)

    conn.close()
    logger.info("\nВсего расхождений: %d", total_mismatch)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
