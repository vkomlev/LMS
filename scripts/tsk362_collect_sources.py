# -*- coding: utf-8 -*-
"""tsk-362, шаг 1: собрать рабочий список непроверяемых заданий и разрешить их источник.

ЧТО ДЕЛАЕТ
Читает с прода LMS активные задания с «пустым правилом» (объект есть, но проверить им
нечего и в ручную задание не уйдёт) и для каждого определяет источник и ID задачи в нём.

Ключ источника берётся в порядке надёжности:
  1. `external_uid` вида `ext:<batch>:<source>:<date>:<id>` — источник и ID заданы явно;
  2. для `tg:*` — **канонический URL из самого поста Telegram** (ContentBackbone,
     `content_hub.source_item.raw`, сущность `text_link`): `kompege.ru/task?id=N`,
     `inf-ege.sdamgia.ru/problem?id=N`, `kpolyakov.spb.ru/...topicId=N`,
     `education.yandex.ru/...`. Это точный ключ, а не разбор текста;
  3. запасной вариант — «Задание NN_<id>» из текста условия + слово-источник рядом
     («КЕГЭ», «Поляков», «Решу ЕГЭ», «Яндекс»). Ненадёжен, помечается `via=stem`.

Ничего не пишет: на выходе JSON для следующего шага (загрузка ответов) + сводка.

Запуск:  python scripts/tsk362_collect_sources.py --out <файл.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

HOLLOW_SQL = """
SELECT t.id, t.course_id, t.external_uid, t.max_score,
       t.task_content->>'type'  AS task_type,
       t.task_content->>'stem'  AS stem,
       t.task_content->>'source_tg_global_uid' AS tg_uid,
       jsonb_typeof(t.solution_rules->'quiz') = 'object' AS is_quiz
