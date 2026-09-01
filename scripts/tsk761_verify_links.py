# -*- coding: utf-8 -*-
"""tsk-761, шаг 1 (read-only): сверить остаточные ссылки на файлы источников с CAS.

Что нашли. У 86 активных заданий в `task_content.stem` рядом с рабочей шапкой
«Файл к заданию: <a href="/api/v1/media/<sha>.<ext>">» остался ИСХОДНЫЙ якорь
источника — относительный или внешний:

  * sdamgia — `<a href="/get_file?id=191284">Задание 26</a>` (29 заданий);
  * kpolyakov — `<a href="ege-txt/10-260.docx">10-260.docx</a>` (56 заданий);
  * одиночный `/doc/inf/zadanie26/26_demo.txt` (1 задание).

На нашем домене такой путь ведёт в никуда: ученик жмёт ссылку в теле условия и
получает 404 (SPW рисует stem как HTML, относительный href резолвится к
api.learn/спв, а не к сайту источника). Задание №26 без файла нерешаемо
(плейбук ContentBackbone §6.5).

Почему сверка, а не «просто скачать заново». Файл у всех 86 УЖЕ лежит в CAS
(шаг tsk-369/390/392), поэтому качать нечего — надо доказать, что привязанный
CAS-файл и файл за битой ссылкой это ОДНО И ТО ЖЕ, и тогда битую ссылку можно
переписать на нашу. Доказательство здесь дешёвое и полное: имя файла в CAS —
это sha256 его содержимого, значит принадлежность проверяется пересчётом, а не
совпадением имени (урок tsk-390: у 4780-4789 имя файла совпадало, а содержимое
было чужим — разные листы внутри `.ods`).

База источников. sdamgia отдаёт `get_file?id=N` с трёх доменов (ege / inf-ege /
inf-oge), и по относительной ссылке домен неизвестен — перебираем, верным
считаем тот, чей sha256 совпал с CAS. У kpolyakov `<a href>` резолвится к
`cms/files/` (НЕ к `/school/ege/` и НЕ к `cms/images/` — там картинки, плейбук §2).

Read-only: ни одного UPDATE, только SELECT + скачивание. Правит ссылки шаг 2
(`scripts/tsk761_rewrite_links.py`).

Запуск из корня проекта:
    python scripts/tsk761_verify_links.py --out out/tsk761_plan.json
    python scripts/tsk761_verify_links.py --out out/tsk761_plan.json --tasks 3791
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
PROD_HOST = "5.42.107.253"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

SDAMGIA_HOSTS = ("ege.sdamgia.ru", "inf-ege.sdamgia.ru", "inf-oge.sdamgia.ru")
POLYAKOV_BASE = "https://kpolyakov.spb.ru/cms/files/"
DOC_PREFIX = "/doc/"

DATA_EXT = "txt|xls|xlsx|ods|csv|doc|docx|zip|rar|odt"

SQL_CANDIDATES = rf"""
WITH s AS (
    SELECT t.id, t.course_id, t.external_uid, t.task_content->>'stem' AS stem
    FROM tasks t
    WHERE t.is_active IS TRUE
      AND t.task_content->>'stem' ~ 'href="'
), links AS (
    SELECT s.*, (regexp_matches(s.stem, 'href="\s*([^"]+)"', 'g'))[1] AS href
    FROM s
)
SELECT
    l.id,
    l.course_id,
    l.external_uid,
    array_agg(DISTINCT l.href) FILTER (WHERE l.href LIKE '/get_file%%'
                                          OR l.href LIKE '%%sdamgia.ru/get_file%%'
                                          OR l.href ~ '^ege-[a-z]+/'
                                          OR l.href LIKE '{DOC_PREFIX}%%') AS bad_hrefs,
    array_agg(DISTINCT l.href) FILTER (WHERE l.href ~ '^/api/v1/media/[0-9a-f]{{64}}\.({DATA_EXT})$') AS cas_hrefs
FROM links l
GROUP BY l.id, l.course_id, l.external_uid
HAVING count(*) FILTER (WHERE l.href LIKE '/get_file%%'
                           OR l.href LIKE '%%sdamgia.ru/get_file%%'
                           OR l.href ~ '^ege-[a-z]+/'
                           OR l.href LIKE '{DOC_PREFIX}%%') > 0
