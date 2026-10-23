"""tsk-1255: конспект урока (docx) → HTML материала.

Раскладка автора: «БЛОК …» — раздел, строка с двоеточием на конце или
«Пример…/Вариант…» — подзаголовок, «List Paragraph» — пункт списка,
таблица — врезка. Итоговая строка таблицы («Итого по…») не переносится.
"""

from __future__ import annotations

from docx.table import Table

from app.services.lesson_import import docx_reader as dr


def konspekt_html(data: bytes, label: str = "конспект") -> str:
    """HTML конспекта; текст экранирован, цвет и ссылки автора сохранены."""
    doc = dr.open_docx(data, label)
    targets = dr.hyperlink_targets(doc)
    out: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    for block in dr.blocks(doc):
        if isinstance(block, Table):
            text = " ".join(dr.cell_text(c) for row in block.rows for c in row.cells).strip()
            if not text or text.startswith("Итого по"):
                continue
            close_list()
            out.append(f'<div class="vrezka"><p>{dr.esc(text)}</p></div>')
            continue
        html = dr.para_html(block, targets)
        if not html:
            continue
        plain = dr.para_text(block)
        if dr.style_name(block) == dr.LIST_STYLE:
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{html}</li>")
            continue
        close_list()
        if plain.startswith(("БЛОК ", "Блок ")):
            out.append(f"<h2>{html}</h2>")
        elif plain.endswith(":") or plain.startswith(("Вариант ", "Пример")):
            out.append(f"<h3>{html}</h3>")
        else:
            out.append(f"<p>{html}</p>")
    close_list()
    return "\n".join(out)
