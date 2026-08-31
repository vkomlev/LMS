"""Конвертер учебного markdown в HTML под белый список SPW (tsk-747).

Зачем это нужно. SPW рендерит `material.content.text` как HTML
(`components/material/MaterialViewer.tsx`, ветка `type == "text"`), а поле
`content.format` не проверяет вовсе. Материал, записанный в markdown, ученик
видит сырьём: решётки заголовков, звёздочки жирного и тройные кавычки
код-блоков остаются в тексте, а переводы строк схлопываются в одну простыню.
Поэтому markdown конвертируется до записи в базу, а не при показе.

Целевой набор тегов — ровно белый список `D:\\Work\\SPW\\lib\\material\\sanitize.ts`:
`p, ul, ol, li, strong, em, code, pre, blockquote, br, hr, h1..h6,
table, thead, tbody, tr, td, th, a, img, span, div`. Всё, что вне списка,
DOMPurify молча вырежет — поэтому конвертер за него не выходит.

Сознательные ограничения (каждое — из уже наступленных граблей):

* `*курсив*` НЕ поддерживается. Одиночная звёздочка в учебных текстах по
  Python — это оператор возведения в степень (`2 ** 3`, `4**512`), и любая
  попытка отличить его от разметки перебором даёт ложные срабатывания
  (tsk-215). Курсив пишется явным `<em>` в исходнике или не пишется вовсе.
* Уровни заголовков нормализуются так, чтобы верхний стал `<h2>`: заголовок
  первого уровня в материале дублирует `material.title`, а SPW срезает именно
  ведущий `<h2>` (tsk-217, `lib/material/strip-title-heading.ts`).
* Весь текст вне код-блоков экранируется. Голая `<` в учебном тексте про
  ветвление («< ромб >») иначе съедает кусок урока целиком.
"""
from __future__ import annotations

import html
import re
from typing import List, Optional, Tuple

__all__ = ["markdown_to_html", "looks_like_markdown", "contains_html_markup"]

