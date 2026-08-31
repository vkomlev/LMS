# scripts/tsk745_build_visuals.py
"""
tsk-745: образы экранов кабинета для курсов-онбордингов.

Зачем не живые скриншоты прода: единственная живая сессия — операторская, и в
кадр попадают его имя, суммы, реквизиты и счётчик уведомлений. Ученикам такое
показывать нельзя. Поэтому образ собирается детерминированно: подписи, кнопки и
статусы взяты с живых прогонов и из кода SPW, а данные — вымышленные и
одинаковые у всех.

**Ширина 440, а не 880 (правка 31.08).** Первая версия рисовала десктопный вид на
широком холсте. На телефоне (колонка ~330 px) он сжимался втрое, и подписи
превращались в штрихи — поймано замером на 360 px. Узкий холст читается на
телефоне почти без сжатия и остаётся чётким на мониторе. Заодно образы кабинета
показывают **мобильный** вид: меню там свёрнуто в кнопку с тремя полосками, и
именно так его видит школьник.

Ширину задаёт сама сцена: у карты прогресса ЕГЭ и диаграммы ОГЭ она своя — эти
образы шире по природе (сетка и ряд из шестнадцати столбиков).

Конвейер класса 3 политики визуалов: SVG -> HTML-обёртка -> Edge headless
(--screenshot) -> PNG 2x. cairosvg/inkscape на машине нет.

Запуск:
    python scripts/tsk745_build_visuals.py
    python scripts/tsk745_build_visuals.py --only onb-menu
"""
from __future__ import annotations

import argparse
import html
import pathlib
import subprocess
import tempfile
import time
from typing import Callable, Dict, List, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "curriculum" / "visuals"
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

#: Ширина образов кабинета. Подобрана под колонку материала на телефоне.
MW = 440

FONT = "Segoe UI, Arial, sans-serif"
MONO = "Consolas, monospace"

INK = "#0f172a"
MUTED = "#64748b"
LINE = "#e2e8f0"
CARD = "#ffffff"
BG = "#f8fafc"
PRIMARY = "#2563eb"
LINK = "#1d4ed8"
OK = "#059669"
WARN = "#d97706"
DANGER = "#dc2626"


# --------------------------------------------------------------- примитивы

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def rect(x: float, y: float, w: float, h: float, *, fill: str = CARD,
         stroke: str = LINE, r: float = 10, sw: float = 1) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def text(x: float, y: float, s: str, *, size: int = 16, fill: str = INK,
         weight: str = "400", anchor: str = "start", font: str = FONT) -> str:
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{esc(s)}</text>')


def button(x: float, y: float, label: str, *, w: float, h: float = 40,
           fill: str = PRIMARY, fg: str = "#ffffff", size: int = 15,
           stroke: str | None = None) -> str:
    body = rect(x, y, w, h, fill=fill, stroke=stroke or fill, r=8)
    body += text(x + w / 2, y + h / 2 + 5, label, size=size, fill=fg,
                 weight="600", anchor="middle")
    return body


def chip(x: float, y: float, label: str, *, fill: str, fg: str, size: int = 13) -> str:
    w = len(label) * 7.6 + 22
    out = rect(x, y, w, 26, fill=fill, stroke=fill, r=13)
    out += text(x + w / 2, y + 18, label, size=size, fill=fg, weight="600", anchor="middle")
    return out


def progress(x: float, y: float, w: float, done: float) -> str:
    """Полоса прогресса на карточке курса — на мобильном виде она есть."""
    out = rect(x, y, w, 8, fill="#e2e8f0", stroke="#e2e8f0", r=4)
    if done > 0:
        out += rect(x, y, max(6, w * done), 8, fill=INK, stroke=INK, r=4)
    return out


def mobile_header(w: int) -> str:
    """Шапка кабинета на узком экране: меню свёрнуто в кнопку с тремя полосками."""
    out = rect(0, 0, w, 52, fill="#ffffff", stroke=LINE, r=0)
    for i in range(3):
        out += rect(18, 18 + i * 6, 18, 2.5, fill=INK, stroke=INK, r=1)
    out += text(52, 33, "Платформа", size=16, weight="600")
    out += rect(w - 56, 16, 24, 20, fill=PRIMARY, stroke=PRIMARY, r=10)
    out += text(w - 44, 31, "9+", size=11, fill="#ffffff", weight="700", anchor="middle")
    return out


