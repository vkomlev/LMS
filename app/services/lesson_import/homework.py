"""tsk-1255: домашняя работа (docx) → блоки заданий с ключами автора.

Перенос общего разборщика уроков 2+ курса «Дипломат» (build_lessons.py).
Раскладка: блок начинается фразой «К блоку №N» или «… вместо задания к
блоку №N»; варианты — абзацы «List Paragraph» (номер в нумерации Word, не в
тексте); соответствия — таблицей в две колонки или строками с табуляцией;
ключи — в конце файла после «Ответы:»/«Ответ:», по одному на блок, по порядку.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.lesson_import import docx_reader as dr
from app.services.lesson_import.answers import clean_key

LETTERS = "АБВГДЕЖЗИКЛМНОП"
_BLOCK_RE = re.compile(r"вместо задания к блоку\s*№?\s*(\d+)", re.I)
_BLOCK_HEAD_RE = re.compile(r"^К блоку\s*№?\s*(\d+)", re.I)
_POS_PREFIX = re.compile(r"^(?:[А-ЯA-Z]\)|\d+[.)])\s*")
_NUMBERED_KEY = re.compile(r"(\d+)\)\s*([\d.,]+)")


@dataclass
class HwBlock:
    """Блок домашней работы: номер, HTML условия, ключ автора (или None)."""

    num: int
    html: list[str] = field(default_factory=list)
    key: str | None = None


@dataclass
class HomeworkResult:
    """Разобранная домашняя работа и предупреждения."""

    blocks: list[HwBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _ul(items: list[str], marks: str | None) -> str:
    """Перечень с явными номерами: буквы А) Б) или цифры 1) 2)."""
    lis = "".join(
        f"<li><strong>{marks[i] if marks else i + 1})</strong> {dr.esc(t)}</li>"
        for i, t in enumerate(items)
    )
    return f"<ul>{lis}</ul>"


def is_answer_table(cols: list[list[str]]) -> bool:
    """Пустая таблица под ответ: «А | Б | В» + пустая строка или «Ответ: | |»."""
    flat = [c for col in cols for c in col]
    return (not flat) or all(len(c) <= 2 or c.startswith("Ответ") for c in flat)


def _join_head(col: list[str]) -> None:
    """Заголовок колонки, разбитый автором на строки, склеить до двоеточия.

    Иначе хвост заголовка («восстановителя:») становится пунктом «1)» и
    нумерация вариантов съезжает против ключа.
    """
    while len(col) > 1 and not col[0].rstrip().endswith(":") and col[1].rstrip().endswith(":"):
        col[0:2] = [f"{col[0]} {col[1]}"]


def matching_html(cols: list[list[str]]) -> str:
    """Соответствие: слева позиции буквами, справа варианты цифрами; буквы автора снимаются."""
    left, right = list(cols[0]), list(cols[-1])
    _join_head(left)
    _join_head(right)
    items_l = [_POS_PREFIX.sub("", x) for x in left[1:]]
    items_r = [_POS_PREFIX.sub("", x) for x in right[1:]]
    return (
        f"<p><strong>{dr.esc(left[0])}</strong></p>{_ul(items_l, LETTERS)}"
        f"<p><strong>{dr.esc(right[0])}</strong></p>{_ul(items_r, None)}"
    )


def tabbed_html(lines: list[str]) -> str:
    """Соответствие строками с табуляцией → две колонки; первая строка из двух ячеек — заголовок."""
    left: list[str] = []
    right: list[str] = []
    heads: list[str] = []
    for line in lines:
        cells = [c.strip() for c in re.split(r"\t+", line) if c.strip()]
        if cells and re.match(r"^[А-Я]\)$", cells[0]):
            left.append(cells[1] if len(cells) > 1 else "")
            if len(cells) >= 4 and re.match(r"^\d+[.)]$", cells[2]):
                right.append(cells[3])
            elif len(cells) == 3 and re.match(r"^\d+[.)]", cells[2]):
                right.append(re.sub(r"^\d+[.)]\s*", "", cells[2]))
        elif not left and len(cells) == 2:
            heads = cells
    if not left:
        return "".join(f"<p>{dr.esc(x)}</p>" for x in lines)
    html = f"<p><strong>{dr.esc(heads[0])}</strong></p>" if heads else ""
    html += _ul(left, LETTERS)
    if right:
        if heads:
            html += f"<p><strong>{dr.esc(heads[1])}</strong></p>"
        html += _ul(right, None)
    return html


def table_html(table: Table, cols: list[list[str]]) -> str:
    """Таблица условия: однострочная из ≥2 колонок — перечень, по колонкам сверху вниз."""
    if len(table.rows) == 1 and len(cols) >= 2:
        return _ul([x for col in cols for x in col], None)
    if len(cols) >= 2 and len(cols[0]) > 1:
        return matching_html(cols)
    return "".join(f"<p>{dr.esc(t)}</p>" for col in cols for t in col)


def _is_key_line(block: Paragraph | Table | None) -> bool:
    """Абзац-ключ: «List Paragraph» из цифр/дроби."""
    return (
        isinstance(block, Paragraph)
        and dr.style_name(block) == dr.LIST_STYLE
        and clean_key(dr.para_text(block)) is not None
    )


def parse_keys(blocks: list[Paragraph | Table], start: int) -> list[str | None]:
    """Ключи после заголовка «Ответы:» — по одному на блок, по порядку.

    Две записи автора: список «List Paragraph» из чисел и строки «1) 1423».
    Пункт списка без числа («Задание с развёрнутым ответом») — блок без ключа.
    """
    head = dr.para_text(blocks[start])  # type: ignore[arg-type]
    numbered: dict[int, str] = {int(n): k for n, k in _NUMBERED_KEY.findall(head)}
    seq: list[str | None] = []
    for block in blocks[start + 1:]:
        if not isinstance(block, Paragraph):
            break
        text = dr.para_text(block)
        if not text:
            continue
        m = _NUMBERED_KEY.fullmatch(text)
        if m:
            numbered[int(m.group(1))] = m.group(2)
        elif dr.style_name(block) == dr.LIST_STYLE:
            seq.append(clean_key(text))
        else:
            break
    if numbered:
        return [clean_key(numbered.get(i, "")) for i in range(1, max(numbered) + 1)]
    return seq


def parse_homework(data: bytes, label: str = "домашняя работа") -> HomeworkResult:
    """Разобрать ДЗ на блоки и сопоставить им ключи автора по порядку."""
    doc = dr.open_docx(data, label)
    all_blocks = dr.blocks(doc)
    res = HomeworkResult()
    cur: HwBlock | None = None
    options: list[str] = []
    tabbed: list[str] = []
    keys: list[str | None] = []

    def flush() -> None:
        if cur is not None and options:
            cur.html.append(_ul(options, None))
        if cur is not None and tabbed:
            cur.html.append(tabbed_html(tabbed))
        options.clear()
        tabbed.clear()

    for i, block in enumerate(all_blocks):
        if isinstance(block, Table):
            cols = dr.table_columns(block)
            if cur is None or is_answer_table(cols):
                continue
            flush()
            cur.html.append(table_html(block, cols))
            continue
        text = dr.para_text(block)
        if not text:
            continue
        nxt = all_blocks[i + 1] if i + 1 < len(all_blocks) else None
        is_answer_head = text.startswith("Ответ")
        if is_answer_head and (text.startswith("Ответы") or _is_key_line(nxt)):
            flush()
            cur = None
            keys = parse_keys(all_blocks, i)
            break
        if is_answer_head and text.rstrip(": ").strip() == "Ответ":
            continue  # подпись к пустой таблице под ответ
        m = _BLOCK_RE.search(text) or _BLOCK_HEAD_RE.match(text)
        if m:
            flush()
            cur = HwBlock(num=int(m.group(1)))
            res.blocks.append(cur)
            continue
        if cur is None:
            continue
        if dr.style_name(block) == dr.LIST_STYLE:
            options.append(re.sub(r"^[-–—]\s*", "", text))
        elif "\t" in text:
            if options:
                cur.html.append(_ul(options, None))
                options.clear()
            tabbed.append(text)
        else:
            flush()
            cur.html.append(f"<p>{dr.esc(text)}</p>")
    flush()
    _attach_keys(res, keys)
    return res


def _attach_keys(res: HomeworkResult, keys: list[str | None]) -> None:
    """Ключи по порядку блоков; расхождение числа — предупреждение, не ошибка."""
    if not res.blocks:
        res.warnings.append(
            "Домашняя работа: не найдено ни одного блока («К блоку №N») — раскладка файла незнакомая"
        )
        return
    if not keys:
        res.warnings.append("Домашняя работа: нет ключей «Ответы:» — все блоки уйдут на ручную проверку")
    elif len(keys) != len(res.blocks):
        res.warnings.append(
            f"Домашняя работа: ключей {len(keys)}, блоков {len(res.blocks)} — проверьте, к какому блоку какой ключ"
        )
    for block, key in zip(res.blocks, keys):
        block.key = key
