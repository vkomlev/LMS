# -*- coding: utf-8 -*-
"""tsk-350: нормализация условия задания и ключи сравнения.

Идея критерия (двухфакторный):
  1. payload — «полезная нагрузка» условия: все числа и формульные символы.
     Задачи одного типа, отличающиеся числами, дают РАЗНЫЙ payload => не дубли.
  2. prose  — текст без чисел и формул (общая преамбула типа задания).
     Похожесть prose подтверждает, что это тот же вопрос, а не совпадение чисел.

Дубль = payload идентичен И похожесть нормализованного текста высокая.
Урок tsk-316: общая преамбула сама по себе ничего не доказывает, различает
именно специфичная деталь — поэтому payload обязателен и сравнивается точно.
"""
from __future__ import annotations

import html
import re
import unicodedata

# --- срезаемый мусор разметки -------------------------------------------------
RE_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
RE_ANY_TAG = re.compile(r"<(/?)([a-zA-Z0-9]+)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>", re.S)
VOID_TAGS = {
    "br", "img", "hr", "input", "meta", "link", "source", "col", "area", "base", "wbr",
}
# katex-html и прочие aria-hidden — визуальный дубликат формулы, режем поддеревом
RE_HIDDEN_ATTRS = re.compile(r'aria-hidden\s*=\s*"true"|class="[^"]*katex-html', re.I)


def drop_hidden_subtrees(s: str) -> str:
    """Удалить поддеревья элементов aria-hidden/katex-html с учётом вложенности."""
    out: list[str] = []
    pos = 0
    skip_depth = 0  # >0 — мы внутри вырезаемого поддерева
    depth = 0
    for m in RE_ANY_TAG.finditer(s):
        closing, name, attrs, self_close = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if not skip_depth:
            out.append(s[pos:m.end()])  # текст ДО тега вместе с самим тегом
        pos = m.end()
        void = bool(self_close) or name in VOID_TAGS
        if closing:
            depth -= 1
            if skip_depth and depth < skip_depth:
                skip_depth = 0
            continue
        if void:
            continue
        depth += 1
        if not skip_depth and RE_HIDDEN_ATTRS.search(attrs):
            skip_depth = depth
    if not skip_depth:
        out.append(s[pos:])
    return " ".join(out)
# в MathML полезен только annotation с исходным TeX
RE_ANNOTATION = re.compile(
    r'<annotation[^>]*encoding="application/x-tex"[^>]*>(.*?)</annotation>', re.S | re.I
)
RE_MATH = re.compile(r"<math\b.*?</math>", re.S | re.I)
RE_TAG = re.compile(r"<[^>]+>")
RE_WS = re.compile(r"\s+")

