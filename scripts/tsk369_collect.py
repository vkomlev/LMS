# -*- coding: utf-8 -*-
"""tsk-369, шаг 1: собрать активные задания, которые требуют файл-приложение, а файла нет.

ЗАЧЕМ
Задания ЕГЭ №3, 9, 10, 17, 18, 22, 24, 26, 27 формулируются коротко («Откройте файл
электронной таблицы…», «Текстовый файл состоит из…»), а всё различие между задачами
одного типа — в приложенном файле. Нет файла — задачу невозможно решить в принципе,
и наружу это никак не всплывает: и текст есть, и правило проверки на месте.

КАК ОПРЕДЕЛЯЕТСЯ «ФАЙЛ ЕСТЬ»
Ученику файл виден ТОЛЬКО ссылкой внутри `stem` (SPW не читает `attached_file_paths` —
проверено grep'ом по клиенту). Поэтому «файл есть» = в stem есть `/api/v1/media/...`.
Метаданные `has_attached_file` / `attached_file_paths` ставит импорт ContentBackbone;
они полезны для машинного учёта, но сами по себе ученику ничего не дают.

МЯГКИЕ ПЕРЕНОСЫ
sdamgia расставляет внутри слов U+00AD («Тек­сто­вый файл со­сто­ит»). Без их снятия
поиск по тексту условия молча теряет целую партию — счёт 108 из первичного разбора
получился именно так.

КЛЮЧ ИСТОЧНИКА (в порядке надёжности)
  1. `task_content.source_kind` + `source_task_id` — партия wp_nav, источник задан явно;
  2. `external_uid` вида `ext:<партия>:<источник>:<дата>:<id>`;
  3. для `tg:*` — канонический URL из самого поста Telegram (ContentBackbone,
     `content_hub.source_item.raw`). Канал импортирован ДВАЖДЫ: сущности сообщения (а с
     ними ссылки) есть только в партии `telegram_desktop_json:*`, тогда как LMS ссылается
     на `tg_parser:*` — склейка идёт по номеру сообщения (`external_id`), не по global_uid;
  4. запасной вариант — «Задание NN_<id>» из шапки условия + слово-источник рядом.

БЛИЗНЕЦЫ
Отдельно ищутся задания, у которых тот же (источник, ID) УЖЕ имеет файл в LMS: тогда
файл не надо ни скачивать, ни заново класть в CAS — берётся готовый sha_ext.

Ничего не пишет в БД. На выходе JSON для шага 2.

Запуск:  python scripts/tsk369_collect.py --out <файл.json>
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

# Формулировки, которые означают «данные задачи лежат в приложенном файле».
# chr(173) — мягкий перенос, снимается до сравнения (см. докстринг).
FILE_GATE_RE = (
    r"(откройте файл|откройте прилага|в файле содерж|в файле привед|в файле, содерж|"
    r"прилагаемом файле|прилагается файл|входного файла|текстовый файл состоит|"
    r"файл электронной таблиц|в прикреплённом файле|в прикрепленном файле|"
    r"данные для выполнения|откройте один из файлов|с помощью текстового редактора|"
    r"в текстовом файле)"
)

TARGET_SQL = f"""
SELECT t.id, t.course_id, t.external_uid, t.max_score,
       t.task_content->>'type' AS task_type,
       t.task_content->>'stem' AS stem,
       t.task_content->>'source_kind' AS source_kind,
       t.task_content->>'source_task_id' AS source_task_id,
       t.task_content->>'source_url' AS source_url,
       t.task_content->>'source_tg_global_uid' AS tg_uid,
       (SELECT m[1] FROM regexp_matches(
            lower(replace(regexp_replace(t.task_content->>'stem','<[^>]+>',' ','g'), chr(173), '')),
            '{FILE_GATE_RE}') m LIMIT 1) AS phrase
FROM tasks t
WHERE t.is_active
  AND lower(replace(regexp_replace(t.task_content->>'stem','<[^>]+>',' ','g'), chr(173), ''))
      ~ '{FILE_GATE_RE}'
  AND (t.task_content->>'stem') NOT LIKE '%/api/v1/media/%'
  AND coalesce(jsonb_array_length(t.task_content->'attached_file_paths'), 0) = 0
ORDER BY t.id
"""

# Задания, у которых файл УЖЕ есть: по ним берётся готовый sha_ext для близнецов.
WITH_FILE_SQL = """
SELECT t.id, t.external_uid,
       t.task_content->>'source_kind' AS source_kind,
       t.task_content->>'source_task_id' AS source_task_id,
       t.task_content->>'stem' AS stem,
       t.task_content->'attached_file_paths' AS paths
FROM tasks t
WHERE t.is_active
  AND ((t.task_content->>'stem') LIKE '%/api/v1/media/%'
       OR coalesce(jsonb_array_length(t.task_content->'attached_file_paths'), 0) > 0)