FROM tasks t
WHERE t.is_active
  AND jsonb_typeof(t.solution_rules) = 'object'
  AND (t.solution_rules->>'manual_review_required')::bool IS NOT TRUE
  AND coalesce(jsonb_array_length(t.solution_rules#>'{short_answer,accepted_answers}'), 0) = 0
  AND coalesce(jsonb_array_length(t.solution_rules->'correct_options'), 0) = 0
  AND coalesce(t.solution_rules->>'text_answer', '') = ''
  AND t.solution_rules->'custom_scoring_config' IS NOT DISTINCT FROM 'null'::jsonb
ORDER BY t.id
"""

# Уже известные ответы внутри LMS — по паре (источник, ID задачи в источнике).
KNOWN_SQL = """
SELECT task_content->>'source_kind' AS src,
       task_content->>'source_task_id' AS src_id,
       solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer,
       id AS twin_id,
       task_content->>'stem' AS stem
FROM tasks
WHERE is_active AND external_uid LIKE 'wp_nav:%'
  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'), 0) > 0
UNION ALL
SELECT split_part(external_uid, ':', 3), split_part(external_uid, ':', 5),
       solution_rules#>>'{short_answer,accepted_answers,0,value}', id,
       task_content->>'stem'
FROM tasks
WHERE is_active AND external_uid ~ '^ext:[a-z0-9]+:(kompege|polyakov|sdamgia):'
  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'), 0) > 0
"""

# Канал импортирован ДВАЖДЫ: `telegram_desktop_json:*` (с сущностями сообщения, там и
# лежат ссылки на источник) и `tg_parser:*` (голый текст, ссылок нет). LMS ссылается на
# второй, поэтому склейка — по номеру сообщения, а не по global_uid.
CB_SQL = """
SELECT external_id,
       string_agg(coalesce(raw::text, '') || ' ' || coalesce(body, ''), ' ') AS rawtxt
FROM content_hub.source_item
WHERE source_id = '1701256430'
GROUP BY external_id
"""

_HREF_PATTERNS = [
    ("kompege", re.compile(r"kompege\.ru/task\?id=(\d+)")),
    ("sdamgia", re.compile(r"sdamgia\.ru/problem\?id=(\d+)")),
    ("polyakov", re.compile(r"kpolyakov\.spb\.ru[^\"'\s]*topicId=(\d+)")),
    ("yandex", re.compile(r"education\.yandex\.ru/[^\"'\s]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")),
]

# Шапка поста бывает не только «Задание N_<id>», но и «Решение задания …», «Разбор
# задания …», «Задания 26 …» — ID один и тот же, меняется только слово перед ним.
_STEM_ID = re.compile(r"(?:Задани[ея]|задани[ея])[^_<]{0,16}_([0-9a-fA-F][0-9a-fA-F\-]*)")


def source_from_words(head: str) -> str | None:
    """Источник по словам в шапке условия. Запасной путь, менее надёжный, чем ссылка."""
    low = head.lower()
    if "крылов" in low:
        return "crylov"
    if re.search(r"комп ?егэ|компегэ|кегэ|kompege|kege", low):
        return "kompege"
    if "поляков" in low:
        return "polyakov"
    if re.search(r"решу ?егэ|sdamgia", low):
        return "sdamgia"
    if re.search(r"яндекс|yandex", low):
        return "yandex"
    return None


def _dsn(server: str) -> str:
    """DSN прод-сервера из .mcp.json. Значение не печатаем."""
    for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
        if not candidate.exists():
            continue
        cfg = json.loads(candidate.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers[server]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://"):
                return arg
    raise RuntimeError(f"не нашёл DSN для {server} в .mcp.json")


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


async def main(out_path: Path) -> None:
    lms = await asyncpg.connect(_dsn("learn_prod_db"))
    try:
        rows = await lms.fetch(HOLLOW_SQL)
        known_rows = await lms.fetch(KNOWN_SQL)
    finally:
        await lms.close()

    cb = await asyncpg.connect(_dsn("content_backbone_prod_db"))
    try:
        cb_rows = await cb.fetch(CB_SQL)
    finally:
        await cb.close()

    tg_links: dict[str, list[tuple[str, str]]] = {}
    for r in cb_rows:
        found = []
        for src, pat in _HREF_PATTERNS:
            for m in pat.finditer(r["rawtxt"]):
                found.append((src, m.group(1)))
        if found:
            tg_links[str(r["external_id"])] = found

    known: dict[tuple[str, str], list[dict]] = {}
    for r in known_rows:
        if not r["src"] or not r["src_id"]:
            continue
        known.setdefault((r["src"], r["src_id"]), []).append(
            {"twin_id": r["twin_id"], "answer": r["answer"], "stem": strip_html(r["stem"])[:400]}
        )

    items = []
    for r in rows:
        uid = r["external_uid"] or ""
        fam = uid.split(":", 1)[0] if uid else "none"
        item = {
            "id": r["id"], "course_id": r["course_id"], "external_uid": uid,
            "family": fam, "task_type": r["task_type"], "max_score": r["max_score"],
            "stem": strip_html(r["stem"]), "is_quiz": r["is_quiz"],
            "source": None, "source_id": None, "via": None,
        }

        # ext:<batch>:<source>[:<tier>]:<YYYYMMDD>:<id>[:<позиция>] — форма плавает
        # (у yandex есть tier и позиция в подборке), поэтому режем по дате-токену.
        parts = uid.split(":")
        src_tok = next((p for p in parts if p in ("kompege", "polyakov", "sdamgia", "yandex")), None)
        date_ix = next((i for i, p in enumerate(parts) if re.fullmatch(r"20\d{6}", p)), None)
        if uid.startswith("ext:") and src_tok and date_ix is not None:
            item["source"] = src_tok
            item["source_id"] = ":".join(parts[date_ix + 1:])
            item["via"] = "external_uid"
        elif fam == "tg" and r["tg_uid"] and r["tg_uid"].rsplit(":", 1)[-1] in tg_links:
            links = tg_links[r["tg_uid"].rsplit(":", 1)[-1]]
            uniq = sorted(set(links))
            if len(uniq) == 1:
                item["source"], item["source_id"], item["via"] = uniq[0][0], uniq[0][1], "tg_link"
            else:
                item["via"] = "tg_link_ambiguous"
                item["candidates"] = [{"source": s, "source_id": i} for s, i in uniq]

        if item["source"] is None and item["via"] in (None, "tg_link_ambiguous"):
            head = item["stem"][:140]
            sid = _STEM_ID.search(r["stem"] or "")
            src = source_from_words(head)
            if src and sid:
                item["source"], item["source_id"] = src, sid.group(1)
                item["via"] = "stem" if item["via"] is None else "stem_after_ambiguous"

        key = (item["source"], item["source_id"])
        twins = known.get(key, []) if item["source"] else []
        item["twins"] = twins
        items.append(item)

    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    by_fam: dict[str, int] = {}
    by_via: dict[str, int] = {}
    by_src: dict[str, int] = {}
    twinned = 0
    for it in items:
        by_fam[it["family"]] = by_fam.get(it["family"], 0) + 1
        by_via[str(it["via"])] = by_via.get(str(it["via"]), 0) + 1
        by_src[str(it["source"])] = by_src.get(str(it["source"]), 0) + 1
        if it["twins"]:
            twinned += 1

    print(f"Непроверяемых активных заданий: {len(items)}")
    print(f"  по семейству external_uid: {by_fam}")
    print(f"  как определён источник:     {by_via}")
    print(f"  источник:                   {by_src}")
    print(f"  есть близнец с ответом в LMS: {twinned}")
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    asyncio.run(main(Path(ap.parse_args().out)))