def svg(width: int, height: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            f'{rect(0, 0, width, height, fill=BG, stroke=BG, r=0)}{body}</svg>')


# --------------------------------------------------- сцены кабинета (440 px)

def scene_menu() -> Tuple[int, int, str]:
    """Меню на телефоне: кнопка с полосками и выехавшая панель со списком."""
    items = ["Курсы", "Занятия", "Сообщения", "Прогресс",
             "История", "Оплата", "Тариф", "Профиль"]
    panel_y = 100
    h = panel_y + 48 + len(items) * 42 + 24
    b = mobile_header(MW)
    b += text(20, 82, "Нажал полоски — выехала панель:", size=15, fill=MUTED)
    b += rect(12, panel_y, MW - 24, 48 + len(items) * 42, fill="#ffffff")
    b += text(32, panel_y + 32, "Меню", size=17, weight="700")
    b += rect(12, panel_y + 48, MW - 24, 1, fill=LINE, stroke=LINE, r=0)
    for i, it in enumerate(items):
        y = panel_y + 48 + i * 42
        if i == 0:
            b += rect(12, y, MW - 24, 42, fill="#f1f5f9", stroke="#f1f5f9", r=0)
        b += rect(32, y + 16, 10, 10, fill=PRIMARY if i == 0 else MUTED,
                  stroke=PRIMARY if i == 0 else MUTED, r=2)
        b += text(58, y + 27, it, size=16, weight="600" if i == 0 else "400")
    return MW, h, b


def scene_kurs_karta() -> Tuple[int, int, str]:
    h = 296
    b = mobile_header(MW)
    b += text(20, 88, "Мои курсы", size=22, weight="700")

    y = 106
    b += rect(12, y, MW - 24, 84)
    b += text(30, y + 26, "Python для ЕГЭ", size=16, weight="600")
    b += text(30, y + 46, "12/361 задач · 3%", size=13, fill=MUTED)
    b += progress(30, y + 54, MW - 60, 0.03)
    b += text(30, y + 76, "Заходили 29 авг.", size=12, fill=MUTED)
    b += text(MW - 30, y + 76, "Продолжить →", size=14, fill=LINK,
              weight="600", anchor="end")

    y = 200
    b += rect(12, y, MW - 24, 84)
    b += text(30, y + 24, "С чего начать: кабинет,", size=16, weight="600")
    b += text(30, y + 44, "занятия и работа дома", size=16, weight="600")
    b += text(30, y + 62, "0/30 задач · 0%", size=13, fill=MUTED)
    b += text(MW - 30, y + 78, "Продолжить →", size=14, fill=LINK,
              weight="600", anchor="end")
    b += text(30, y + 78, "Ещё не начат", size=12, fill=MUTED)
    return MW, h, b


def scene_kurs_derevo() -> Tuple[int, int, str]:
    h = 424
    b = mobile_header(MW)
    b += text(20, 78, "← Все курсы", size=13, fill=MUTED)
    b += text(20, 108, "С чего начать: кабинет,", size=18, weight="700")
    b += text(20, 130, "занятия и работа дома", size=18, weight="700")
    b += text(20, 154, "3/30 задач, 1/8 материалов", size=13, fill=MUTED)
    b += button(12, 168, "Открыть следующий материал →", w=MW - 24, h=42, size=14)
    for i, (tab, active) in enumerate([("Разделы", True), ("Лента", False),
                                       ("Программа", False)]):
        x = 20 + i * 108
        b += text(x, 244, tab, size=15, fill=PRIMARY if active else MUTED,
                  weight="600" if active else "400")
        if active:
            b += rect(x, 252, len(tab) * 9, 2, fill=PRIMARY, stroke=PRIMARY, r=1)
    rows = [("✓", OK, "Раздел 1. Кабинет", "5/5"),
            ("!", WARN, "Раздел 2. Задания", "2/5"),
            ("·", MUTED, "Раздел 3. Когда не получается", "0/5"),
            ("·", MUTED, "Раздел 4. Занятия", "0/5")]
    for i, (mark, color, title, cnt) in enumerate(rows):
        y = 272 + i * 36
        b += rect(12, y, MW - 24, 32, fill="#ffffff", stroke=LINE, r=6)
        b += text(30, y + 22, mark, size=15, fill=color, weight="700")
        b += text(52, y + 22, title, size=14)
        b += text(MW - 30, y + 22, cnt, size=13, fill=MUTED, anchor="end")
    return MW, h, b


