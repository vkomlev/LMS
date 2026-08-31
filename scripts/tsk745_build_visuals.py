# scripts/tsk745_build_visuals.py
"""
tsk-745: образы экранов кабинета для курса-онбординга.

Зачем не живые скриншоты прода: единственная живая сессия — операторская, и в
кадр попадают его имя, маски телефона и почты, счётчик уведомлений. Ученикам
такое показывать нельзя. Поэтому образ собирается детерминированно: подписи,
кнопки и статусы взяты с живых прогонов 31.08 и из кода SPW, а данные —
вымышленные и одинаковые у всех.

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
from typing import Callable, Dict, List

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "curriculum" / "visuals"
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

W = 880
FONT = "Segoe UI, Arial, sans-serif"
MONO = "Consolas, monospace"

INK = "#0f172a"
MUTED = "#64748b"
LINE = "#e2e8f0"
CARD = "#ffffff"
BG = "#f8fafc"
PRIMARY = "#2563eb"
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


def text(x: float, y: float, s: str, *, size: int = 15, fill: str = INK,
         weight: str = "400", anchor: str = "start", font: str = FONT) -> str:
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{esc(s)}</text>')


def button(x: float, y: float, label: str, *, w: float | None = None, h: float = 36,
           fill: str = PRIMARY, fg: str = "#ffffff", size: int = 14,
           stroke: str | None = None) -> str:
    width = w if w is not None else len(label) * 8.2 + 30
    body = rect(x, y, width, h, fill=fill, stroke=stroke or fill, r=8)
    body += text(x + width / 2, y + h / 2 + 5, label, size=size, fill=fg,
                 weight="600", anchor="middle")
    return body


def chip(x: float, y: float, label: str, *, fill: str, fg: str, size: int = 12) -> str:
    width = len(label) * 7.0 + 20
    out = rect(x, y, width, 24, fill=fill, stroke=fill, r=12)
    out += text(x + width / 2, y + 16, label, size=size, fill=fg, weight="600", anchor="middle")
    return out


def field(x: float, y: float, w: float, label: str, value: str = "", *, h: float = 44) -> str:
    out = text(x, y - 8, label, size=13, fill=MUTED, weight="600")
    out += rect(x, y, w, h, fill="#ffffff", stroke="#cbd5e1", r=8)
    if value:
        out += text(x + 12, y + h / 2 + 5, value, size=14, fill=INK)
    return out


def header_bar() -> str:
    """Верхняя панель кабинета — общая шапка всех экранов."""
    items = ["Курсы", "Занятия", "Сообщения", "Прогресс", "История", "Оплата", "Тариф", "Профиль"]
    out = rect(0, 0, W, 56, fill="#ffffff", stroke=LINE, r=0)
    out += rect(16, 15, 26, 26, fill=PRIMARY, stroke=PRIMARY, r=6)
    out += text(29, 33, "ОП", size=12, fill="#ffffff", weight="700", anchor="middle")
    x = 54
    for i, it in enumerate(items):
        out += text(x, 34, it, size=13, fill=PRIMARY if i == 0 else MUTED,
                    weight="600" if i == 0 else "400")
        x += len(it) * 7.6 + 18
    out += text(W - 46, 34, "🔔", size=15, fill=MUTED)
    out += rect(W - 34, 14, 22, 16, fill=DANGER, stroke=DANGER, r=8)
    out += text(W - 23, 26, "3", size=11, fill="#ffffff", weight="700", anchor="middle")
    return out


def svg(height: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
            f'viewBox="0 0 {W} {height}">'
            f'{rect(0, 0, W, height, fill=BG, stroke=BG, r=0)}{body}</svg>')


# ------------------------------------------------------------------ сцены

def scene_menu() -> tuple[int, str]:
    h = 150
    b = header_bar()
    b += text(24, 96, "Верхнее меню одинаковое на всех экранах кабинета.", size=14, fill=MUTED)
    b += text(24, 122, "Слева — разделы, справа — колокольчик с числом непрочитанных уведомлений.",
              size=14, fill=MUTED)
    return h, b


def scene_kurs_karta() -> tuple[int, str]:
    h = 260
    b = header_bar()
    b += text(24, 92, "Мои курсы", size=22, fill=INK, weight="700")
    for i, (name, done, total, pct, seen) in enumerate([
        ("Python для ЕГЭ", 12, 361, 3, "Заходили 29 авг."),
        ("С чего начать: кабинет, занятия и работа дома", 0, 30, 0, "Ещё не открывали"),
    ]):
        y = 112 + i * 72
        b += rect(24, y, W - 48, 60)
        b += text(40, y + 26, name, size=15, weight="600")
        b += text(40, y + 46, f"{done}/{total} задач · {pct}%   ·   {seen}", size=13, fill=MUTED)
        b += button(W - 168, y + 12, "Продолжить →", w=128, h=34)
    return h, b


def scene_kurs_derevo() -> tuple[int, str]:
    h = 400
    b = header_bar()
    b += text(24, 86, "← Все курсы", size=13, fill=MUTED)
    b += text(24, 118, "С чего начать: кабинет, занятия и работа дома", size=20, weight="700")
    b += text(24, 142, "3/30 задач, 1/7 материалов", size=13, fill=MUTED)
    b += button(24, 158, "Перейти к следующей задаче →", w=272, h=40)
    for i, (tab, active) in enumerate([("Разделы", True), ("Лента", False), ("Программа курса", False)]):
        x = 24 + i * 118
        b += text(x, 232, tab, size=14, fill=PRIMARY if active else MUTED,
                  weight="600" if active else "400")
        if active:
            b += rect(x, 240, len(tab) * 8.4, 2, fill=PRIMARY, stroke=PRIMARY, r=1)
    rows = [
        ("✓", OK, "Раздел 1. Кабинет: где мой следующий шаг", "5/5", "Обязательно"),
        ("⚠", WARN, "Раздел 2. Задания: как сдать и что значит проверка", "2/5", "Обязательно"),
        # Все разделы онбординга обязательные — так и в самом курсе. Показывать
        # здесь «Желательно» ради контраста значило бы нарисовать курс, которого
        # нет: ученик сверит картинку со своим экраном и не найдёт этой строки.
        ("·", MUTED, "Раздел 3. Когда не получается: подсказки, наставник, преподаватель", "0/5", "Обязательно"),
        ("·", MUTED, "Раздел 4. Занятия: расписание и пропуски", "0/5", "Обязательно"),
    ]
    for i, (mark, color, title, cnt, req) in enumerate(rows):
        y = 258 + i * 34
        b += rect(24, y, W - 48, 30, fill="#ffffff", stroke=LINE, r=6)
        b += text(40, y + 20, mark, size=14, fill=color, weight="700")
        b += text(62, y + 20, title, size=13.5)
        b += text(W - 160, y + 20, cnt, size=13, fill=MUTED)
        b += text(W - 118, y + 20, req, size=12,
                  fill=INK if req == "Обязательно" else MUTED,
                  weight="600" if req == "Обязательно" else "400")
    return h, b


def scene_zadanie() -> tuple[int, str]:
    h = 470
    b = header_bar()
    b += text(24, 86, "← К курсу", size=13, fill=MUTED)
    b += chip(24, 100, "Обязательно", fill="#e0e7ff", fg="#3730a3")
    b += text(W - 24, 118, "Попыток: 1 / 3", size=14, fill=MUTED, weight="600", anchor="end")
    b += text(24, 158, "Сумма двух чисел", size=20, weight="700")
    b += rect(24, 174, W - 48, 46, fill="#ffffff")
    b += text(40, 202, "Напиши программу, которая печатает сумму чисел 17 и 25.", size=14)
    b += field(24, 254, W - 48, "Ответ", "42")
    b += field(24, 336, W - 48, "Комментарий (необязательно)", "посчитал через print(17 + 25)")
    b += text(24, 404, "Файл", size=13, fill=MUTED, weight="600")
    b += rect(24, 412, 200, 34, fill="#f1f5f9", stroke="#cbd5e1", r=8)
    b += text(124, 434, "Выбрать файл", size=13, fill=MUTED, anchor="middle")
    b += button(W - 224, 412, "Отправить на проверку", w=200, h=34)
    return h, b


def scene_pomosch() -> tuple[int, str]:
    h = 340
    b = header_bar()
    b += rect(24, 84, W - 48, 76, fill="#fef2f2", stroke="#fecaca")
    b += text(44, 112, "Ответ неверный", size=15, fill=DANGER, weight="700")
    b += text(44, 136, "0 / 1 баллов · осталось попыток: 2", size=13, fill=DANGER)
    b += button(44, 176, "Разобраться с наставником", w=246, h=38)
    b += rect(306, 176, 250, 38, fill="#ffffff", stroke="#cbd5e1", r=8)
    b += text(431, 200, "Запросить помощь преподавателя", size=13, fill=INK,
              weight="600", anchor="middle")
    b += text(24, 246, "Подсказки", size=14, fill=MUTED, weight="600")
    for i, label in enumerate(["Подсказка 1", "Видео-подсказка 1"]):
        y = 258 + i * 38
        b += rect(24, y, W - 48, 32, fill="#f8fafc", stroke=LINE, r=8)
        b += text(40, y + 21, "▸  " + label, size=13, weight="600")
        b += text(W - 40, y + 21, "нажми, чтобы раскрыть", size=12, fill=MUTED, anchor="end")
    return h, b


def scene_zanyatie() -> tuple[int, str]:
    h = 310
    b = header_bar()
    b += text(24, 92, "Мои занятия", size=22, weight="700")
    b += rect(24, 110, W - 48, 96)
    b += text(44, 140, "Среда, 3 сентября", size=16, weight="700")
    b += text(44, 164, "18:00–19:30 МСК", size=15)
    b += text(190, 164, "у вас 20:00–21:30", size=13, fill=MUTED)
    b += chip(W - 176, 126, "Запланировано", fill="#e0e7ff", fg="#3730a3")
    b += button(44, 178, "Я на занятии", w=142, h=32, fill=PRIMARY)
    b += rect(198, 178, 148, 32, fill="#ffffff", stroke="#cbd5e1", r=8)
    b += text(272, 199, "Присоединиться", size=13, weight="600", anchor="middle")
    b += text(W - 60, 199, "Отказаться", size=13, fill=MUTED, weight="600", anchor="end")
    b += rect(24, 222, W - 48, 62, fill="#ffffff")
    b += text(44, 248, "Четверг, 28 августа", size=15, weight="600", fill=MUTED)
    b += chip(W - 176, 234, "Занятие пропущено", fill="#fee2e2", fg="#991b1b")
    b += text(44, 270, "Записаться на занятие взамен", size=13, fill=PRIMARY, weight="600")
    return h, b


def scene_oplata() -> tuple[int, str]:
    h = 380
    b = header_bar()
    b += text(24, 92, "Оплата", size=22, weight="700")
    b += text(24, 118, "Начисления по месяцам и приложенные чеки.", size=13, fill=MUTED)
    b += rect(24, 134, W - 48, 74, fill="#f8fafc")
    b += text(44, 162, "Реквизиты для перевода", size=13, fill=MUTED, weight="600")
    b += text(44, 188, "+7 977 ХХХ-ХХ-ХХ   Сбер, ТБанк", size=16, weight="700", font=MONO)
    for i, (period, total, state, color, bgc) in enumerate([
        ("Сентябрь 2026", "8 000 ₽", "К оплате до 30 сентября", WARN, "#fffbeb"),
        ("Август 2026", "8 000 ₽", "Чек на подтверждении", PRIMARY, "#eff6ff"),
    ]):
        y = 224 + i * 76
        b += rect(24, y, W - 48, 64)
        b += text(44, y + 28, period, size=15, weight="700")
        b += text(44, y + 50, total, size=14, fill=MUTED)
        b += chip(150, y + 34, state, fill=bgc, fg=color)
        if i == 0:
            b += button(W - 184, y + 16, "Приложить чек", w=160, h=32)
    return h, b



def scene_ege_karta() -> tuple[int, str]:
    """Карта прогресса по вариантам — по образцу рабочей таблицы оператора.

    Данные вымышленные: настоящая таблица содержит фамилии учеников и ссылки на
    варианты, а этот образ увидит любой ученик курса.

    Показан ФРАГМЕНТ 12x5, а не все 27 заданий: полная сетка на телефоне
    сжимается до нечитаемых штрихов (проверено на 360 px). Двенадцати строк
    хватает, чтобы увидеть главное — как от варианта к варианту убывает красное.
    """
    rows = 12
    cols = 5
    x0, y0 = 250, 168
    cw, ch = 116, 40

    # Детерминированная раскладка с ЗАМЫСЛОМ: доля красного падает от первого
    # варианта к последнему. Случайный шум читался бы как «прогресса нет».
    def cell(r: int, c: int) -> str:
        v = (r * 7 + c * 11) % 10
        if v < 4 - c:
            return "r"
        if v < 8 - c:
            return "y"
        return "g"

    color = {"g": "#16a34a", "y": "#f59e0b", "r": "#dc2626"}

    h = y0 + rows * ch + 150
    b = text(24, 52, "Карта прогресса: варианты и задания", size=26, weight="700")
    b += text(24, 86, "Строка — номер задания, столбец — прорешанный вариант.",
              size=18, fill=MUTED)
    b += text(24, 110, "Показаны первые двенадцать заданий.", size=16, fill=MUTED)

    for c in range(cols):
        b += text(x0 + c * cw + cw / 2, y0 - 16, f"Вар. {c + 1}", size=17,
                  fill=MUTED, weight="600", anchor="middle")
    for r in range(rows):
        y = y0 + r * ch
        b += text(x0 - 18, y + 27, f"Задание {r + 1}", size=17, anchor="end")
        for c in range(cols):
            fill = color[cell(r, c)]
            b += rect(x0 + c * cw + 2, y + 2, cw - 4, ch - 4, fill=fill, stroke=fill, r=5)

    ly = y0 + rows * ch + 34
    for i, (code, label) in enumerate([
        ("g", "верно с первого раза"),
        ("y", "исправлено работой над ошибками"),
        ("r", "не знаю, как решить — разбираем вместе"),
    ]):
        y = ly + i * 34
        b += rect(24, y, 24, 24, fill=color[code], stroke=color[code], r=5)
        b += text(60, y + 19, label, size=19)
    return h, b


def scene_oge_bally() -> tuple[int, str]:
    """Где на ОГЭ лежат баллы: 12 заданий по одному против четырёх практических.

    Столбик = задание, высота = его вес в баллах. Смысл образа один: показать,
    что четыре последних задания стоят почти столько же, сколько все двенадцать
    первых. Таблицей это видно хуже, чем высотой.
    """
    # Веса сверены по трём источникам и сходятся арифметически: 12 + 9 = 21.
    weights = [1] * 12 + [2, 3, 2, 2]
    h = 420
    x0, ybase, bw, gap = 40, 300, 44, 8
    unit = 62  # высота одного балла

    b = text(24, 44, "Где на ОГЭ лежат баллы", size=20, weight="700")
    b += text(24, 70, "Столбик — задание, высота — сколько баллов оно стоит.",
              size=14, fill=MUTED)

    for i, w in enumerate(weights):
        x = x0 + i * (bw + gap)
        bar_h = w * unit
        practical = i >= 12
        fill = PRIMARY if practical else "#93c5fd"
        b += rect(x, ybase - bar_h, bw, bar_h, fill=fill, stroke=fill, r=6)
        b += text(x + bw / 2, ybase - bar_h - 8, str(w), size=13,
                  fill=INK, weight="700", anchor="middle")
        b += text(x + bw / 2, ybase + 20, str(i + 1), size=12,
                  fill=INK if practical else MUTED,
                  weight="600" if practical else "400", anchor="middle")

    b += text(x0 + 6 * (bw + gap), ybase + 48, "задания 1–12: краткий ответ, по 1 баллу",
              size=13, fill=MUTED, anchor="middle")
    b += text(x0 + 14 * (bw + gap) - 20, ybase + 48, "13–16: на компьютере",
              size=13, fill=PRIMARY, weight="600", anchor="middle")

    b += rect(24, ybase + 66, W - 48, 44, fill="#eff6ff", stroke="#bfdbfe")
    b += text(W / 2, ybase + 94,
              "12 баллов за двенадцать заданий  ·  9 баллов за четыре практических  ·  всего 21",
              size=14, weight="600", anchor="middle")
    return h, b


SCENES: Dict[str, Callable[[], tuple[int, str]]] = {
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
        height, body = SCENES[name]()
        png = render(name, W, height, svg(height, body))
        size = png.stat().st_size if png.exists() else 0
        print(f"{name:18} {W}x{height}  ->  {png.name}  {size} байт"
              + ("" if size else "   ПУСТО, проверить"))


if __name__ == "__main__":
    main()
