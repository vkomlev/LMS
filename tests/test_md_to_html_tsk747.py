"""tsk-747: конвертер учебного markdown в HTML под белый список SPW.

Проверяется то, из-за чего материалы показывались ученику сырьём, и то, что
конвертер не ломает вещи, на которых уже обжигались: оператор возведения в
степень (tsk-215), голая `<` в тексте про ветвление, `#` внутри примера кода.
"""
from __future__ import annotations

from app.utils.md_to_html import (
    contains_html_markup,
    looks_like_markdown,
    markdown_to_html,
)


def test_headings_normalized_to_h2() -> None:
    """Верхний уровень заголовков становится h2 — под срез дубля title в SPW."""
    html = markdown_to_html("# Заголовок\n\nтекст\n\n## Раздел\n\nещё")
    assert "<h2>Заголовок</h2>" in html
    assert "<h3>Раздел</h3>" in html


def test_headings_shift_when_document_starts_from_h3() -> None:
    """Материал без `#` (верхний уровень — `###`) тоже начинается с h2."""
    html = markdown_to_html("### Высказывание\n\nтекст\n\n### Три связки\n\nещё")
    assert html.count("<h2>") == 2
    assert "<h3>" not in html


def test_bold_and_inline_code() -> None:
    html = markdown_to_html("Правая граница **не входит**, см. `range(a, b)`.")
    assert "<strong>не входит</strong>" in html
    assert "<code>range(a, b)</code>" in html


def test_power_operator_is_not_bold() -> None:
    """`2 ** 3` — возведение в степень, а не разметка (грабли tsk-215)."""
    html = markdown_to_html("Возведение в степень: 2 ** 3 = 8, а также 4**512 + 8**512.")
    assert "<strong>" not in html
    assert "2 ** 3 = 8" in html


def test_bold_inside_code_span_is_literal() -> None:
    html = markdown_to_html("Пример: `a ** b` — степень.")
    assert "<code>a ** b</code>" in html
    assert "<strong>" not in html


def test_bold_wrapping_inline_code() -> None:
    """Выделение, внутри которого стоит код, не должно рваться на части."""
    html = markdown_to_html("доходит до `b`, но **без самого `b`** — вот так")
    assert "<strong>без самого <code>b</code></strong>" in html
    assert "**" not in html


def test_unpaired_backtick_stays_literal() -> None:
    html = markdown_to_html("осталась `непарная кавычка")
    assert "<code>" not in html
    assert "`непарная кавычка" in html


def test_angle_brackets_escaped_outside_code() -> None:
    """Голая `<` не должна съедать остаток текста."""
    html = markdown_to_html("Из ромба < условие > выходят две стрелки: да и нет.")
    assert "&lt; условие &gt;" in html
    assert "выходят две стрелки" in html


def test_fence_becomes_pre_code_with_language() -> None:
    html = markdown_to_html("```python\nprint(10 // 3)\n```")
    assert '<pre><code class="language-python">print(10 // 3)</code></pre>' in html


def test_fence_content_is_escaped() -> None:
    html = markdown_to_html("```\n< ромб >  --  да & нет\n```")
    assert "&lt; ромб &gt;" in html
    assert "&amp; нет" in html


def test_hash_inside_fence_is_not_heading() -> None:
    """`# комментарий` в примере кода остаётся комментарием."""
    html = markdown_to_html("```python\n# считаем сумму\ns = 0\n```")
    assert "<h2>" not in html
    assert "# считаем сумму" in html


def test_unordered_list() -> None:
    html = markdown_to_html("**Итог:**\n- первое;\n- второе.")
    assert "<p><strong>Итог:</strong></p>" in html
    assert "<ul><li>первое;</li><li>второе.</li></ul>" in html


def test_ordered_list() -> None:
    html = markdown_to_html("1. раз\n2. два")
    assert "<ol><li>раз</li><li>два</li></ol>" in html


def test_table() -> None:
    md = "| Тип | Пример |\n|---|---|\n| `int` | `6` |\n| `str` | текст |"
    html = markdown_to_html(md)
    assert "<table><thead><tr><th>Тип</th><th>Пример</th></tr></thead>" in html
    assert "<td><code>int</code></td>" in html
    assert "<td>текст</td>" in html


def test_hr() -> None:
    assert "<hr>" in markdown_to_html("текст\n\n---\n\nещё текст")


def test_paragraph_joins_wrapped_lines() -> None:
    """Перенос строки внутри абзаца — оформление исходника, а не разрыв."""
    html = markdown_to_html("Первая половина\nвторая половина.")
    assert "<p>Первая половина вторая половина.</p>" == html


def test_no_markdown_syntax_left() -> None:
    """На выходе не остаётся разметки, которую ученик видел сырьём."""
    md = (
        "# Где заканчивается range\n\n"
        "**После этого материала** ты сможешь.\n\n"
        "## Правая граница\n\n"
        "```\nrange(1, 5)\n```\n\n"
        "- первое;\n- второе."
    )
    html = markdown_to_html(md)
    assert "```" not in html
    assert "**" not in html
    assert not any(line.startswith("#") for line in html.split("\n"))


def test_only_whitelisted_tags() -> None:
    """Конвертер не выходит за белый список sanitize.ts."""
    import re

    allowed = {
        "p", "ol", "ul", "li", "strong", "b", "em", "i", "u", "s",
        "code", "pre", "blockquote", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "thead", "tbody", "tr", "td", "th",
        "a", "img", "span", "div",
    }
    md = (
        "# Заголовок\n\ntекст **жирный** и `код`\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "---\n\n- пункт\n\n```python\nx = 1\n```"
    )
    html = markdown_to_html(md)
    used = {t.lower() for t in re.findall(r"</?([a-zA-Z0-9]+)", html)}
    assert used <= allowed, f"вне белого списка: {used - allowed}"


def test_empty_input() -> None:
    assert markdown_to_html("") == ""
    assert markdown_to_html("   \n  ") == ""


def test_looks_like_markdown_ignores_code_comments() -> None:
    """HTML-материал с `# комментарий` в примере кода — не markdown."""
    assert not looks_like_markdown("<p>текст</p>\n<pre><code># сумма\ns = 0</code></pre>")
    assert looks_like_markdown("# Заголовок\n\nтекст")
    assert looks_like_markdown("текст **жирный**")
    assert not looks_like_markdown("<p>степень 2 ** 3</p>")


def test_looks_like_markdown_rejects_html_material() -> None:
    """Текст с HTML-тегами markdown'ом не считается ни при каких признаках.

    На проде 31.08.2026 широкая эвристика дала 68 кандидатов вместо 6: решётки
    и звёздочки жили внутри `<pre><code>` материалов курсов Python.
    """
    html_material = (
        "<h3>Как посчитать сумму</h3>\n"
        "<pre><code class=\"language-python\"># сумма чисел\n"
        "s = 0\nprint(2 ** 3)</code></pre>"
    )
    assert contains_html_markup(html_material)
    assert not looks_like_markdown(html_material)


def test_contains_html_markup_on_plain_markdown() -> None:
    assert not contains_html_markup("# Заголовок\n\n**жирный** и `код`")
    assert not contains_html_markup("сравнение a < b и c > d")
