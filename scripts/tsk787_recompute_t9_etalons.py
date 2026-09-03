# -*- coding: utf-8 -*-
"""tsk-787: независимый пересчёт эталонов заданий типа 9 (курс 160) по файлу условия.

Зачем. Сверка эталона с чистым `div.answer` источника (см.
``tsk787_verify_sdamgia_etalons.py``) ловит дефекты РАЗБОРА, но верит источнику.
Здесь эталон проверяется сильнее: скачиваем приложенную к заданию таблицу и считаем
ответ заново по условию. Повод — tsk-787: преподаватель не поверил эталону «— 2640»,
и прежде чем объявлять эталоны верными, надо убедиться, что верить было можно.

Что считается. Все задания курса 160 — тип 9 ЕГЭ («откройте файл электронной
таблицы»), и условие каждого формализуется предикатом на строку таблицы. Предикаты
переписаны с условий вручную, по одному на задание, и НАМЕРЕННО не выведены из
решения источника: иначе пересчёт повторил бы его ошибку, если она есть.

Читаем .ods своим разбором (zip + content.xml): odfpy в окружении нет, а
единственное, что нужно от формата, — числа по строкам.

Read-only: скрипт ничего не пишет в базу, только скачивает файлы в кэш и считает.

Запуск::

    python scripts/tsk787_recompute_t9_etalons.py
    python scripts/tsk787_recompute_t9_etalons.py --task 2223
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Callable

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk787-recompute")

MEDIA_BASE = "https://api.learn.victor-komlev.ru/api/v1/media/"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".qa-artifacts" / "tsk787-t9-files"

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

Row = list[int]


def read_ods_rows(path: Path) -> list[Row]:
    """Строки чисел из первого листа .ods.

    Пустые строки и хвостовые пустые ячейки отбрасываются: ODF хранит прямоугольник
    с запасом, и без этого в конце появились бы фантомные строки.
    """
    with zipfile.ZipFile(path) as archive:
        content = archive.read("content.xml")
    root = ET.fromstring(content)
    rows: list[Row] = []
    for row_el in root.iter(f"{{{_TABLE_NS}}}table-row"):
        values: list[int] = []
        for cell in row_el.findall(f"{{{_TABLE_NS}}}table-cell"):
            repeat = int(cell.get(f"{{{_TABLE_NS}}}number-columns-repeated", "1"))
            raw = cell.get(f"{{{_OFFICE_NS}}}value")
            if raw is None:
                text_el = cell.find(f"{{{_TEXT_NS}}}p")
                raw = (text_el.text or "").strip() if text_el is not None else ""
            raw = (raw or "").strip().replace(",", ".")
            if not raw:
                # Пустая ячейка: если дальше в строке чисел нет, повтор — это хвост.
                if repeat > 100:
                    break
                continue
            try:
                number = int(float(raw))
            except ValueError:
                continue
            values.extend([number] * repeat)
        if values:
            rows.append(values)
    return rows


# --- предикаты условий (по одному на задание) ------------------------------------

def _mean(values: list[int]) -> float:
    return sum(values) / len(values)


def _repeated_and_unique(row: Row) -> tuple[list[int], list[int]]:
    """Числа строки с повторами и без, КАЖДОЕ столько раз, сколько встречается."""
    counts = Counter(row)
    repeated = [v for v in row if counts[v] > 1]
    unique = [v for v in row if counts[v] == 1]
    return repeated, unique


def t2211(row: Row) -> bool:
    """Два числа повторяются дважды, остальные различны; среднее повторяющихся
    меньше среднего всех чисел строки."""
    counts = Counter(row)
    twice = [v for v, n in counts.items() if n == 2]
    if len(twice) != 2 or any(n > 2 for n in counts.values()):
        return False
    repeated = [v for v in row if counts[v] > 1]
    return _mean(repeated) < _mean(row)


def t2212(row: Row) -> bool:
    """Есть и повторяющиеся, и неповторяющиеся; среднее неповторяющихся МЕНЬШЕ
    среднего повторяющихся."""
    repeated, unique = _repeated_and_unique(row)
    if not repeated or not unique:
        return False
    return _mean(unique) < _mean(repeated)


def t2213(row: Row) -> bool:
    """Минимум ровно один раз; хотя бы одно число повторяется; максимум больше
    среднего остальных пяти более чем в три раза."""
    counts = Counter(row)
    if counts[min(row)] != 1:
        return False
    if not any(n > 1 for n in counts.values()):
        return False
    top = max(row)
    rest = list(row)
    rest.remove(top)
    return top > 3 * _mean(rest)


def t2214(row: Row) -> bool:
    """Тройка может быть сторонами треугольника (строгое неравенство)."""
    a, b, c = sorted(row)
    return a + b > c


def t2215(row: Row) -> bool:
    """Ровно одно число трижды, остальные без повторений; квадрат суммы
    повторяющихся больше квадрата суммы неповторяющихся."""
    counts = Counter(row)
    thrice = [v for v, n in counts.items() if n == 3]
    if len(thrice) != 1 or any(n not in (1, 3) for n in counts.values()):
        return False
    repeated, unique = _repeated_and_unique(row)
    return sum(repeated) ** 2 > sum(unique) ** 2


def t2216(row: Row) -> bool:
    """Все числа различны; чётных больше нечётных; сумма чётных меньше суммы нечётных."""
    if len(set(row)) != len(row):
        return False
    even = [v for v in row if v % 2 == 0]
    odd = [v for v in row if v % 2 != 0]
    return len(even) > len(odd) and sum(even) < sum(odd)


def t2218(row: Row) -> bool:
    """Из четвёрки можно выбрать три числа, которые не могут быть сторонами никакого
    треугольника, В ТОМ ЧИСЛЕ вырожденного.

    Здесь легко ошибиться на границе, и первый прогон tsk-787 на ней и ошибся: с
    условием ``a + b <= c`` пересчёт дал 3158 против эталона 3094. Оговорка «в том
    числе вырожденного» РАСШИРЯЕТ набор треугольников, которыми тройка могла бы
    быть, а не набор запрещённых: при ``a + b == c`` тройка как раз образует
    вырожденный треугольник, то есть условию не подходит. Остаётся строгое
    ``a + b < c``, и на нём пересчёт сходится с эталоном — то есть неверным было
    прочтение условия, а не эталон.
    """
    return any(a + b < c for a, b, c in (sorted(t) for t in combinations(row, 3)))


def t2219(row: Row) -> bool:
    """Есть число, повторяющееся >= 3 раз; есть неповторяющееся; среднее
    повторяющихся (с учётом повторов) БОЛЬШЕ среднего неповторяющихся."""
    counts = Counter(row)
    if not any(n >= 3 for n in counts.values()):
        return False
    repeated, unique = _repeated_and_unique(row)
    if not unique:
        return False
    return _mean(repeated) > _mean(unique)


def t2220(row: Row) -> bool:
    """Хотя бы одно: квадрат наибольшего больше произведения трёх других; либо
    упорядоченные числа образуют арифметическую прогрессию."""
    ordered = sorted(row)
    top = ordered[-1]
    others = ordered[:-1]
    if top ** 2 > others[0] * others[1] * others[2]:
        return True
    steps = {ordered[i + 1] - ordered[i] for i in range(3)}
    return len(steps) == 1


def t2222(row: Row) -> bool:
    """Тройка может быть сторонами ОСТРОугольного треугольника."""
    a, b, c = sorted(row)
    return a + b > c and a * a + b * b > c * c


def t2223(row: Row) -> bool:
    """Квадрат суммы максимального и минимального больше суммы квадратов трёх
    оставшихся. Задание из инцидента tsk-787."""
    ordered = sorted(row)
    return (ordered[0] + ordered[-1]) ** 2 > sum(v * v for v in ordered[1:-1])


def t2224(row: Row) -> bool:
    """То же, что 2215 (условие переформулировано, смысл тот же)."""
    return t2215(row)


def t2225(row: Row) -> bool:
    """Все различны; нечётных больше чётных; сумма нечётных меньше суммы чётных."""
    if len(set(row)) != len(row):
        return False
    even = [v for v in row if v % 2 == 0]
    odd = [v for v in row if v % 2 != 0]
    return len(odd) > len(even) and sum(odd) < sum(even)


def t2227(row: Row) -> bool:
    """Как 2219, но среднее повторяющихся МЕНЬШЕ среднего неповторяющихся."""
    counts = Counter(row)
    if not any(n >= 3 for n in counts.values()):
        return False
    repeated, unique = _repeated_and_unique(row)
    if not unique:
        return False
    return _mean(repeated) < _mean(unique)


def t2228(row: Row) -> bool:
    """ЛЮБЫЕ три числа четвёрки образуют невырожденный треугольник."""
    return all(a + b > c for a, b, c in (sorted(t) for t in combinations(row, 3)))


def t2229(row: Row) -> bool:
    """Есть повторяющиеся; максимум не повторяется; сумма повторяющихся (с учётом
    повторов) больше максимума строки."""
    counts = Counter(row)
    repeated = [v for v in row if counts[v] > 1]
    if not repeated:
        return False
    top = max(row)
    if counts[top] > 1:
        return False
    return sum(repeated) > top


def t2230(row: Row) -> bool:
    """Наибольшее меньше суммы трёх других; четыре числа разбиваются на две пары
    с равными суммами."""
    ordered = sorted(row)
    if ordered[-1] >= sum(ordered[:-1]):
        return False
    a, b, c, d = ordered
    return a + d == b + c or a + b == c + d or a + c == b + d


def t2233(row: Row) -> bool:
    """Как 2212, но среднее неповторяющихся БОЛЬШЕ среднего повторяющихся."""
    repeated, unique = _repeated_and_unique(row)
    if not repeated or not unique:
        return False
    return _mean(unique) > _mean(repeated)


#: Задания, где ответ — число строк, удовлетворяющих предикату.
ROW_PREDICATES: dict[int, Callable[[Row], bool]] = {
    2211: t2211, 2212: t2212, 2213: t2213, 2214: t2214, 2215: t2215,
    2216: t2216, 2218: t2218, 2219: t2219, 2220: t2220, 2222: t2222,
    2223: t2223, 2224: t2224, 2225: t2225, 2227: t2227, 2228: t2228,
    2229: t2229, 2230: t2230, 2233: t2233,
}


def count_rows_with_interesting_cell(rows: list[Row], min_in_column: int) -> int:
    """2226: строки с РОВНО ОДНОЙ «интересной» ячейкой.

    Интересная ячейка: число не встречается в других ячейках своей строки, встречается
    не менее ``min_in_column`` раз в других ячейках своего СТОЛБЦА и больше среднего
    арифметического всех чисел строки.
    """
    width = max(len(row) for row in rows)
    columns = [Counter(row[i] for row in rows if i < len(row)) for i in range(width)]
    total = 0
    for row in rows:
        row_counts = Counter(row)
        mean = _mean(row)
        interesting = 0
        for i, value in enumerate(row):
            if row_counts[value] > 1:
                continue
            if columns[i][value] - 1 < min_in_column:
                continue
            if value > mean:
                interesting += 1
        if interesting == 1:
            total += 1
    return total


def count_rows_with_good_cell(rows: list[Row], times_in_other_rows: int) -> int:
    """2232: строки, содержащие хотя бы одну «хорошую» ячейку.

    Хорошая ячейка: число не встречается в других ячейках своей строки и ровно
    ``times_in_other_rows`` раз встречается в ДРУГИХ строках таблицы.
    """
    overall = Counter(value for row in rows for value in row)
    total = 0
    for row in rows:
        row_counts = Counter(row)
        for value in row:
            if row_counts[value] > 1:
                continue
            if overall[value] - row_counts[value] == times_in_other_rows:
                total += 1
                break
    return total


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def fetch_media(name: str) -> Path:
    """Файл условия из публичного /media, с кэшем на диске."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return path
    with urllib.request.urlopen(MEDIA_BASE + name, timeout=60) as response:
        path.write_bytes(response.read())
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, action="append",
                        help="считать только эти задания (по умолчанию все)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    wanted = set(args.task or []) or set(ROW_PREDICATES) | {2226, 2232}

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
            (sorted(wanted),),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    agree, disagree, skipped = 0, [], []
    for record in rows:
        task_id = record["id"]
        if not record["media"]:
            skipped.append((task_id, "нет файла условия"))
            continue
        try:
            table = read_ods_rows(fetch_media(record["media"]))
        except Exception as exc:  # noqa: BLE001 — причина обязана попасть в отчёт
            skipped.append((task_id, f"файл не прочитан: {exc}"))
            continue
        if not table:
            skipped.append((task_id, "в файле не нашлось строк с числами"))
            continue

        if task_id == 2226:
            computed = count_rows_with_interesting_cell(table, 330)
        elif task_id == 2232:
            computed = count_rows_with_good_cell(table, 45)
        else:
            predicate = ROW_PREDICATES[task_id]
            computed = sum(1 for row in table if predicate(row))

        etalon = (record["etalon"] or "").strip()
        mark = "OK " if etalon == str(computed) else "РАСХОЖДЕНИЕ"
        logger.info("%s [%s] строк %d: эталон %r, пересчёт %d  — %s",
                    mark, task_id, len(table), etalon, computed,
                    (record["title"] or "")[:45])
        if etalon == str(computed):
            agree += 1
        else:
            disagree.append((task_id, etalon, computed))

    logger.info("\nСошлось: %d, расходится: %d, пропущено: %d",
                agree, len(disagree), len(skipped))
    for task_id, why in skipped:
        logger.info("  пропуск [%s]: %s", task_id, why)
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
