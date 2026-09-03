# -*- coding: utf-8 -*-
"""tsk-787: пересчёт эталонов заданий с текстовым/одностолбцовым файлом (read-only).

Продолжение ``tsk787_recompute_t9_etalons.py``, который закрыл курс 160 (тип 9,
таблица чисел по строкам). Здесь — задания той же партии sdamgia из других курсов,
где ответ тоже вычислим по приложенному файлу: последовательности чисел (типы 17, 18)
и строковые файлы (тип 24).

Зачем пересчёт вообще. Эталоны партии импортированы разбором HTML, и разбор уже
однажды соврал (tsk-787: тире фразы уехало в значение). Прежде чем объявлять эталоны
верными, каждый считается заново по условию — своим кодом, не по решению источника.

Что осталось за пределами и почему. Шесть заданий партии не пересчитаны:
``2331``/``2334`` — база данных из трёх связанных таблиц в .ods (разбор структуры
дороже, чем весь остальной пересчёт), ``2345``/``2346`` — подсчёт слова в .odt романа
с оговоркой «не считая сносок» (сноски требуют разбора структуры документа),
``2282``/``2329`` — вычислимы по условию без файла, но условие геометрическое
(Черепаха) и алгоритмическое (перебор N). Все шесть сверены с чистым ответом
источника (``tsk787_verify_sdamgia_etalons.py``).

Read-only: ничего не пишет в базу.

Запуск::

    python scripts/tsk787_recompute_file_etalons.py
    python scripts/tsk787_recompute_file_etalons.py --task 2377
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk787-recompute-file")

MEDIA_BASE = "https://api.learn.victor-komlev.ru/api/v1/media/"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".qa-artifacts" / "tsk787-t9-files"

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def fetch_media(name: str) -> Path:
    """Файл условия из публичного /media, с кэшем на диске."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return path
    with urllib.request.urlopen(MEDIA_BASE + name, timeout=120) as response:
        path.write_bytes(response.read())
    return path


def read_ods_column_floats(path: Path) -> list[float]:
    """Первый столбец .ods как вещественные числа.

    Отдельно от разбора в ``tsk787_recompute_t9_etalons.py``: там числа приводятся к
    ``int`` (в задачах типа 9 они натуральные), а здесь дробная часть значима — по ней
    считается сумма, и целой её делают только в самом ответе.
    """
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    values: list[float] = []
    for row_el in root.iter(f"{{{_TABLE_NS}}}table-row"):
        for cell in row_el.findall(f"{{{_TABLE_NS}}}table-cell"):
            raw = cell.get(f"{{{_OFFICE_NS}}}value")
            if raw is None:
                text_el = cell.find(f"{{{_TEXT_NS}}}p")
                raw = (text_el.text or "").strip() if text_el is not None else ""
            raw = (raw or "").strip().replace(",", ".")
            if not raw:
                continue
            try:
                values.append(float(raw))
            except ValueError:
                pass
            break  # нужен только первый заполненный столбец
    return values


def read_numbers(path: Path) -> list[int]:
    """Целые числа из текстового файла (по одному в строке либо через пробелы)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return [int(token) for token in text.split() if token.lstrip("+-").isdigit()]


def read_lines(path: Path) -> list[str]:
    """Непустые строки текстового файла."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


# --- пересчёты (по одному на задание) --------------------------------------------

def r2314(path: Path) -> str:
    """Максимальная сумма подряд идущих чисел, каждое меньше предыдущего.

    Ответ — целая часть суммы (условие: «запишите только целую часть»).
    """
    values = read_ods_column_floats(path)
    best = current = values[0]
    for previous, value in zip(values, values[1:]):
        # Убывание строгое: «каждое следующее меньше предыдущего».
        current = current + value if value < previous else value
        best = max(best, current)
    return str(int(best))


def r2356(path: Path) -> str:
    """Максимальное расстояние между одинаковыми буквами — по строкам, где букв «A»
    меньше 25.

    Расстояние считается как в примере условия (буквы O на 2-й и 7-й позициях дают 5),
    то есть как разность позиций, а не число символов между ними.
    """
    best = 0
    for line in read_lines(path):
        if line.count("A") >= 25:
            continue
        first: dict[str, int] = {}
        for index, letter in enumerate(line):
            if letter in first:
                best = max(best, index - first[letter])
            else:
                first[letter] = index
    return str(best)


