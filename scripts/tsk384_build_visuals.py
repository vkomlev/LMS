# -*- coding: utf-8 -*-
"""tsk-384, шаг 1: сгенерировать SVG->PNG схему доски для 5 заданий про ходы фигур.

Вариант визуала — гибрид (обоснование в reviews/2026-08-03-tsk384-chess-visuals.md):
доска 8x8 с координатной нумерацией по осям (как на скрине оператора) + геометрия
хода КОНКРЕТНОЙ фигуры, показанная СТРЕЛКАМИ РОВНО ТЕХ 3 ПРОГОНОВ, что уже названы в
тексте задания ("Запустите программу 3 раза: вход ..."). Данные не выдуманы: числа и
подписи взяты из task_content.stem (сверено запросом к проду 2026-08-03) и/или
вычислены по формуле хода, приведённой в том же stem, — не по "похожести" условия
(прецедент путаницы tsk-316: 5 заданий делят общую преамбулу и различаются только
именем фигуры в хвосте).

Рендер SVG->PNG: Edge headless (playwright не установлен в .venv LMS, ставить ради
5 картинок нецелесообразно) — тот же приём, что предписывает /course-screenshots
Шаг 3 для машин без cairosvg/inkscape.

Запуск: python scripts/tsk384_build_visuals.py
Пишет .svg и .png в reviews/tsk384-chess-visuals/.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = Path(__file__).resolve().parents[1] / "reviews" / "tsk384-chess-visuals"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

SCALE = 2
CELL = 56
BOARD = CELL * 8
BOARD_X = 74
BOARD_Y = 92
W = BOARD_X + BOARD + 40
LEGEND_TOP = BOARD_Y + BOARD + 46
ROW_H = 27
H = LEGEND_TOP + 44 + 3 * ROW_H + 30

INK = "#28374a"
INK_STRONG = "#1f3a5f"
LIGHT_SQ = "#f0d9b5"
DARK_SQ = "#b58863"
GREEN = "#1a7a3c"
RED = "#b3261e"
PAPER = "#ffffff"


def t(x: float, y: float, s: str, size: int = 14, fill: str = INK,
      weight: str = "normal", anchor: str = "start") -> str:
    return (f'<text x="{x}" y="{y}" font-family="Segoe UI, Arial" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{escape(s)}</text>')


def cell_center(x: int, y: int) -> tuple[float, float]:
    """(x,y) шахматные координаты 1..8 (x=вертикаль, y=горизонталь) -> центр пикселя.
    y=1 внизу доски, y=8 наверху — как в тексте задания и на реальной доске."""
    cx = BOARD_X + (x - 1) * CELL + CELL / 2
    cy = BOARD_Y + (8 - y) * CELL + CELL / 2
    return cx, cy


def board_svg() -> str:
    out = []
    for row in range(8):  # row 0 = верх доски (y=8) .. row 7 = низ (y=1)
        for col in range(8):
            x_coord = col + 1
            y_coord = 8 - row
            light = (x_coord + y_coord) % 2 == 0
            out.append(
                f'<rect x="{BOARD_X + col * CELL}" y="{BOARD_Y + row * CELL}" '
                f'width="{CELL}" height="{CELL}" fill="{LIGHT_SQ if light else DARK_SQ}"/>'
            )
    out.append(f'<rect x="{BOARD_X}" y="{BOARD_Y}" width="{BOARD}" height="{BOARD}" '
                f'fill="none" stroke="{INK_STRONG}" stroke-width="2"/>')
    for i in range(1, 9):
        cx, _ = cell_center(i, 1)
        out.append(t(cx, BOARD_Y + BOARD + 22, str(i), 14, INK_STRONG, "600", "middle"))
        _, cy = cell_center(1, i)
        out.append(t(BOARD_X - 16, cy + 5, str(i), 14, INK_STRONG, "600", "middle"))
    return "".join(out)


def piece_marker(x: int, y: int, glyph: str) -> str:
    cx, cy = cell_center(x, y)
    return (f'<circle cx="{cx}" cy="{cy}" r="21" fill="{PAPER}" stroke="{INK_STRONG}" stroke-width="2"/>'
            f'<text x="{cx}" y="{cy + 12}" font-size="34" text-anchor="middle" fill="{INK_STRONG}">{glyph}</text>')


def arrow_defs() -> str:
    return (
        '<defs>'
        f'<marker id="argreen" markerWidth="10" markerHeight="10" refX="7" refY="5" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{GREEN}"/></marker>'
        f'<marker id="arred" markerWidth="10" markerHeight="10" refX="7" refY="5" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{RED}"/></marker>'
        '</defs>'
    )


def move_arrow(x1: int, y1: int, x2: int, y2: int, legal: bool, n: int) -> str:
    cx1, cy1 = cell_center(x1, y1)
    cx2, cy2 = cell_center(x2, y2)
    color = GREEN if legal else RED
    marker = "argreen" if legal else "arred"
    # чуть отвести конец стрелки от центра клетки, чтобы не перекрывать badge/фигуру
    dx, dy = cx2 - cx1, cy2 - cy1
    dist = max((dx ** 2 + dy ** 2) ** 0.5, 1)
    ex, ey = cx2 - dx / dist * 16, cy2 - dy / dist * 16
    mx, my = (cx1 + ex) / 2, (cy1 + ey) / 2
    out = [f'<line x1="{cx1}" y1="{cy1}" x2="{ex}" y2="{ey}" stroke="{color}" '
           f'stroke-width="3.5" marker-end="url(#{marker})"/>']
    out.append(f'<circle cx="{mx}" cy="{my}" r="12" fill="{color}"/>')
    out.append(t(mx, my + 5, str(n), 13, "#ffffff", "bold", "middle"))
    target_mark = (f'<circle cx="{cx2}" cy="{cy2}" r="15" fill="none" stroke="{color}" '
                   f'stroke-width="3" stroke-dasharray="{"" if legal else "4 3"}"/>')
    out.append(target_mark)
    return "".join(out)


def legend_line(n: int, x1: int, y1: int, x2: int, y2: int, label: str, legal: bool, top: float) -> str:
    color = GREEN if legal else RED
    y = top + n * ROW_H
    verdict = "YES" if legal else "NO"
    out = [f'<circle cx="{BOARD_X + 10}" cy="{y - 5}" r="9" fill="{color}"/>',
           t(BOARD_X + 10, y - 1, str(n), 11, "#ffffff", "bold", "middle")]
    text = f"({x1},{y1}) \u2192 ({x2},{y2})  {label}  \u2014 {verdict}"
    out.append(t(BOARD_X + 28, y, text, 13.5, INK))
    return "".join(out)


def build_svg(title: str, glyph: str, starts: list[tuple[int, int]],
              moves: list[tuple[int, int, int, int, str, bool]]) -> str:
    parts = [f'<rect width="{W}" height="{H}" fill="{PAPER}"/>', arrow_defs()]
    parts.append(t(W / 2, 34, title, 21, INK_STRONG, "700", "middle"))
    parts.append(t(W / 2, 58, "x \u2014 номер вертикали, y \u2014 номер горизонтали (1..8)",
                    13, INK, "normal", "middle"))
    parts.append(board_svg())
    for (x1, y1, x2, y2, label, legal) in moves:
        parts.append(move_arrow(x1, y1, x2, y2, legal, moves.index((x1, y1, x2, y2, label, legal)) + 1))
    for sx, sy in starts:
        parts.append(piece_marker(sx, sy, glyph))
    parts.append(t(BOARD_X, LEGEND_TOP, "Примеры ходов из условия:", 14.5, INK_STRONG, "700"))
    for i, (x1, y1, x2, y2, label, legal) in enumerate(moves, start=1):
        parts.append(legend_line(i, x1, y1, x2, y2, label, legal, LEGEND_TOP + 20))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'width="{W}" height="{H}">{"".join(parts)}</svg>')


# ---------------------------------------------------------------- данные (из прод-stem, сверено 2026-08-03)

FIGURES = {
    "rook": dict(
        task_id=182, title="Ладья \u2014 куда может пойти", glyph="\u265c",
        starts=[(1, 1)],
        moves=[
            (1, 1, 1, 8, "по вертикали", True),
            (1, 1, 8, 1, "по горизонтали", True),
            (1, 1, 5, 5, "по диагонали \u2014 ладья не ходит", False),
        ],
    ),
    "bishop": dict(
        task_id=183, title="Слон \u2014 куда может пойти", glyph="\u265d",
        starts=[(1, 1), (4, 4)],
        moves=[
            (1, 1, 5, 5, "диагональ", True),
            (1, 1, 3, 5, "не диагональ", False),
            (4, 4, 8, 8, "диагональ", True),
        ],
    ),
    "king": dict(
        task_id=184, title="Король \u2014 куда может пойти", glyph="\u265a",
        starts=[(4, 4)],
        moves=[
            (4, 4, 5, 5, "диагональ на 1", True),
            (4, 4, 4, 3, "вниз на 1", True),
            (4, 4, 6, 4, "вбок на 2 \u2014 нельзя", False),
        ],
    ),
    "queen": dict(
        task_id=185, title="Ферзь \u2014 куда может пойти", glyph="\u265b",
        starts=[(4, 4)],
        moves=[
            (4, 4, 8, 4, "вертикаль", True),
            (4, 4, 8, 8, "диагональ", True),
            (4, 4, 5, 7, "ни то, ни другое", False),
        ],
    ),
    "knight": dict(
        task_id=186, title="Конь \u2014 куда может пойти", glyph="\u265e",
        starts=[(4, 4)],
        moves=[
            (4, 4, 6, 5, "dx=2, dy=1", True),
            (4, 4, 5, 6, "dx=1, dy=2", True),
            (4, 4, 6, 6, "dx=2, dy=2 \u2014 слон, не конь", False),
        ],
    ),
}


def verify_rules() -> None:
    """Пересчитать легальность каждого хода по формуле САМОЙ фигуры (не по label) —
    страховка от опечатки в ручной разметке legal/illegal."""
    rules = {
        "rook": lambda x1, y1, x2, y2: x1 == x2 or y1 == y2,
        "bishop": lambda x1, y1, x2, y2: abs(x1 - x2) == abs(y1 - y2),
        "king": lambda x1, y1, x2, y2: abs(x1 - x2) <= 1 and abs(y1 - y2) <= 1,
        "queen": lambda x1, y1, x2, y2: x1 == x2 or y1 == y2 or abs(x1 - x2) == abs(y1 - y2),
        "knight": lambda x1, y1, x2, y2: (abs(x1 - x2) == 2 and abs(y1 - y2) == 1) or (
            abs(x1 - x2) == 1 and abs(y1 - y2) == 2),
    }
    for key, data in FIGURES.items():
        fn = rules[key]
        for (x1, y1, x2, y2, label, legal) in data["moves"]:
            computed = fn(x1, y1, x2, y2)
            if computed != legal:
                raise AssertionError(
                    f"{key}: ход ({x1},{y1})->({x2},{y2}) размечен legal={legal}, "
                    f"а по формуле фигуры {computed}"
                )
    print(f"Проверка формул хода: {sum(len(d['moves']) for d in FIGURES.values())} ходов, "
          f"расхождений с разметкой не найдено.")


def render_png(svg_path: Path, png_path: Path) -> None:
    html_path = svg_path.with_suffix(".html")
    svg_text = svg_path.read_text(encoding="utf-8")
    html_path.write_text(
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<style>*{{margin:0;padding:0}}html,body{{width:{W}px;height:{H}px;background:#fff}}'
        f'svg{{display:block}}</style></head><body>{svg_text}</body></html>',
        encoding="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="tsk384-edge-") as profile_dir:
        cmd = [
            EDGE, "--headless", "--disable-gpu",
            f"--user-data-dir={profile_dir}",
            f"--force-device-scale-factor={SCALE}",
            f"--window-size={W},{H}",
            f"--screenshot={png_path}",
            html_path.as_uri(),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    for _ in range(50):
        if png_path.exists() and png_path.stat().st_size > 0:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError(f"PNG не появился: {png_path}")


def main() -> None:
    verify_rules()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key, data in FIGURES.items():
        svg_text = build_svg(data["title"], data["glyph"], data["starts"], data["moves"])
        svg_path = OUT_DIR / f"{key}.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        png_path = OUT_DIR / f"{key}.png"
        render_png(svg_path, png_path)
        print(f"OK  {key}.svg -> {key}.png  ({png_path.stat().st_size} B)  task_id={data['task_id']}")


if __name__ == "__main__":
    main()
