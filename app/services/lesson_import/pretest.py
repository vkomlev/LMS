"""tsk-1255: пре-тест (docx) → утверждения «Верно/Неверно».

Раскладка автора: таблица «номер | утверждение», ниже абзац
«Ключ: 1) + 2) − …». Утверждение без ключа не выбрасывается — уходит в
черновик с предупреждением, ключ ставит методист в предпросмотре.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from docx.table import Table

from app.services.lesson_import import docx_reader as dr

_KEY_ITEM = re.compile(r"(\d+)\)\s*([+−–—-])")


@dataclass
class Statement:
    """Одно утверждение пре-теста."""

    num: int
    text: str
    correct: bool | None  # None — ключа в файле нет


@dataclass
class PretestResult:
    """Разобранный пре-тест и предупреждения для предпросмотра."""

    statements: list[Statement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_pretest(data: bytes, label: str = "пре-тест") -> PretestResult:
    """Разобрать пре-тест; нестыковки ключа — в предупреждения, не в исключение."""
    doc = dr.open_docx(data, label)
    rows: list[tuple[int, str]] = []
    key: dict[int, bool] = {}
    for block in dr.blocks(doc):
        if isinstance(block, Table):
            for row in block.rows:
                cells = [dr.cell_text(c) for c in row.cells]
                if len(cells) >= 2 and cells[0].isdigit() and cells[1]:
                    rows.append((int(cells[0]), cells[1]))
            continue
        text = dr.para_text(block)
        if text.startswith("Ключ"):
            for num, sign in _KEY_ITEM.findall(text):
                key[int(num)] = sign == "+"

    result = PretestResult(
        statements=[Statement(num, text, key.get(num)) for num, text in rows]
    )
    if not rows:
        result.warnings.append(
            "Пре-тест: не найдена таблица «номер | утверждение» — раскладка файла незнакомая"
        )
    missing = [s.num for s in result.statements if s.correct is None]
    if missing:
        result.warnings.append(
            "Пре-тест: нет ключа для утверждений " + ", ".join(map(str, missing))
            + " — выберите верный ответ вручную"
        )
    extra = sorted(set(key) - {n for n, _ in rows})
    if extra:
        result.warnings.append(
            "Пре-тест: в ключе есть номера без утверждений: " + ", ".join(map(str, extra))
        )
    return result
