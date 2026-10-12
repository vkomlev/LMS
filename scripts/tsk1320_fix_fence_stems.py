# -*- coding: utf-8 -*-
"""tsk-1320: блок ``` в HTML-условии 12 ЕГЭ-заданий «Черепаха» / входной файл.

SPW рисует stem с HTML-тегами как HTML и markdown не разбирает: ученик видит
кавычки ```, строки алгоритма склеены в одну, а у заданий ``wp_nav:*`` склеено
всё условие — абзацы и список команд разделены только ``\\r\\n`` внутри одного
``<p>``. Находка линтера CB ``markdown-fence-in-html-stem`` (tsk-1313).

Правка приводит каждый ``<p>``, где есть переносы, ``<br>`` или ```, к HTML:
  * пустая строка (или ``<br> <br>``) → граница абзаца ``</p><p>``;
  * перенос строки внутри абзаца → ``<br>``; хвостовые «два пробела» и ``\\``
    (markdown-переносы) снимаются;
  * блок ```…``` → ``<pre><code class="language-text">код</code></pre>`` вне ``<p>``;
    в коде экранируются только ``&<>``, кавычки буквально (линкификатор SPW
    съедает ``&quot`` после URL, tsk-1313);
  * инлайн `x` → ``<code>x</code>``.
Слова не меняются: прогон сверяет текст без тегов до и после.

Повторный импорт правку не затрёт: ``tg_ege_import`` и ``wp_nav_import`` работают
«только создание» (tsk-734), существующие external_uid пропускают.

Запуск (после протокола /db-check):
    python scripts/tsk1320_fix_fence_stems.py            # план + снимок, без записи
    DBCHECK_OK=1 python scripts/tsk1320_fix_fence_stems.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT_ROOT / "docs" / "qa" / "2026-10-12-tsk1320-snimok-stem.json"
TASK_IDS: list[int] = [2997, 2998, 3476, 3692, 3711, 3715, 3716, 3725, 3730, 3737, 3742, 3749]

logger = logging.getLogger("tsk1320-fix-fence-stems")

FENCE = "`" * 3
P_RE = re.compile(r"<p>(.*?)</p>", re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)
FENCE_RE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)\n[ \t]*```[ \t]*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n<]+)`(?!`)")
PLACEHOLDER = "\x00PRE{}\x00"
PLACEHOLDER_RE = re.compile(r"^\x00PRE(\d+)\x00$")


def load_prod_dsn() -> str:
    """Достать DSN прод-БД из .mcp.json (сервер learn_prod_db) с проверкой хоста."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return f"postgresql://{parts.username}:{unquote(parts.password or '')}@{parts.hostname}:{parts.port}{parts.path}"


