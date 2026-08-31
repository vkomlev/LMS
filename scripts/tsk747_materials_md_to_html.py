"""tsk-747: переиздание материалов, записанных в markdown, в HTML.

Что чинится. SPW отдаёт `material.content.text` в `SanitizedHTML`
(`components/material/MaterialViewer.tsx`, ветка `type == "text"`) и поле
`content.format` не смотрит вовсе. Материал с `format = "markdown"` ученик
видит сырьём: решётки заголовков, звёздочки жирного, тройные кавычки код-блоков
и текст одной простынёй. Подтверждено живьём 31.08.2026 на материале 3863
(курс 1455 «Мини-курс: точный диапазон и аккуратный вывод»).

Почему переиздаём контент, а не учим клиента markdown. HTML — фактический
контракт материалов (3121 материал из 3225 на 31.08.2026); markdown завели два
скрипта авторинга, и он не работал ни в одном клиенте. Вторая ветка рендера
означала бы второй путь очистки в SPW и третий — в ТГ-боте.

Что делает скрипт: находит материалы с markdown-разметкой, конвертирует тело
через `app.utils.md_to_html` и переиздаёт их идемпотентным
`POST /materials/bulk-upsert` по паре (course_id, external_uid) — то есть
обновляет те же строки, а не плодит дубли. `is_active`, `order_position` и
`requirement_level` НЕ передаются: при обновлении сервис их не трогает, и
переиздание не включит выключенное и не утащит материал в конец курса
(tsk-377/tsk-378). `description` и `caption` передаются текущими значениями —
их сервис перезаписывает всегда.

Протокол /db-check соблюдён: по умолчанию предпросмотр (ничего не пишется),
запись — отдельным флагом, после записи обязательная проверка каждой строки.

Запуск на боевом сервере (там сервисный ключ и локальный API):
    sudo -u app /opt/lms/venv/bin/python /opt/lms/scripts/tsk747_materials_md_to_html.py
    sudo -u app /opt/lms/venv/bin/python /opt/lms/scripts/tsk747_materials_md_to_html.py --apply

Локально (предпросмотр по dev-базе) — то же без `--apply`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.utils.md_to_html import (  # noqa: E402
    contains_html_markup,
    looks_like_markdown,
    markdown_to_html,
)


# ------------------------------------------------------------------ доступ

def _token(explicit: Optional[str]) -> str:
    """Сервисный ключ для API. Значение не печатается никогда."""
    if explicit:
        return explicit
    raw = os.environ.get("VALID_API_KEYS")
    if raw and raw.strip():
        return raw.split(",")[0].strip()
    for env_path in (pathlib.Path("/opt/lms/.env"), ROOT / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("VALID_API_KEYS="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value.split(",")[0].strip()
    sys.exit("не найден сервисный ключ (VALID_API_KEYS)")


def _dsn() -> str:
    """DSN базы для чтения. На сервере — из /opt/lms/.env, локально — из .env."""
    dsn = os.environ.get("DATABASE_URL") or ""
    for env_path in (pathlib.Path("/opt/lms/.env"), ROOT / ".env"):
        if dsn or not env_path.exists():
            break
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not dsn:
        sys.exit("не найден DATABASE_URL")
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace("+psycopg2", "")


def _call(base: str, token: str, method: str, path: str, payload: Any = None):
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


# ------------------------------------------------------------------ выборка

_SELECT = """
    SELECT m.id, m.course_id, c.title AS course_title, m.external_uid, m.title,
           m.type, m.description, m.caption, m.is_active, m.order_position,
           m.requirement_level::text AS requirement_level,
           m.content->>'text'   AS text,
           m.content->>'format' AS format,
           m.content_provenance
      FROM materials m
      JOIN courses c ON c.id = m.course_id
     WHERE {where}
     ORDER BY m.course_id, m.order_position, m.id