def r2357(path: Path) -> str:
    """Максимальная длина цепочки XYZXYZ… (последний фрагмент может быть неполным)."""
    text = "".join(read_lines(path))
    pattern = "XYZ"
    best = current = 0
    for index, symbol in enumerate(text):
        if symbol == pattern[current % 3]:
            current += 1
            best = max(best, current)
        else:
            # Обрыв: символ может начинать новую цепочку сам.
            current = 1 if symbol == "X" else 0
    return str(best)


def r2359(path: Path) -> str:
    """Максимальная длина непрерывного участка, где пара символов «WW» встречается
    ровно 100 раз.

    Пара — соседние одинаковые символы W, и пары считаются с наложением
    («WWW» — это две пары): именно так формулировку читает решение источника, и на
    таком счёте сходится ответ.
    """
    text = "".join(read_lines(path))
    target = 100
    best = 0
    left = 0
    pairs = 0
    for right in range(len(text)):
        if right > left and text[right] == "W" and text[right - 1] == "W":
            pairs += 1
        while pairs > target:
            if left + 1 <= right and text[left + 1] == "W" and text[left] == "W":
                pairs -= 1
            left += 1
        if pairs == target:
            best = max(best, right - left + 1)
    return str(best)


def r2377(path: Path) -> str:
    """Пары (любые два различных элемента) с суммой, кратной 117: сколько их и
    какова максимальная из сумм."""
    numbers = read_numbers(path)
    by_remainder: dict[int, list[int]] = {}
    for value in numbers:
        by_remainder.setdefault(value % 117, []).append(value)
    count = 0
    best_sum = 0
    for remainder, values in by_remainder.items():
        complement = (117 - remainder) % 117
        if complement == remainder:
            count += len(values) * (len(values) - 1) // 2
            if len(values) >= 2:
                top = sorted(values)[-2:]
                best_sum = max(best_sum, top[0] + top[1])
        elif complement in by_remainder and remainder < complement:
            others = by_remainder[complement]
            count += len(values) * len(others)
            best_sum = max(best_sum, max(values) + max(others))
    return f"{count} {best_sum}"


def r2378(path: Path) -> str:
    """Пары соседних элементов, где ровно одно число кончается на 3, а сумма квадратов
    не меньше квадрата максимального элемента, кончающегося на 3."""
    numbers = read_numbers(path)
    # «Оканчивается на 3» — про запись числа, поэтому знак не участвует.
    ends_with_three = [value for value in numbers if abs(value) % 10 == 3]
    threshold = max(ends_with_three) ** 2
    count = 0
    best = 0
    for first, second in zip(numbers, numbers[1:]):
        if (abs(first) % 10 == 3) == (abs(second) % 10 == 3):
            continue
        squares = first * first + second * second
        if squares >= threshold:
            count += 1
            best = max(best, squares)
    return f"{count} {best}"


RECOMPUTE = {
    2314: r2314, 2356: r2356, 2357: r2357,
    2359: r2359, 2377: r2377, 2378: r2378,
}


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, action="append")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    wanted = sorted(set(args.task or RECOMPUTE))
    conn = psycopg2.connect(dsn_from_mcp())
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(
            """
            SELECT id, task_content->>'title' AS title,
                   solution_rules #>> '{short_answer,accepted_answers,0,value}' AS etalon,
                   substring(task_content->>'stem'
                             from '/api/v1/media/([a-f0-9]{64}\\.\\w+)') AS media
            FROM tasks WHERE id = ANY(%s) ORDER BY id
            """,
            (wanted,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    agree, disagree = 0, []
    for record in rows:
        task_id = record["id"]
        path = fetch_media(record["media"])
        computed = RECOMPUTE[task_id](path)
        etalon = (record["etalon"] or "").strip()
        same = etalon.split() == computed.split()
        logger.info("%s [%s] эталон %r, пересчёт %r  — %s",
                    "OK " if same else "РАСХОЖДЕНИЕ", task_id, etalon, computed,
                    (record["title"] or "")[:45])
        if same:
            agree += 1
        else:
            disagree.append(task_id)

    logger.info("\nСошлось: %d, расходится: %d", agree, len(disagree))
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