def scene_zadanie() -> Tuple[int, int, str]:
    h = 490
    b = mobile_header(MW)
    b += text(20, 78, "← К курсу", size=13, fill=MUTED)
    b += chip(20, 92, "Обязательно", fill="#e0e7ff", fg="#3730a3")
    b += text(MW - 20, 110, "Попыток: 1 / 3", size=14, fill=MUTED,
              weight="600", anchor="end")
    b += text(20, 150, "Сумма двух чисел", size=19, weight="700")
    b += rect(12, 164, MW - 24, 58, fill="#ffffff")
    b += text(28, 190, "Напиши программу, которая", size=14)
    b += text(28, 210, "печатает сумму чисел 17 и 25.", size=14)

    b += text(20, 248, "Ответ", size=13, fill=MUTED, weight="600")
    b += rect(12, 256, MW - 24, 44, fill="#ffffff", stroke="#cbd5e1", r=8)
    b += text(28, 284, "42", size=15)

    b += text(20, 330, "Комментарий", size=13, fill=MUTED, weight="600")
    b += rect(12, 338, MW - 24, 44, fill="#ffffff", stroke="#cbd5e1", r=8)
    b += text(28, 366, "print(17 + 25)", size=14, font=MONO)

    b += rect(12, 398, 152, 34, fill="#f1f5f9", stroke="#cbd5e1", r=8)
    b += text(88, 420, "Выбрать файл", size=13, fill=MUTED, anchor="middle")
    b += button(12, 442, "Отправить на проверку", w=MW - 24, h=40, size=15)
    return MW, h, b


def scene_pomosch() -> Tuple[int, int, str]:
    h = 404
    b = mobile_header(MW)
    b += rect(12, 74, MW - 24, 74, fill="#fef2f2", stroke="#fecaca")
    b += text(30, 102, "Ответ неверный", size=16, fill=DANGER, weight="700")
    b += text(30, 128, "0 / 1 баллов · осталось попыток: 2", size=13, fill=DANGER)
    b += button(12, 164, "Разобраться с наставником", w=MW - 24, h=44, size=15)
    b += rect(12, 220, MW - 24, 44, fill="#ffffff", stroke="#cbd5e1", r=8)
    b += text(MW / 2, 248, "Запросить помощь преподавателя", size=14,
              weight="600", anchor="middle")
    b += text(20, 298, "Подсказки", size=15, fill=MUTED, weight="600")
    for i, label in enumerate(["Подсказка 1", "Видео-подсказка 1"]):
        y = 310 + i * 42
        b += rect(12, y, MW - 24, 36, fill="#f8fafc", stroke=LINE, r=8)
        b += text(30, y + 24, "▸  " + label, size=14, weight="600")
    return MW, h, b


def scene_zanyatie() -> Tuple[int, int, str]:
    h = 422
    b = mobile_header(MW)
    b += text(20, 88, "Мои занятия", size=22, weight="700")
    b += rect(12, 106, MW - 24, 214)
    b += text(30, 134, "Среда, 3 сентября", size=17, weight="700")
    b += chip(30, 148, "Запланировано", fill="#e0e7ff", fg="#3730a3")
    b += text(30, 202, "18:00–19:30 МСК", size=16)
    b += text(30, 224, "у вас 20:00–21:30", size=13, fill=MUTED)
    b += button(30, 238, "Я на занятии", w=146, h=36, size=14)
    b += rect(188, 238, 152, 36, fill="#ffffff", stroke="#cbd5e1", r=8)
    b += text(264, 261, "Присоединиться", size=13, weight="600", anchor="middle")
    b += text(30, 302, "Отказаться", size=14, fill=MUTED, weight="600")
    b += rect(12, 336, MW - 24, 66, fill="#ffffff")
    b += text(30, 362, "Четверг, 28 августа", size=15, weight="600", fill=MUTED)
    b += chip(30, 372, "Занятие пропущено", fill="#fee2e2", fg="#991b1b")
    return MW, h, b