#: Реальный тег из белого списка SPW — зеркало `HTML_TAG_RE` из
#: `components/task/TaskContentRenderer.tsx`. Наличие такого тега означает, что
#: материал уже написан в HTML и конвертировать его НЕЛЬЗЯ: конвертер экранирует
#: разметку, и весь HTML стал бы литеральным текстом на экране ученика.
_HTML_TAG_RE = re.compile(
    r"</?(p|ol|ul|li|strong|b|em|i|u|s|code|pre|blockquote|br|hr|h[1-6]"
    r"|table|thead|tbody|tr|td|th|a|img|span|div)\b[^>]*>",
    re.IGNORECASE,
)
#: Содержимое `<pre>`/`<code>`: там `#` — комментарий примера, а не заголовок.
_HTML_CODE_RE = re.compile(r"<(pre|code)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)

#: Открывающая строка код-блока: ```` ```python ```` или просто ```` ``` ````.
_FENCE_OPEN_RE = re.compile(r"^\s*```([A-Za-z0-9_+\-]*)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_UL_ITEM_RE = re.compile(r"^\s*[-+]\s+(.*)$")
_OL_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
#: Строгий предикат «настоящий парный жирный на границе слова» — зеркало правила
#: из `scripts/fix_stem_markdown_bold_tsk212.py`. Открывающая пара не может стоять
#: сразу после буквы или цифры, закрывающая — сразу перед ними: иначе питоновская
#: степень `4**512 + 8**512` схлопнулась бы в «512 + 8» жирным (tsk-212/tsk-215).
_BOLD_RE = re.compile(r"(?<![\w*])\*\*(?!\s)([^*]+?)(?<!\s)\*\*(?![\w*])", re.S)

#: Плейсхолдер код-блока в потоке строк. Управляющие символы выбраны так, чтобы
#: не встречаться в учебном тексте и пережить экранирование.
_FENCE_TOKEN = "\x01FENCE_{}\x02"
_FENCE_TOKEN_RE = re.compile(r"^\x01FENCE_(\d+)\x02$")


def contains_html_markup(text: str) -> bool:
    """Есть ли в тексте настоящий HTML-тег из белого списка SPW.

    :param text: тело материала (`content.text`).
    :return: True — материал уже HTML, конвертировать его нельзя.
    """
    return bool(text) and _HTML_TAG_RE.search(text) is not None


def looks_like_markdown(text: str) -> bool:
    """Грубый признак markdown-разметки в тексте материала.

    Используется для отбора кандидатов на переиздание и для проверки результата
    («разметки не осталось»), а не для решения «конвертировать ли»: решение
    принимает человек по предпросмотру. Из проверки исключены и markdown-ограды,
    и HTML-блоки `<pre>`/`<code>`: `# комментарий` в примере на Python
    заголовком не является. Текст с HTML-тегами markdown'ом не считается вовсе —
    иначе 62 материала курсов Python попали бы в переиздание из-за решёток в
    примерах кода (поймано предпросмотром 31.08.2026).

    :param text: тело материала (`content.text`).
    :return: True, если вне кода есть markdown-заголовок, жирный или ограда.
    """
    if not text or contains_html_markup(text):
        return False
    body, _ = _extract_fences(text.replace("\r\n", "\n").replace("\r", "\n"))
    joined = _HTML_CODE_RE.sub(" ", "\n".join(body))
    if re.search(r"(^|\n)\s*#{1,6}\s+\S", joined):
        return True
    if _BOLD_RE.search(joined):
        return True
    return "```" in text


def _extract_fences(text: str) -> Tuple[List[str], List[str]]:
    """Вынести код-блоки в отдельный список, оставив в тексте плейсхолдеры.

    :param text: исходный markdown с нормализованными переводами строк.
    :return: пара «строки текста с плейсхолдерами, готовые HTML код-блоков».
    """
    lines = text.split("\n")
    out: List[str] = []
    fences: List[str] = []
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        lang = m.group(1)
        body: List[str] = []
        i += 1
        while i < len(lines) and not _FENCE_CLOSE_RE.match(lines[i]):
            body.append(lines[i])
            i += 1
        i += 1  # закрывающая ограда (или конец текста — незакрытый блок)
        code = html.escape("\n".join(body).strip("\n"), quote=False)
        cls = f' class="language-{lang}"' if lang else ""
        fences.append(f"<pre><code{cls}>{code}</code></pre>")
        out.append(_FENCE_TOKEN.format(len(fences) - 1))
    return out, fences


#: Плейсхолдер инлайнового кода. Символ не-словесный, поэтому не мешает границам
#: слова в предикате жирного и не искажается экранированием.
_CODE_TOKEN = "\x03CODE_{}\x04"
_CODE_TOKEN_RE = re.compile(r"\x03CODE_(\d+)\x04")


def _inline(text: str) -> str:
    """Инлайновая разметка одной строки: `код`, **жирный**, экранирование.

    Код вырезается в стеш ДО разбора жирного, а не обрабатывается посегментно:
    иначе выделение, внутри которого стоит код (``**без самого `b`**``),
    рвалось бы на части и звёздочки оставались бы в тексте.

    :param text: строка исходника без блочной разметки.
    :return: безопасный HTML-фрагмент.
    """
    parts = text.split("`")
    stash: List[str] = []
    rebuilt: List[str] = []
    for idx, part in enumerate(parts):
        # Нечётные сегменты — внутри пары обратных кавычек. Непарная кавычка в
        # конце строки кодом не считается: последний сегмент возвращается текстом.
        if idx % 2 == 1 and idx < len(parts) - 1:
            stash.append(html.escape(part, quote=False))
            rebuilt.append(_CODE_TOKEN.format(len(stash) - 1))
        else:
            if idx % 2 == 1:
                rebuilt.append("`")
            rebuilt.append(html.escape(part, quote=False))

    out = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", "".join(rebuilt))
    return _CODE_TOKEN_RE.sub(lambda m: f"<code>{stash[int(m.group(1))]}</code>", out)


def _is_table(block: List[str]) -> bool:
    """Блок — markdown-таблица: минимум шапка, разделитель и одна строка."""
    return (
        len(block) >= 3
        and block[0].lstrip().startswith("|")
        and _TABLE_DELIM_RE.match(block[1])
        is not None
    )


def _table_html(block: List[str]) -> str:
    """Собрать `<table>` из markdown-таблицы (шапка + тело)."""

    def cells(row: str) -> List[str]:
        trimmed = row.strip()
        if trimmed.startswith("|"):
            trimmed = trimmed[1:]
        if trimmed.endswith("|"):
            trimmed = trimmed[:-1]
        return [c.strip() for c in trimmed.split("|")]

    head = "".join(f"<th>{_inline(c)}</th>" for c in cells(block[0]))
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(row)) + "</tr>"
        for row in block[2:]
        if row.strip()
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _flush_paragraph(buf: List[str], out: List[str]) -> None:
    """Слить накопленные строки абзаца в `<p>` (перевод строки → пробел)."""
    if not buf:
        return
    out.append("<p>" + _inline(" ".join(s.strip() for s in buf)) + "</p>")
    buf.clear()


def _flush_list(items: List[str], ordered: bool, out: List[str]) -> None:
    """Слить накопленные пункты в `<ul>`/`<ol>`."""
    if not items:
        return
    tag = "ol" if ordered else "ul"
    out.append(f"<{tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{tag}>")
    items.clear()


def _heading_shift(lines: List[str]) -> int:
    """Насколько поднять уровни заголовков, чтобы верхний стал `<h2>`.

    Сдвиг бывает и отрицательным: материал, у которого верхний уровень `###`
    (такие тоже есть на проде), поднимается до `<h2>`, иначе внутри страницы
    начинался бы с третьего уровня без второго.

    :param lines: строки материала с вырезанными код-блоками.
    :return: сдвиг уровня, может быть отрицательным.
    """
    levels = [len(m.group(1)) for m in (_HEADING_RE.match(l) for l in lines) if m]
    if not levels:
        return 0
    return 2 - min(levels)


def markdown_to_html(text: str, *, title: Optional[str] = None) -> str:
    """Преобразовать учебный markdown в HTML под белый список SPW.

    :param text: тело материала в markdown.
    :param title: заголовок материала; если первый заголовок текста совпадает
        с ним, он всё равно остаётся в HTML — срезает его уже SPW (tsk-217).
        Параметр оставлен для явности вызова и будущих проверок.
    :return: HTML-строка; при пустом входе — пустая строка.
    """
    if not text or not text.strip():
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines, fences = _extract_fences(normalized)
    shift = _heading_shift(lines)

    out: List[str] = []
    para: List[str] = []
    items: List[str] = []
    ordered = False
    block: List[str] = []

    def flush_block() -> None:
        """Разобрать накопленный блок строк (между пустыми строками)."""
        nonlocal ordered
        if not block:
            return
        if _is_table(block):
            _flush_paragraph(para, out)
            _flush_list(items, ordered, out)
            out.append(_table_html(block))
            block.clear()
            return
        for line in block:
            token = _FENCE_TOKEN_RE.match(line.strip())
            if token:
                _flush_paragraph(para, out)
                _flush_list(items, ordered, out)
                out.append(fences[int(token.group(1))])
                continue
            if _HR_RE.match(line):
                _flush_paragraph(para, out)
                _flush_list(items, ordered, out)
                out.append("<hr>")
                continue
            heading = _HEADING_RE.match(line)
            if heading:
                _flush_paragraph(para, out)
                _flush_list(items, ordered, out)
                level = min(6, max(2, len(heading.group(1)) + shift))
                out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
                continue
            ul = _UL_ITEM_RE.match(line)
            ol = _OL_ITEM_RE.match(line)
            if ul or ol:
                _flush_paragraph(para, out)
                want_ordered = ol is not None
                if items and want_ordered != ordered:
                    _flush_list(items, ordered, out)
                ordered = want_ordered
                items.append((ol or ul).group(1).strip())
                continue
            if items:
                # Продолжение последнего пункта списка (висячий отступ).
                items[-1] = items[-1] + " " + line.strip()
                continue
            para.append(line)
        _flush_paragraph(para, out)
        _flush_list(items, ordered, out)
        block.clear()

    for line in lines:
        if not line.strip():
            flush_block()
            continue
        block.append(line)
    flush_block()

    return "\n".join(out)