"""


async def _fetch(dsn: str, ids: Optional[List[int]]) -> List[Dict[str, Any]]:
    """Прочитать кандидатов на переиздание.

    Отбор узкий намеренно: `format = "markdown"` (плюс явный `--ids`). Широкая
    эвристика «в теле похоже на markdown» на проде 31.08.2026 дала 68 строк
    вместо 6 — решётки и звёздочки жили внутри `<pre><code>` HTML-материалов
    курсов Python. Прогнать такой материал через конвертер значит превратить
    весь его HTML в литеральный текст, то есть сломать урок целиком.
    Материалы с HTML-тегами в теле отсеиваются здесь же, даже если названы
    явным `--ids`: это последняя сетка перед записью.
    """
    import asyncpg  # локально не нужен при чтении из файла-плана

    where = "m.id = ANY($1::int[])" if ids else "m.content->>'format' = 'markdown'"
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_SELECT.format(where=where), *([ids] if ids else []))
    finally:
        await conn.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if row.get("type") != "text" or not row.get("text"):
            print(f"  пропуск id={row['id']}: тип {row.get('type')!r} или пустое тело")
            continue
        if contains_html_markup(row["text"]):
            print(f"  ПРОПУСК id={row['id']} ({row['external_uid']}): в теле уже есть HTML-теги")
            continue
        out.append(row)
    return out


async def _scan(dsn: str) -> List[Dict[str, Any]]:
    """Перечислить материалы с markdown-разметкой, у которых формат другой.

    Только отчёт: такие строки надо смотреть глазами, а не конвертировать
    пачкой — почти все окажутся HTML-материалами с решётками в примерах кода.
    """
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            _SELECT.format(where="coalesce(m.content->>'format','') <> 'markdown'")
        )
    finally:
        await conn.close()
    return [
        dict(r) for r in rows
        if r["type"] == "text" and r["text"] and looks_like_markdown(r["text"])
    ]


async def _verify(dsn: str, ids: List[int]) -> List[Dict[str, Any]]:
    """Перечитать тронутые строки после записи (проверка поштучно, не агрегатом)."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_SELECT.format(where="m.id = ANY($1::int[])"), ids)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- отчёт