def scene_oplata() -> Tuple[int, int, str]:
    h = 438
    b = mobile_header(MW)
    b += text(20, 88, "Оплата", size=22, weight="700")
    b += text(20, 112, "Начисления по месяцам и чеки.", size=13, fill=MUTED)
    b += rect(12, 126, MW - 24, 68, fill="#f8fafc")
    b += text(30, 152, "Реквизиты для перевода", size=13, fill=MUTED, weight="600")
    b += text(30, 178, "+7 977 ХХХ-ХХ-ХХ  Сбер", size=15, weight="700", font=MONO)

    b += rect(12, 206, MW - 24, 96)
    b += text(30, 232, "Сентябрь 2026", size=16, weight="700")
    b += text(30, 254, "Начислено 8 000 ₽", size=13, fill=MUTED)
    b += text(30, 274, "оплата до 30 сен.", size=13, fill=MUTED)
    b += text(MW - 30, 232, "Осталось 8 000 ₽", size=14, weight="600", anchor="end")
    b += chip(MW - 146, 252, "Просрочено", fill="#fffbeb", fg=WARN)
    b += button(12, 314, "Отправить чек за сентябрь", w=MW - 24, h=40, size=15)

    b += rect(12, 368, MW - 24, 58, fill="#ffffff")
    b += text(30, 394, "Август 2026", size=15, weight="600")
    b += chip(MW - 174, 378, "Чек на проверке", fill="#eff6ff", fg=PRIMARY)
    return MW, h, b


# ------------------------------------------- сцены предметных курсов (шире)

def scene_ege_karta() -> Tuple[int, int, str]:
    """Карта прогресса: фрагмент 12x5 с крупными подписями.

    Полная сетка 27x6 на телефоне сжималась до нечитаемых штрихов; двенадцати
    строк хватает, чтобы увидеть главное — как от варианта к варианту убывает
    красное.
    """
    rows, cols = 12, 5
    w = 700
    x0, y0 = 200, 152
    cw, ch = 94, 36

    def cell(r: int, c: int) -> str:
        v = (r * 7 + c * 11) % 10
        if v < 4 - c:
            return "r"
        if v < 8 - c:
            return "y"
        return "g"

    color = {"g": "#16a34a", "y": "#f59e0b", "r": "#dc2626"}
    h = y0 + rows * ch + 140
    b = text(20, 46, "Карта прогресса", size=23, weight="700")
    b += text(20, 76, "Строка — задание, столбец — прорешанный вариант.", size=15, fill=MUTED)
    b += text(20, 100, "Показаны первые двенадцать заданий.", size=14, fill=MUTED)
    for c in range(cols):
        b += text(x0 + c * cw + cw / 2, y0 - 14, f"Вар. {c + 1}", size=15,
                  fill=MUTED, weight="600", anchor="middle")
    for r in range(rows):
        y = y0 + r * ch
        b += text(x0 - 16, y + 24, f"Задание {r + 1}", size=15, anchor="end")
        for c in range(cols):
            fill = color[cell(r, c)]
            b += rect(x0 + c * cw + 2, y + 2, cw - 4, ch - 4, fill=fill, stroke=fill, r=5)
    ly = y0 + rows * ch + 30
    for i, (code, label) in enumerate([
        ("g", "верно с первого раза"),
        ("y", "исправлено работой над ошибками"),
        ("r", "не знаю, как решить — разбираем вместе"),
    ]):
        y = ly + i * 32
        b += rect(20, y, 22, 22, fill=color[code], stroke=color[code], r=5)
        b += text(54, y + 18, label, size=17)
    return w, h, b


