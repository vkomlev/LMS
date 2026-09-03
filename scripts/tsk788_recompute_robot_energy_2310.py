# -*- coding: utf-8 -*-
"""tsk-788: пересчёт эталона задания 2310 «Минимальный запас энергии» (read-only).

Зачем. Эталон записан как ``1150 2652`` — два числа, а источник (sdamgia) печатает их
СЛИТНО: ``11502652``. Прежде чем добавлять слитную форму в приём ответа, надо
убедиться, что слитная запись — это действительно склейка двух верных чисел, а не
другой ответ. Пересчёт по приложенной таблице отвечает на это прямо.

Условие (ЕГЭ, задание 18). Робот стоит в левом нижнем углу поля, за ход идёт на клетку
вправо или вверх. В клетках с ``-1`` ходить нельзя. Финальная клетка — та, из которой
допустимого хода нет. Расход энергии на запуск равен числу в стартовой клетке, дальше —
числу в каждой следующей клетке.

  * Задание 1 — минимальный запас, чтобы добраться до **какой-либо** финальной клетки,
    то есть ДЕШЕВЕЙШИЙ маршрут.
  * Задание 2 — минимальный запас, чтобы пройти **любым** допустимым маршрутом, то есть
    САМЫЙ ДОРОГОЙ маршрут (запаса должно хватить на худший случай).

Read-only: ничего не пишет в базу.

Запуск::

    python scripts/tsk788_recompute_robot_energy_2310.py
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk788")

MEDIA_BASE = "https://api.learn.victor-komlev.ru/api/v1/media/"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".qa-artifacts" / "tsk788-files"

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

BLOCKED = -1


def fetch_media(name: str) -> Path:
    """Файл условия из публичного /media, с кэшем на диске."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return path
    with urllib.request.urlopen(MEDIA_BASE + name, timeout=120) as response:
        path.write_bytes(response.read())
    return path


def read_grid(path: Path) -> list[list[int]]:
    """Поле как прямоугольник целых чисел.

    Две вещи, на которых разбор ломается молча:

    * **Пустая ячейка — не запрещённая клетка.** В этом файле лист начинается с пустой
      строки, и у каждой строки пустая первая ячейка: данные лежат со второй колонки.
      Если считать пустоту за ``-1``, левый нижний угол оказывается «запрещённым» — на
      этом первый прогон и упал. Поэтому пустые ячейки читаются как ``None``, пустые
      края обрезаются, а пустота ВНУТРИ поля — повод остановиться, а не догадываться.
    * **Позиция ячейки значима.** В отличие от заданий типа 9, где строка это просто
      набор чисел, здесь пропуск ячейки сдвинул бы поле и сломал соседство клеток,
      поэтому ``number-columns-repeated`` разворачивается как есть (кроме хвоста
      прямоугольника ODF, который тянется до 16384 колонок).
    """
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))

    rows: list[list[int | None]] = []
    for row_el in root.iter(f"{{{_TABLE_NS}}}table-row"):
        values: list[int | None] = []
        for cell in row_el.findall(f"{{{_TABLE_NS}}}table-cell"):
            repeat = int(cell.get(f"{{{_TABLE_NS}}}number-columns-repeated", "1"))
            raw = cell.get(f"{{{_OFFICE_NS}}}value")
            if raw is None:
                text_el = cell.find(f"{{{_TEXT_NS}}}p")
                raw = (text_el.text or "").strip() if text_el is not None else ""
            raw = (raw or "").strip().replace(",", ".")
            if repeat > 100:
                break  # хвост прямоугольника ODF, а не данные
            try:
                values.extend([int(float(raw))] * repeat)
            except ValueError:
                values.extend([None] * repeat)
        rows.append(values)

    filled = [row for row in rows if any(value is not None for value in row)]
    if not filled:
        raise RuntimeError("в файле не нашлось ни одного числа")
    width = max(len(row) for row in filled)
    filled = [row + [None] * (width - len(row)) for row in filled]

    first_col = min(next(i for i, v in enumerate(row) if v is not None) for row in filled)
    last_col = max(max(i for i, v in enumerate(row) if v is not None) for row in filled)
    grid = [row[first_col:last_col + 1] for row in filled]

    holes = [(r, c) for r, row in enumerate(grid)
             for c, value in enumerate(row) if value is None]
    if holes:
        raise RuntimeError(f"внутри поля {len(holes)} пустых ячеек, первая {holes[0]} — "
                           "поле не прямоугольное, разбор надо уточнять, а не угадывать")
    return [[int(value) for value in row] for row in grid]