def _code_block(raw: str) -> str:
    """Собрать <pre><code> из сырого текста блока: кавычки буквально, &<> экранированы."""
    lines = [ln.rstrip() for ln in html.unescape(raw).split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    code = html.escape("\n".join(lines), quote=False)
    return f'<pre><code class="language-text">{code}</code></pre>'


def _clean_line(line: str) -> str:
    """Снять markdown-переносы (хвостовые пробелы и обратную косую) и инлайн-код."""
    line = line.strip()
    if line.endswith("\\"):
        line = line[:-1].rstrip()
    return INLINE_CODE_RE.sub(r"<code>\1</code>", line)


def convert_paragraph(inner: str) -> str:
    """Превратить содержимое одного <p> с переносами/блоками в цепочку <p>/<pre>."""
    text = BR_RE.sub("\n", inner.replace("\r\n", "\n").replace("\r", "\n"))
    blocks: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        blocks.append(_code_block(m.group(2)))
        return "\n" + PLACEHOLDER.format(len(blocks) - 1) + "\n"

    text = FENCE_RE.sub(_stash, text)
    if FENCE in text:
        raise ValueError("остался непарный ``` — разбор не сошёлся")

    out: list[str] = []
    para: list[str] = []

    def _flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    for line in text.split("\n"):
        m = PLACEHOLDER_RE.match(line.strip())
        if m:
            _flush()
            out.append(blocks[int(m.group(1))])
            continue
        cleaned = _clean_line(line)
        if not cleaned:
            _flush()
            continue
        para.append(cleaned)
    _flush()
    return "".join(out)


def convert_stem(stem: str) -> str:
    """Переписать все <p>, где есть переносы, <br> или ```; остальное не трогать."""
    def _repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if FENCE in inner or "\n" in inner or "\r" in inner or BR_RE.search(inner):
            return convert_paragraph(inner)
        return m.group(0)

    return P_RE.sub(_repl, stem)


def visible_words(stem: str) -> str:
    """Текст без тегов, кавычек-ограждений и markdown-переносов — для сверки «слова не изменились»."""
    s = stem.replace(FENCE, " ")
    s = re.sub(r"\\\s*(\r?\n)", r" \1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("`", " ")
    return re.sub(r"\s+", " ", s).strip()


def check(old: str, new: str) -> list[str]:
    """Вернуть список нарушений инвариантов правки (пустой — всё в порядке)."""
    problems: list[str] = []
    if FENCE in new:
        problems.append("в новом stem остался ```")
    fences = old.count(FENCE) // 2
    pres = new.count("<pre><code")
    if fences != pres:
        problems.append(f"блоков в исходнике {fences}, <pre> в новом {pres}")
    if visible_words(old) != visible_words(new):
        problems.append("текст без тегов разошёлся со старым")
    if re.search(r"<p>\s*</p>", new):
        problems.append("пустой <p>")
    outside_pre = re.sub(r"<pre>.*?</pre>", "", new, flags=re.S).replace(">\n<", "><")
    if "\r" in outside_pre or "\n" in outside_pre:
        problems.append("вне <pre> остались сырые переносы")
    return problems


async def main_async(apply: bool) -> int:
    """План/снимок без записи; с apply — запись в одной транзакции и сверка после."""
    import asyncpg

    conn = await asyncpg.connect(load_prod_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, task_content, content_provenance FROM tasks "
            "WHERE id = ANY($1::int[]) AND is_active ORDER BY id",
            TASK_IDS,
        )
        if len(rows) != len(TASK_IDS):
            logger.error("Найдено %d активных из %d — состав изменился, стоп", len(rows), len(TASK_IDS))
            return 2

        plan: list[dict] = []
        failed = False
        for r in rows:
            tc = json.loads(r["task_content"]) if isinstance(r["task_content"], str) else r["task_content"]
            old = tc.get("stem") or ""
            new = convert_stem(old)
            problems = check(old, new)
            logger.info("--- id=%s %s ---", r["id"], r["external_uid"])
            logger.info("СТАНЕТ: %s", new)
            if problems:
                failed = True
                logger.error("id=%s НАРУШЕНИЯ: %s", r["id"], "; ".join(problems))
            prov = r["content_provenance"]
            plan.append({
                "id": r["id"],
                "external_uid": r["external_uid"],
                "stem_before": old,
                "stem_after": new,
                "content_provenance_before": json.loads(prov) if isinstance(prov, str) else prov,
            })

        SNAPSHOT_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("Снимок до/после: %s", SNAPSHOT_PATH)
        if failed:
            logger.error("Есть нарушения — запись запрещена")
            return 1
        if not apply:
            logger.info("Без --apply: только план. Для записи — DBCHECK_OK=1 ... --apply")
            return 0

        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        by_id = {r["id"]: r for r in rows}
        async with conn.transaction():
            for item in plan:
                row = by_id[item["id"]]
                tc = json.loads(row["task_content"]) if isinstance(row["task_content"], str) else row["task_content"]
                if (tc.get("stem") or "") != item["stem_before"]:
                    raise RuntimeError(f"id={item['id']}: stem изменился после чтения")
                tc["stem"] = item["stem_after"]
                prov = {
                    "source": "manual_script",
                    "edited_at": stamp,
                    "edited_by": "tsk-1320",
                    "fields": ["task_content"],
                    "reason": "блок ``` и markdown-переносы в HTML-условии приведены к <p>/<br>/<pre><code> "
                    "(линтер CB markdown-fence-in-html-stem); слова не менялись",
                    "previous": item["content_provenance_before"],
                }
                status = await conn.execute(
                    "UPDATE tasks SET task_content=$1::jsonb, content_provenance=$2::jsonb, updated_at=now() "
                    "WHERE id=$3",
                    json.dumps(tc, ensure_ascii=False), json.dumps(prov, ensure_ascii=False), item["id"],
                )
                if status != "UPDATE 1":
                    raise RuntimeError(f"id={item['id']}: {status}")

        after = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", TASK_IDS
        )
        expected = {p["id"]: p["stem_after"] for p in plan}
        bad = [a["id"] for a in after if a["stem"] != expected[a["id"]]]
        logger.info("Проверка после записи: совпало %d из %d%s", len(after) - len(bad), len(after),
                    f", расхождения: {bad}" if bad else "")
        return 1 if bad else 0
    finally:
        await conn.close()


def main() -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="выполнить запись")
    mode.add_argument("--dry-run", action="store_true", help="только план и снимок (по умолчанию)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
