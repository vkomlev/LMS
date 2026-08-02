"""Read-only аудит битых ссылок на файлы в материалах и заданиях LMS (tsk-519), v2.

Отличия от v1: URL берётся целиком (вместе с query-подписью, как у VK CDN),
HTML-энтити декодируются, и ссылки собираются не только по расширению файла,
но и из атрибутов src у <img>/<source>/<video>/<audio> и из полей url в JSON.

Скрипт ничего не пишет: только SELECT в БД и HEAD/GET по ссылкам.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Set, Tuple

import asyncpg
import httpx

# URL целиком: до пробела, кавычки, экранированной кавычки (\") или закрывающего тега
URL_CHARS = r'[^\s"\'<>\\]+'
SRC_RE = re.compile(r'(?:src|href)\s*=\s*\\?["\']?(https?://' + URL_CHARS + r')', re.IGNORECASE)
JSON_URL_RE = re.compile(r'"(?:url|file_url|src|image_url)"\s*:\s*"([^"]+)"', re.IGNORECASE)
EXT_RE = re.compile(
    r'https?://' + URL_CHARS.replace(']+', r'.]+') +
    r'\.(?:jpg|jpeg|png|gif|webp|svg|bmp|pdf|mp4|webm|mp3|zip|rar|7z|docx|xlsx|pptx|csv|txt|py)'
    r'(?:\?' + URL_CHARS + r')?',
    re.IGNORECASE,
)
MEDIA_RE = re.compile(r'/api/v1/media/([0-9a-f]{64}\.[A-Za-z0-9]+)')
MATFILE_RE = re.compile(r'/api/v1/materials/files/([A-Za-z0-9._-]+)')

SQL = """
SELECT 'material' AS kind, id, course_id, is_active, content::text AS body FROM materials
UNION ALL
SELECT 'task', id, course_id, is_active, task_content::text FROM tasks
"""


def dsn_from_env(path: str = "/opt/lms/.env") -> str:
    """Достаёт DATABASE_URL из .env и приводит его к формату asyncpg."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                return dsn.replace("postgresql+asyncpg://", "postgresql://")
    raise RuntimeError("DATABASE_URL не найден в .env")


def clean(url: str) -> str:
    """Декодирует HTML-энтити и убирает хвостовую пунктуацию."""
    url = html.unescape(url.replace('\\/', '/'))
    return url.rstrip('.,;)]}\'"')


def extract(body: str) -> Set[str]:
    """Собирает из тела все ссылки, ведущие на файлы (картинки, видео, вложения)."""
    found: Set[str] = set()
    for regex in (SRC_RE, JSON_URL_RE, EXT_RE):
        for raw in regex.findall(body):
            url = clean(raw)
            if not url.startswith("http"):
                continue
            path = url.split("?", 1)[0]
            looks_like_file = "." in path.rsplit("/", 1)[-1]
            if looks_like_file or regex is SRC_RE:
                found.add(url)
    return found


async def probe(client: httpx.AsyncClient, url: str) -> int:
    """Возвращает HTTP-код по ссылке (HEAD, при 403/405/501 — повтор через GET)."""
    try:
        resp = await client.head(url)
        if resp.status_code in (403, 405, 501):
            resp = await client.get(url)
        return resp.status_code
    except Exception as exc:  # noqa: BLE001 — код нужен только для отчёта
        print(f"ERR {url} :: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


async def main() -> None:
    conn = await asyncpg.connect(dsn_from_env())
    rows = await conn.fetch(SQL)
    await conn.close()

    refs: Dict[str, List[Tuple[str, int, int, bool]]] = defaultdict(list)
    s3_base = (os.getenv("S3_MEDIA_BUCKET_URL") or "").rstrip("/")

    for row in rows:
        body = row["body"] or ""
        owner = (row["kind"], row["id"], row["course_id"], row["is_active"])
        for url in extract(body):
            refs[url].append(owner)
        for sha in MEDIA_RE.findall(body):
            refs[f"{s3_base}/{sha[:2]}/{sha}" if s3_base else f"CAS:{sha}"].append(owner)
        for fid in MATFILE_RE.findall(body):
            refs[f"LOCALFILE:{fid}"].append(owner)

    local_dir = "/opt/lms/uploads/materials"
    broken: List[Tuple[str, int]] = []
    checked: Set[str] = set()

    for url in list(refs):
        if url.startswith("LOCALFILE:"):
            checked.add(url)
            if not os.path.exists(os.path.join(local_dir, url.split(":", 1)[1])):
                broken.append((url, 404))

    net_urls = [u for u in refs if u not in checked]
    sem = asyncio.Semaphore(16)

    print(f"уникальных ссылок: {len(refs)}, сетевых к проверке: {len(net_urls)}", flush=True)
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        verify=False,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        },
    ) as client:

        async def worker(url: str) -> None:
            async with sem:
                code = await probe(client, url)
                if code != 200:
                    broken.append((url, code))

        await asyncio.gather(*(worker(u) for u in net_urls))

    report = []
    print("\n=== БИТЫЕ ССЫЛКИ ===")
    for url, code in sorted(broken, key=lambda x: x[0]):
        owners = refs[url]
        act = [o for o in owners if o[3]]
        report.append({"url": url, "code": code, "refs": len(owners), "active": len(act),
                       "owners_active": act[:10]})
        print(f"[{code}] {url}")
        print(f"    ссылок: {len(owners)}, активных: {len(act)}; примеры: {act[:5] or owners[:5]}")
    print(f"\nитого битых уникальных ссылок: {len(broken)}")
    with open("/tmp/broken_links_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
