# -*- coding: utf-8 -*-
"""tsk-1320, второй проход: списки в условиях 2997 и 2998.

2997 — ходы игры («убрать из кучи три камня…») автор канала оформил блоком кода;
это список, а длинная строка уходила под горизонтальную прокрутку → ``<ul><li>``.
2998 — команды Черепахи шли одним абзацем → вводный абзац, «У исполнителя
существует 6 команд:», список (имена команд жирным, как у соседних ``wp_nav:6``),
отдельный абзац «Запись Повтори k […]».

Слова не меняются: сверка текста без тегов до/после (``visible_words`` из
первого прохода). Снимок до/после — ``docs/qa/2026-10-12-tsk1320-snimok-spiski.json``.

Запуск (после протокола /db-check):
    python scripts/tsk1320_fix_lists_2997_2998.py --dry-run
    DBCHECK_OK=1 python scripts/tsk1320_fix_lists_2997_2998.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from tsk1320_fix_fence_stems import load_prod_dsn, visible_words  # noqa: E402

SNAPSHOT_PATH = PROJECT_ROOT / "docs" / "qa" / "2026-10-12-tsk1320-snimok-spiski.json"
logger = logging.getLogger("tsk1320-fix-lists")

def sp(text: str) -> str:
    """Регулярка для фразы, где любой пробел может быть обычным или неразрывным (ТГ-пост)."""
    return r"[   ]+".join(re.escape(w) for w in text.split(" "))


MOVES_RE = re.compile(r'</p><pre><code class="language-text">(' + sp("убрать из кучи три камня") + r"\n.*?)</code></pre>", re.S)
CMD_HEAD_RE = re.compile(r"[   ]+" + sp("У исполнителя существует 6 команд:") + r"[   ]+")
CMD_TAIL_RE = re.compile(r"[   ]+(" + sp("Запись") + r"[   ]+(" + sp("Повтори k [Команда1 Команда2 ... КомандаS]") + r"))(?=[   ]+" + sp("означает,") + ")")
CMD_SPLIT_RE = re.compile(r"(?<=;)[   ]+(?=\S)|(?<=стрелке,)[   ]+(?=Налево)")
CMD_NAME_RE = re.compile("^(" + "|".join(sp(n) for n in ("Поднять хвост", "Опустить хвост", "Вперёд n", "Назад n", "Направо t", "Налево t")) + ")")


def fix_2997(stem: str) -> str:
    """Блок кода с ходами игры → маркированный список."""
    found = MOVES_RE.findall(stem)
    if len(found) != 1:
        raise ValueError(f"2997: блок ходов найден {len(found)} раз(а)")
    items = "".join(f"<li>{ln.strip()}</li>" for ln in found[0].split("\n") if ln.strip())
    return MOVES_RE.sub(lambda _m: f"</p><ul>{items}</ul>", stem, count=1)


def fix_2998(stem: str) -> str:
    """Сплошной абзац с командами → абзац, заголовок списка, список, абзац «Запись…»."""
    heads, tails = CMD_HEAD_RE.findall(stem), CMD_TAIL_RE.findall(stem)
    if len(heads) != 1 or len(tails) != 1:
        raise ValueError(f"2998: якоря списка команд найдены {len(heads)}/{len(tails)} раз, нужно 1/1")
    head = CMD_HEAD_RE.search(stem)
    tail_m = CMD_TAIL_RE.search(stem, head.end())
    if tail_m is None:
        raise ValueError("2998: «Запись Повтори…» не после списка команд")
    before, middle, after = stem[: head.start()], stem[head.end(): tail_m.start()], stem[tail_m.end():]
    head_text = head.group(0).strip("   ")
    items = CMD_SPLIT_RE.split(middle)
    if len(items) != 6:
        raise ValueError(f"2998: команд {len(items)}, ожидалось 6")
    lis = []
    for item in items:
        m = CMD_NAME_RE.match(item)
        if not m:
            raise ValueError(f"2998: не распознано имя команды: {item[:40]}")
        lis.append(f"<li><strong>{m.group(1)}</strong>{item[m.end():]}</li>")
    full, repeat = tail_m.group(1), tail_m.group(2)
    tail = full.replace(repeat, f"<strong>{repeat}</strong>", 1)
    return f"{before}</p><p>{head_text}</p><ul>{''.join(lis)}</ul><p>{tail}{after}"


def fix_2998_spaces(stem: str) -> str:
    """Узкий неразрывный пробел (U+202F) из ТГ-поста → обычный: в SPW он почти не виден («Вперёдn»)."""
    if " " not in stem:
        raise ValueError("2998: узких неразрывных пробелов нет — уже исправлено?")
    return stem.replace(" ", " ")


FIXES = {2997: fix_2997, 2998: fix_2998}


def words(stem: str) -> str:
    """Текст без тегов для сверки; пробел перед знаком препинания не в счёт (его даёт снятый тег)."""
    return re.sub(r"\s+(?=[,.;:!?)])", "", visible_words(stem))


async def main_async(apply: bool) -> int:
    """План/снимок без записи; с apply — запись в одной транзакции и сверка после."""
    import asyncpg

    conn = await asyncpg.connect(load_prod_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, task_content, content_provenance FROM tasks WHERE id = ANY($1::int[]) AND is_active ORDER BY id",
            list(FIXES),
        )
        if len(rows) != len(FIXES):
            logger.error("Найдено %d активных из %d — стоп", len(rows), len(FIXES))
            return 2

        plan: list[dict] = []
        for r in rows:
            tc = json.loads(r["task_content"]) if isinstance(r["task_content"], str) else r["task_content"]
            old = tc.get("stem") or ""
            new = FIXES[r["id"]](old)
            if words(old) != words(new):
                logger.error("id=%s: текст без тегов разошёлся со старым — стоп", r["id"])
                return 1
            if re.search(r"<p>\s*</p>|<p><p>|</p></p>", new):
                logger.error("id=%s: пустой <p> — стоп", r["id"])
                return 1
            logger.info("--- id=%s ---\nСТАНЕТ: %s", r["id"], new)
            prov = r["content_provenance"]
            plan.append({
                "id": r["id"],
                "stem_before": old,
                "stem_after": new,
                "content_provenance_before": json.loads(prov) if isinstance(prov, str) else prov,
            })

        SNAPSHOT_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("Снимок до/после: %s", SNAPSHOT_PATH)
        if not apply:
            logger.info("Без --apply: только план. Для записи — DBCHECK_OK=1 ... --apply")
            return 0

        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        by_id = {r["id"]: r for r in rows}
        async with conn.transaction():
            for item in plan:
                tc = by_id[item["id"]]["task_content"]
                tc = json.loads(tc) if isinstance(tc, str) else tc
                tc["stem"] = item["stem_after"]
                prev = dict(item["content_provenance_before"] or {})
                prov = {
                    "source": "manual_script",
                    "edited_at": stamp,
                    "edited_by": "tsk-1320",
                    "fields": ["task_content"],
                    "reason": "второй проход: ходы игры / команды Черепахи оформлены списком <ul>; слова не менялись",
                    "previous": prev.get("previous", prev),
                }
                status = await conn.execute(
                    "UPDATE tasks SET task_content=$1::jsonb, content_provenance=$2::jsonb, updated_at=now() "
                    "WHERE id=$3 AND task_content->>'stem' = $4",
                    json.dumps(tc, ensure_ascii=False), json.dumps(prov, ensure_ascii=False),
                    item["id"], item["stem_before"],
                )
                if status != "UPDATE 1":
                    raise RuntimeError(f"id={item['id']}: {status} — stem изменился после чтения")

        after = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", list(FIXES)
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
    parser.add_argument("--spaces", action="store_true",
                        help="третий проход: только узкие неразрывные пробелы в 2998")
    args = parser.parse_args()
    if args.spaces:
        global FIXES, SNAPSHOT_PATH
        FIXES = {2998: fix_2998_spaces}
        SNAPSHOT_PATH = SNAPSHOT_PATH.with_name("2026-10-12-tsk1320-snimok-probely.json")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