"""

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
    ("yandex", re.compile(
        r"education\.yandex\.ru/[^\"'\s]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")),
]

_STEM_ID = re.compile(r"(?:Задани[ея]|задани[ея])[^_<]{0,16}_([0-9a-fA-F][0-9a-fA-F\-]*)")
_SHA_EXT = re.compile(r"/api/v1/media/([0-9a-f]{64}\.[a-z0-9]+)")


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


def dsn(server: str) -> str:
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
    """Текст условия без разметки и мягких переносов."""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("­", "").replace("​", "").replace("﻿", "")
    return re.sub(r"\s+", " ", s).strip()


def normalize_source_id(source: str, raw: str) -> str:
    """ID задачи в источнике.

    У yandex `source_task_id` бывает двух видов: голый UUID задачи и `<UUID>:<N>`, где
    UUID — это подборка (`/ege/collections/<uuid>/task/<N>`), а N — номер задания в ней.
    Хвост срезать НЕЛЬЗЯ: по UUID подборки запрос задачи отвечает 404 (проверено — так
    отвалились 12 заданий в первом прогоне). Оба вида разбирает шаг 2.
    """
    raw = (raw or "").strip()
    if source == "yandex":
        m = re.match(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?::(\d+))?", raw)
        if m:
            return f"{m.group(1)}:{m.group(2)}" if m.group(2) else m.group(1)
    return raw


async def main(out_path: Path) -> None:
    lms = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await lms.fetch(TARGET_SQL)
        with_file = await lms.fetch(WITH_FILE_SQL)
    finally:
        await lms.close()

    cb = await asyncpg.connect(dsn("content_backbone_prod_db"))
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

    # Индекс «(источник, id) → уже привязанные файлы» для поиска близнецов.
    have: dict[tuple[str, str], list[dict]] = {}
    for r in with_file:
        shas = _SHA_EXT.findall(r["stem"] or "")
        for p in (r["paths"] or []):
            m = _SHA_EXT.search(str(p))
            if m:
                shas.append(m.group(1))
        shas = sorted(set(shas))
        if not shas:
            continue
        keys: set[tuple[str, str]] = set()
        if r["source_kind"] and r["source_task_id"]:
            keys.add((r["source_kind"], normalize_source_id(r["source_kind"], r["source_task_id"])))
        uid = r["external_uid"] or ""
        parts = uid.split(":")
        src_tok = next((p for p in parts if p in ("kompege", "polyakov", "sdamgia", "yandex")), None)
        date_ix = next((i for i, p in enumerate(parts) if re.fullmatch(r"20\d{6}", p)), None)
        if src_tok and date_ix is not None:
            keys.add((src_tok, ":".join(parts[date_ix + 1:])))
        for k in keys:
            have.setdefault(k, []).append({"twin_id": r["id"], "sha_ext": shas})

    items = []
    for r in rows:
        uid = r["external_uid"] or ""
        item = {
            "id": r["id"], "course_id": r["course_id"], "external_uid": uid,
            "family": uid.split(":", 1)[0] if uid else "none",
            "task_type": r["task_type"], "max_score": r["max_score"],
            "phrase": r["phrase"], "source_url": r["source_url"],
            "stem_html": r["stem"], "stem": strip_html(r["stem"]),
            "source": None, "source_id": None, "via": None,
        }

        if r["source_kind"] and r["source_task_id"]:
            item["source"] = r["source_kind"]
            item["source_id"] = normalize_source_id(r["source_kind"], r["source_task_id"])
            item["via"] = "source_task_id"
        else:
            parts = uid.split(":")
            src_tok = next((p for p in parts if p in ("kompege", "polyakov", "sdamgia", "yandex")), None)
            date_ix = next((i for i, p in enumerate(parts) if re.fullmatch(r"20\d{6}", p)), None)
            if uid.startswith("ext:") and src_tok and date_ix is not None:
                item["source"] = src_tok
                item["source_id"] = ":".join(parts[date_ix + 1:])
                item["via"] = "external_uid"
            elif item["family"] == "tg" and r["tg_uid"]:
                msg_no = r["tg_uid"].rsplit(":", 1)[-1]
                uniq = sorted(set(tg_links.get(msg_no, [])))
                if len(uniq) == 1:
                    item["source"], item["source_id"], item["via"] = uniq[0][0], uniq[0][1], "tg_link"
                elif uniq:
                    item["via"] = "tg_link_ambiguous"
                    item["candidates"] = [{"source": s, "source_id": i} for s, i in uniq]

        if item["source"] is None:
            sid = _STEM_ID.search(r["stem"] or "")
            src = source_from_words(item["stem"][:140])
            if src and sid:
                item["source"], item["source_id"] = src, sid.group(1)
                item["via"] = "stem" if item["via"] is None else "stem_after_ambiguous"

        key = (item["source"], item["source_id"])
        item["twins"] = have.get(key, []) if item["source"] else []
        items.append(item)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    def tally(field):
        acc: dict[str, int] = {}
        for it in items:
            acc[str(it[field])] = acc.get(str(it[field]), 0) + 1
        return dict(sorted(acc.items(), key=lambda kv: -kv[1]))

    print(f"Заданий с файловым условием и без файла: {len(items)}")
    print(f"  семейство external_uid: {tally('family')}")
    print(f"  как определён источник: {tally('via')}")
    print(f"  источник:               {tally('source')}")
    print(f"  сработавшая формулировка: {tally('phrase')}")
    print(f"  есть близнец с готовым файлом: {sum(1 for i in items if i['twins'])}")
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    asyncio.run(main(Path(ap.parse_args().out)))