# --- символьная канонизация ---------------------------------------------------
SYMBOL_MAP = {
    "∨": "|", "\\lor": "|", "\\vee": "|",
    "∧": "&", "\\land": "&", "\\wedge": "&",
    "¬": "!", "\\neg": "!", "\\lnot": "!", "~": "!",
    "≡": "=", "\\equiv": "=", "↔": "=", "\\leftrightarrow": "=",
    "→": ">", "\\to": ">", "\\rightarrow": ">", "⇒": ">",
    "≤": "<=", "≥": ">=", "≠": "!=",
    "·": "*", "×": "*", "⋅": "*", "\\cdot": "*", "\\times": "*",
    "−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-",
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'",
    "…": "...",
}
# служебные TeX-обёртки, не несущие смысла
TEX_NOISE = re.compile(
    r"\\(?:mathrm|mathvariant|text|mathbf|mathit|displaystyle|left|right|,|;|!|quad|qquad)\b"
)
# мягкий перенос / zero-width / BOM — удалять (склеивают слово, а не разделяют)
DELETABLE = dict.fromkeys(
    [0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None
)
# неразрывные и типографские пробелы — заменять на обычный пробел
SPACEABLE = dict.fromkeys(
    [0x00A0, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008,
     0x2009, 0x200A, 0x202F, 0x205F, 0x3000, 0x0009, 0x000D], " "
)

RE_PUNCT = re.compile(r"""([,;:.!?()\[\]{}"'|&=<>+\-*/^_~])""")
RE_NUM = re.compile(r"\d+(?:[.,]\d+)?")
RE_FORMULA_CH = re.compile(r"[|&!=><+\-*/^(){}\[\]]")
RE_LATIN_VAR = re.compile(r"(?<![a-z])[a-z](?![a-z])")


def strip_html(raw: str) -> str:
    """HTML-условие -> плоский текст с сохранением формул в TeX-виде."""
    if not raw:
        return ""
    s = RE_SCRIPT.sub(" ", raw)
    s = drop_hidden_subtrees(s)

    def _math(m: re.Match) -> str:
        ann = RE_ANNOTATION.search(m.group(0))
        return " " + html.unescape(ann.group(1)) + " " if ann else " "

    s = RE_MATH.sub(_math, s)
    # блочные теги -> перенос, чтобы ячейки таблиц не слипались
    s = re.sub(r"</(p|div|tr|li|h[1-6])>", " \n ", s, flags=re.I)
    s = re.sub(r"</(td|th)>", " ; ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", " \n ", s, flags=re.I)
    s = RE_TAG.sub(" ", s)
    return html.unescape(s)


def canon_text(raw: str) -> str:
    """Полная нормализация: невидимые символы, юникод, символы операций, регистр."""
    s = strip_html(raw)
    s = s.translate(DELETABLE).translate(SPACEABLE)
    s = unicodedata.normalize("NFKC", s)
    s = TEX_NOISE.sub(" ", s)
    for src, dst in SYMBOL_MAP.items():
        s = s.replace(src, dst)
    s = s.lower()
    s = s.replace("**", "^")
    # Знаки препинания и операций — отдельными токенами. Иначе «w, x, y, z» и
    # «w,x,y,z» дают полностью разные фрагменты, и настоящий дубль из другого
    # источника не склеивается (ложный пропуск на задании 2_1, tsk-350).
    s = RE_PUNCT.sub(r" \1 ", s)
    # унифицировать разделитель дробей в числах и убрать разряды-пробелы в числах
    s = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", s)
    s = RE_WS.sub(" ", s).strip()
    return s


def payload_of(text: str) -> str:
    """Полезная нагрузка: числа + формульные символы + одиночные латинские переменные.

    Именно она различает «та же формулировка, другие числа».
    Порядок сохраняется — перестановка чисел меняет задачу.
    """
    toks: list[str] = []
    for m in re.finditer(r"\d+(?:[.,]\d+)?|[|&!=><+\-*/^]|(?<![a-zа-я])[a-z](?![a-zа-я])", text):
        tok = m.group(0).replace(",", ".")
        toks.append(tok)
    return " ".join(toks)


def prose_of(text: str) -> str:
    """Проза без чисел и формул — общая часть типа задания."""
    s = RE_NUM.sub(" # ", text)
    s = RE_FORMULA_CH.sub(" ", s)
    s = re.sub(r"[^а-яёa-z# ]+", " ", s)
    return RE_WS.sub(" ", s).strip()


def task_text(task_content: dict) -> str:
    """Собрать сравниваемый текст задания: условие + код + варианты ответа."""
    parts: list[str] = []
    stem = task_content.get("stem") or ""
    parts.append(str(stem))
    code = task_content.get("code")
    if code:
        parts.append(str(code))
    tbl = task_content.get("table")
    if tbl:
        parts.append(json_flat(tbl))
    opts = task_content.get("options")
    if opts:
        parts.append(json_flat(opts))
    return "\n".join(parts)


def json_flat(obj) -> str:
    """Плоское строковое представление вложенной структуры."""
    if isinstance(obj, dict):
        return " ".join(json_flat(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return " ".join(json_flat(v) for v in obj)
    return "" if obj is None else str(obj)