def _report(rows: List[Dict[str, Any]], converted: Dict[int, str], path: pathlib.Path) -> None:
    """Сохранить артефакт «было/стало».

    Он же — единственный бэкап исходных тел: `bulk-upsert` коммитит сам, и
    вернуть markdown после записи можно только из этого файла. Поэтому путь по
    умолчанию — `reviews/` рядом с кодом, а не временный каталог.
    """
    lines = ["# tsk-747 — переиздание markdown-материалов в HTML", ""]
    for row in rows:
        lines += [
            f"## Материал {row['id']} — {row['title']}",
            "",
            f"- курс: {row['course_id']} «{row['course_title']}»",
            f"- external_uid: `{row['external_uid']}`",
            f"- format: `{row['format']}`, is_active: {row['is_active']}, "
            f"order_position: {row['order_position']}, requirement_level: {row['requirement_level']}",
            f"- content_provenance: {row['content_provenance']!r}",
            "",
            "### Было (markdown)", "", "```markdown", row["text"], "```", "",
            "### Стало (HTML)", "", "```html", converted[row["id"]], "```", "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# -------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="tsk-747: markdown-материалы → HTML")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="базовый адрес API")
    ap.add_argument("--token", default=None, help="сервисный ключ (по умолчанию из окружения)")
    ap.add_argument("--ids", default=None, help="явный список id материалов через запятую")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию предпросмотр)")
    ap.add_argument("--scan", action="store_true",
                    help="только перечислить материалы с markdown-разметкой вне format=markdown")
    ap.add_argument("--report", default=None, help="путь к отчёту (по умолчанию reviews/)")
    args = ap.parse_args()

    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    dsn = _dsn()

    if args.scan:
        found = asyncio.run(_scan(dsn))
        print(f"материалов с markdown-разметкой вне format=markdown: {len(found)}")
        for row in found:
            print(f"  id={row['id']:>5} курс {row['course_id']:>5} format={row['format']!r} "
                  f"{row['external_uid']} — {row['title']}")
        return

    rows = asyncio.run(_fetch(dsn, ids))
    if not rows:
        print("markdown-материалов не найдено — переиздавать нечего.")
        return

    converted: Dict[int, str] = {}
    skipped: List[int] = []
    for row in rows:
        html = markdown_to_html(row["text"], title=row["title"])
        if not html.strip():
            skipped.append(row["id"])
            continue
        converted[row["id"]] = html

    rows = [r for r in rows if r["id"] in converted]
    report_path = pathlib.Path(args.report) if args.report else (
        ROOT / "reviews" / "2026-08-31-tsk747-markdown-materials-to-html.md"
    )
    _report(rows, converted, report_path)

    print(f"кандидатов: {len(rows)}" + (f", пропущено пустых: {skipped}" if skipped else ""))
    for row in rows:
        before, after = row["text"], converted[row["id"]]
        print(
            f"  id={row['id']:>5} курс {row['course_id']:>5} {row['external_uid']}\n"
            f"        {row['title']}\n"
            f"        было {len(before)} симв. ({row['format']}), стало {len(after)} симв. (html); "
            f"разметки не осталось: {not looks_like_markdown(after)}"
        )
    print(f"отчёт: {report_path}")

    if not args.apply:
        print("\nПредпросмотр. В базу ничего не записано. Повторите с --apply.")
        return

    # ------------------------------------------------------------- запись
    token = _token(args.token)
    items = [{
        "course_id": row["course_id"],
        "external_uid": row["external_uid"],
        "title": row["title"],
        "type": row["type"],
        "description": row["description"],
        "caption": row["caption"],
        # is_active / order_position / requirement_level не передаём: при
        # обновлении сервис их не трогает (tsk-377/tsk-378), и переиздание не
        # включит выключенное и не сдвинет порядок в курсе.
        "content": {"text": converted[row["id"]], "format": "html"},
    } for row in rows]

    st, res = _call(args.base, token, "POST", "/api/v1/materials/bulk-upsert", {"items": items})
    if st != 200 or not isinstance(res, dict):
        sys.exit(f"bulk-upsert не прошёл: {st} {res}")
    print(f"\nbulk-upsert: processed={res.get('processed')} created={res.get('created')} "
          f"updated={res.get('updated')} unchanged={res.get('unchanged')}")
    for item in res.get("items", []):
        if item.get("status") == "error":
            print(f"  ОШИБКА {item.get('external_uid')}: {item.get('error')}")

    # Ни одна строка не должна быть created: created означает, что upsert не
    # нашёл пару (course_id, external_uid) и завёл дубль вместо обновления.
    if res.get("created"):
        sys.exit("ОСТАНОВ: bulk-upsert создал новые строки — проверьте external_uid, возможны дубли")

    # --------------------------------------------------------- проверка
    checked = asyncio.run(_verify(dsn, [r["id"] for r in rows]))
    by_id = {r["id"]: r for r in checked}
    bad: List[str] = []
    for row in rows:
        after = by_id.get(row["id"])
        if after is None:
            bad.append(f"id={row['id']}: строка исчезла")
            continue
        if after["format"] != "html":
            bad.append(f"id={row['id']}: format={after['format']!r}, ожидали 'html'")
        if after["text"] != converted[row["id"]]:
            bad.append(f"id={row['id']}: тело в базе не совпало с конвертированным")
        if looks_like_markdown(after["text"]):
            bad.append(f"id={row['id']}: в теле осталась markdown-разметка")
        if bool(after["is_active"]) != bool(row["is_active"]):
            bad.append(f"id={row['id']}: is_active изменился {row['is_active']} → {after['is_active']}")
        if after["order_position"] != row["order_position"]:
            bad.append(
                f"id={row['id']}: order_position изменился "
                f"{row['order_position']} → {after['order_position']}"
            )
        if after["requirement_level"] != row["requirement_level"]:
            bad.append(
                f"id={row['id']}: requirement_level изменился "
                f"{row['requirement_level']} → {after['requirement_level']}"
            )

    print("\nпроверка после записи:")
    if bad:
        for line in bad:
            print(f"  ПРОВАЛ {line}")
        sys.exit("проверка не пройдена — см. строки выше")
    for row in rows:
        print(f"  ок id={row['id']} format=html, разметки нет, is_active/позиция/уровень не изменились")
    print("\nГотово.")


if __name__ == "__main__":
    main()