def solve(grid: list[list[int]]) -> tuple[int, int]:
    """(дешевейший маршрут, самый дорогой маршрут) от левого нижнего угла.

    Ходы — вправо и вверх, значит клетка зависит только от левого и нижнего соседа:
    обход снизу вверх и слева направо считает оба крайних маршрута одним проходом.
    """
    height = len(grid)
    width = len(grid[0])
    start = (height - 1, 0)
    if grid[start[0]][start[1]] == BLOCKED:
        raise RuntimeError("стартовая клетка запрещена — поле прочитано неверно")

    best: list[list[int | None]] = [[None] * width for _ in range(height)]
    worst: list[list[int | None]] = [[None] * width for _ in range(height)]

    for row in range(height - 1, -1, -1):
        for col in range(width):
            value = grid[row][col]
            if value == BLOCKED:
                continue
            if (row, col) == start:
                best[row][col] = worst[row][col] = value
                continue
            # Пришли снизу (row + 1) или слева (col - 1).
            incoming_best = [
                cost for cost in (
                    best[row + 1][col] if row + 1 < height else None,
                    best[row][col - 1] if col > 0 else None,
                ) if cost is not None
            ]
            incoming_worst = [
                cost for cost in (
                    worst[row + 1][col] if row + 1 < height else None,
                    worst[row][col - 1] if col > 0 else None,
                ) if cost is not None
            ]
            if incoming_best:
                best[row][col] = min(incoming_best) + value
                worst[row][col] = max(incoming_worst) + value

    def is_final(row: int, col: int) -> bool:
        """Из клетки нет допустимого хода: справа и сверху границы или запрет."""
        right_open = col + 1 < width and grid[row][col + 1] != BLOCKED
        up_open = row - 1 >= 0 and grid[row - 1][col] != BLOCKED
        return not right_open and not up_open

    finals = [(row, col) for row in range(height) for col in range(width)
              if grid[row][col] != BLOCKED and best[row][col] is not None
              and is_final(row, col)]
    if not finals:
        raise RuntimeError("финальных клеток не нашлось — поле прочитано неверно")
    logger.info("Поле %d×%d, запрещённых клеток %d, финальных достижимо %d",
                height, width,
                sum(1 for r in grid for v in r if v == BLOCKED), len(finals))
    return (min(best[r][c] for r, c in finals),
            max(worst[r][c] for r, c in finals))


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    conn = psycopg2.connect(dsn_from_mcp())
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(
            """
            SELECT id, task_content->>'title' AS title,
                   solution_rules #> '{short_answer,accepted_answers}' AS accepted,
                   substring(task_content->>'stem'
                             from '/api/v1/media/([a-f0-9]{64}\\.\\w+)') AS media
            FROM tasks WHERE id = 2310
            """
        )
        record = cur.fetchone()
    finally:
        conn.close()

    cheapest, priciest = solve(read_grid(fetch_media(record["media"])))
    computed = f"{cheapest} {priciest}"
    accepted = [item.get("value") for item in (record["accepted"] or [])]
    logger.info("[2310] %s", record["title"])
    logger.info("  задание 1 (дешевейший маршрут): %d", cheapest)
    logger.info("  задание 2 (самый дорогой маршрут): %d", priciest)
    logger.info("  пересчёт: %r", computed)
    logger.info("  принимается сейчас: %s", accepted)
    logger.info("  слитная форма источника: %r", f"{cheapest}{priciest}")
    return 0 if computed in accepted else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