ORDER BY l.course_id, l.id
"""


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из .mcp.json (как в tsk759_rewrite)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if PROD_HOST not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and PROD_HOST in arg:
                    dsn = arg
                    break
    if PROD_HOST not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn.")
    return dsn


def fetch(url: str, timeout: int = 40, retries: int = 3) -> tuple[bytes, str] | None:
    """Скачивает URL. Возвращает (тело, content-type) либо None. Сеть у источников рвётся —
    отсюда ретраи: одиночный отказ curl уже выдавал ложное «файла нет» (tsk-390)."""
    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return None if exc.code == 404 else None
        except Exception as exc:  # сеть/таймаут — пробуем ещё
            last = repr(exc)
            time.sleep(2 * (attempt + 1))
    print(f"    сеть не отдала {url}: {last}")
    return None


def candidate_urls(bad_href: str) -> list[str]:
    """Битая ссылка -> список URL-кандидатов на источнике (в порядке правдоподобия)."""
    href = bad_href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return [href]
    if re.match(r"^ege-[a-z]+/", href):
        return [POLYAKOV_BASE + href]
    if href.startswith("/"):
        # И `/get_file?id=N`, и статика вида `/doc/inf/zadanie26/26_demo.txt` живут на
        # доменах sdamgia — какой именно, по относительной ссылке не видно, поэтому
        # перебираем все три и верным считаем тот, чей sha256 совпал с CAS.
        return [f"https://{host}{href}" for host in SDAMGIA_HOSTS]
    return []


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def main(out_path: Path, task_filter: list[int] | None) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(SQL_CANDIDATES)
    finally:
        await conn.close()

    items = [dict(r) for r in rows]
    if task_filter:
        items = [it for it in items if it["id"] in task_filter]
    print(f"Кандидатов (активные задания с остаточной ссылкой источника): {len(items)}")

    cache: dict[str, tuple[str, int, str]] = {}  # url -> (sha, size, content_type)
    plan: list[dict] = []
    # Битых ссылок в одном задании бывает больше одной (у №26 источник даёт «Файл A» и
    # «Файл B»), и CAS-файлов тоже — поэтому идём по КАЖДОЙ паре, а не по первой. Ранняя
    # версия брала `[1]` из обоих списков: вторая ссылка молча осталась бы битой.
    for idx, it in enumerate(items, 1):
        cas_hrefs = list(it["cas_hrefs"] or [])
        sha_to_href = {c.rsplit("/", 1)[-1].split(".")[0]: c for c in cas_hrefs}
        for bad_raw in list(it["bad_hrefs"] or []):
            bad = (bad_raw or "").strip()
            rec = {
                "task_id": it["id"],
                "course_id": it["course_id"],
                "external_uid": it["external_uid"],
                "bad_href": bad_raw,
                "cas_href": None,
                "cas_sha": None,
                "cas_candidates": cas_hrefs,
                "source_url": None,
                "source_sha": None,
                "source_size": None,
                "source_content_type": None,
                "match": None,   # True/False/None(не скачали)
                "note": "",
            }
            urls = candidate_urls(bad)
            if not urls:
                rec["note"] = "URL источника не построен"
            for url in urls:
                if url in cache:
                    sha, size, ctype = cache[url]
                else:
                    got = fetch(url)
                    if got is None:
                        continue
                    data, ctype = got
                    sha, size = sha_of(data), len(data)
                    cache[url] = (sha, size, ctype)
                rec.update(
                    source_url=url, source_sha=sha, source_size=size, source_content_type=ctype
                )
                if sha in sha_to_href:
                    rec.update(match=True, cas_sha=sha, cas_href=sha_to_href[sha])
                    break
                rec["match"] = False
            if rec["match"] is False and rec["source_sha"]:
                rec["note"] = (
                    "sha источника не совпал ни с одним привязанным CAS-файлом — "
                    "файл в задании другой, разбирать вручную"
                )
            print(
                f"[{idx}/{len(items)}] задание {rec['task_id']}: {bad} -> "
                f"{'СОВПАЛ' if rec['match'] else ('НЕ совпал' if rec['match'] is False else 'не скачан')}"
            )
            plan.append(rec)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "task": "tsk-761",
                "read_only": True,
                "total": len(plan),
                "matched": sum(1 for p in plan if p["match"] is True),
                "mismatched": sum(1 for p in plan if p["match"] is False),
                "unresolved": sum(1 for p in plan if p["match"] is None),
                "plan": plan,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"\nИтог: совпало {sum(1 for p in plan if p['match'] is True)}, "
        f"не совпало {sum(1 for p in plan if p['match'] is False)}, "
        f"не скачано {sum(1 for p in plan if p['match'] is None)}. План: {out_path}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/tsk761_plan.json")
    ap.add_argument("--tasks", help="ограничить списком id через запятую")
    args = ap.parse_args()
    filt = [int(x) for x in args.tasks.split(",")] if args.tasks else None
    asyncio.run(main(Path(args.out), filt))