def scene_oge_bally() -> Tuple[int, int, str]:
    """Где на ОГЭ лежат баллы: двенадцать заданий по одному против четырёх практических."""
    weights = [1] * 12 + [2, 3, 2, 2]
    w, h = 700, 404
    x0, ybase, bw, gap = 28, 288, 34, 6
    unit = 58

    b = text(20, 44, "Где на ОГЭ лежат баллы", size=23, weight="700")
    b += text(20, 74, "Столбик — задание, высота — сколько баллов оно стоит.",
              size=15, fill=MUTED)
    for i, wt in enumerate(weights):
        x = x0 + i * (bw + gap)
        bar_h = wt * unit
        practical = i >= 12
        fill = PRIMARY if practical else "#93c5fd"
        b += rect(x, ybase - bar_h, bw, bar_h, fill=fill, stroke=fill, r=5)
        b += text(x + bw / 2, ybase - bar_h - 7, str(wt), size=14,
                  fill=INK, weight="700", anchor="middle")
        b += text(x + bw / 2, ybase + 20, str(i + 1), size=13,
                  fill=INK if practical else MUTED,
                  weight="600" if practical else "400", anchor="middle")
    b += rect(20, ybase + 40, w - 40, 50, fill="#eff6ff", stroke="#bfdbfe")
    b += text(w / 2, ybase + 64, "12 баллов за двенадцать заданий",
              size=15, weight="600", anchor="middle")
    b += text(w / 2, ybase + 84, "9 баллов за четыре практических  ·  всего 21",
              size=15, weight="600", anchor="middle")
    return w, h, b


SCENES: Dict[str, Callable[[], Tuple[int, int, str]]] = {
    "onb-menu": scene_menu,
    "onb-kurs-karta": scene_kurs_karta,
    "onb-kurs-derevo": scene_kurs_derevo,
    "onb-zadanie": scene_zadanie,
    "onb-pomosch": scene_pomosch,
    "onb-zanyatie": scene_zanyatie,
    "onb-oplata": scene_oplata,
    "onb-ege-karta": scene_ege_karta,
    "onb-oge-bally": scene_oge_bally,
}


# ------------------------------------------------------------------ рендер

def render(name: str, width: int, height: int, markup: str) -> pathlib.Path:
    """SVG -> HTML-обёртка точного размера -> Edge headless -> PNG 2x."""
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"{name}.png"
    if png.exists():
        png.unlink()
    page = (
        "<!doctype html><meta charset='utf-8'>"
        f"<style>html,body{{margin:0;padding:0;width:{width}px;height:{height}px;"
        "overflow:hidden;background:#f8fafc}</style>" + markup
    )
    # ignore_cleanup_errors: Edge держит файлы кэша профиля ещё какое-то время
    # после выхода, и уборка каталога падает на WinError 5 уже ПОСЛЕ того, как
    # снимок сделан. Ронять из-за этого сборку картинок незачем.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        htm = pathlib.Path(tmp) / f"{name}.html"
        htm.write_text(page, encoding="utf-8")
        subprocess.run(
            [str(EDGE), "--headless", "--disable-gpu", f"--user-data-dir={tmp}\\profile",
             "--force-device-scale-factor=2", f"--screenshot={png}",
             f"--window-size={width},{height}", str(htm)],
            check=False, capture_output=True, timeout=120,
        )
        for _ in range(60):
            if png.exists() and png.stat().st_size > 0:
                break
            time.sleep(0.2)
    return png


def main() -> None:
    ap = argparse.ArgumentParser(description="tsk-745: образы экранов кабинета")
    ap.add_argument("--only", default=None, help="собрать один образ по имени")
    args = ap.parse_args()

    names: List[str] = [args.only] if args.only else list(SCENES)
    for name in names:
        if name not in SCENES:
            raise SystemExit(f"нет такой сцены: {name}; есть {list(SCENES)}")
        width, height, body = SCENES[name]()
        png = render(name, width, height, svg(width, height, body))
        size = png.stat().st_size if png.exists() else 0
        print(f"{name:18} {width}x{height}  ->  {png.name}  {size} байт"
              + ("" if size else "   ПУСТО, проверить"))


if __name__ == "__main__":
    main()
