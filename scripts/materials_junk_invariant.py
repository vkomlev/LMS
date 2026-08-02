# -*- coding: utf-8 -*-
"""Инвариант материалов: в БД не должно быть отладочных заглушек публикатора.

ЗАЧЕМ
Отладочные прогоны публикатора ContentBackbone трижды (1, 3 и 6 июня) записали
в прод-БД `learn` тестовые материалы «Win»/«OK row» с содержимым `hello`/`ok`.
Они пролежали в живом курсе «Основы Python» почти два месяца и были замечены
только при визуальной ревизии контента в кабинете методиста (tsk-465). Гейт на
приёмнике (`MaterialsBulkUpsertItem`, tsk-467) блокирует НОВЫЕ такие записи, но
не ловит то, что уже просочилось раньше гейта или мимо него (ручной INSERT,
будущий обходной путь). Этот скрипт — регулярная проверка, которая не даёт
такому мусору снова пролежать незамеченным месяцами.

ЧТО ПРОВЕРЯЕТ
  A. title (после trim, без учёта регистра) совпадает со стоп-листом заведомо
     тестовых значений публикатора.
  B. type='text' и всё содержимое content->>'text' (после trim, без учёта
     регистра) — одно из тестовых слов-заглушек.
Стоп-листы — те же, что в `app.schemas.materials._JUNK_TITLES` /
`_JUNK_TEXT_BODIES`; держать их синхронно (импорт напрямую из схемы, чтобы не
разъезжались двумя списками).
Материал, указанный в allowlist (по id), из обеих проверок исключается.

ТОЛЬКО ЧТЕНИЕ: единственный SQL — SELECT. Ничего не пишет.

КОД ВОЗВРАТА: 0 — инвариант держится; 1 — найдены нарушения; 2 — ошибка запуска.
Годится для планировщика и pre-deploy проверки.

ЗАПУСК
  # на проде (/opt/lms) — DSN из .env
  venv/bin/python scripts/materials_junk_invariant.py
  # локально по прод-базе
  DATABASE_URL='postgresql://...' python scripts/materials_junk_invariant.py

ВНИМАНИЕ: локальный .env указывает на DEV-базу. Скрипт печатает хост и имя БД в
шапке — сверяйтесь с ними, прежде чем делать вывод о проде.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import asyncpg

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.materials import _JUNK_TITLES, _JUNK_TEXT_BODIES  # noqa: E402

_TITLES_SQL_ARRAY = ", ".join(f"'{t}'" for t in sorted(_JUNK_TITLES))
_BODIES_SQL_ARRAY = ", ".join(f"'{b}'" for b in sorted(_JUNK_TEXT_BODIES))

QUERY = f"""
SELECT m.id,
       m.course_id,
       c.title AS course_title,
       m.external_uid,
       m.title,
       m.type,
       m.is_active,
       m.created_at,
       (lower(trim(m.title)) IN ({_TITLES_SQL_ARRAY})) AS junk_title,
       (m.type = 'text'
        AND lower(trim(m.content->>'text')) IN ({_BODIES_SQL_ARRAY})) AS junk_body
  FROM materials m
  JOIN courses c ON c.id = m.course_id
 WHERE lower(trim(m.title)) IN ({_TITLES_SQL_ARRAY})
    OR (m.type = 'text' AND lower(trim(m.content->>'text')) IN ({_BODIES_SQL_ARRAY}))
 ORDER BY m.created_at DESC
"""

TOTAL_QUERY = "SELECT count(*) FROM materials"


def _dsn() -> str:
    """DSN базы: из env DATABASE_URL или из .env, в форме asyncpg (без +asyncpg)."""
    url: Optional[str] = os.environ.get("DATABASE_URL")
    if not url:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("DATABASE_URL не найден ни в env, ни в .env")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


def _where(dsn: str) -> str:
    """Человекочитаемое «куда подключились» — чтобы не спутать dev и прод."""
    m = re.search(r"@([^/:]+)(?::\d+)?/([^?]+)", dsn)
    return f"{m.group(1)}/{m.group(2)}" if m else "неизвестно"


def _load_allowlist(path: Path) -> dict[str, Any]:
    """Разрешённые исключения: id материала -> причина."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("allow") or {}


async def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка инварианта отсутствия тестовых заглушек в materials")
    ap.add_argument(
        "--allowlist",
        default=str(Path(__file__).with_name("materials_junk_invariant_allowlist.json")),
        help="JSON с разрешёнными исключениями",
    )
    ap.add_argument("--json", action="store_true", help="вывести находки как JSON")
    args = ap.parse_args()

    allow = _load_allowlist(Path(args.allowlist))
    dsn = _dsn()

    conn = await asyncpg.connect(dsn)
    try:
        total = await conn.fetchval(TOTAL_QUERY)
        rows = await conn.fetch(QUERY)
    finally:
        await conn.close()

    findings, allowed = [], []
    for r in rows:
        item = {
            "id": r["id"],
            "course_id": r["course_id"],
            "course_title": r["course_title"],
            "external_uid": r["external_uid"],
            "title": r["title"],
            "type": r["type"],
            "is_active": r["is_active"],
            "created_at": str(r["created_at"]),
            "мусорный_заголовок": r["junk_title"],
            "мусорное_тело": r["junk_body"],
        }
        (allowed if str(r["id"]) in allow else findings).append(item)

    if args.json:
        print(json.dumps({"total_materials": total, "findings": findings,
                          "allowed": allowed}, ensure_ascii=False, indent=2, default=str))
        return 1 if findings else 0

    print(f"База: {_where(dsn)}")
    print(f"Материалов всего: {total}")
    print(f"Разрешённых исключений в allowlist: {len(allow)}")
    print()

    if allowed:
        print(f"Пропущено по allowlist: {len(allowed)}")
        for it in allowed:
            print(f"  id={it['id']} ({it['external_uid']}) — {allow[str(it['id'])].get('reason', '')}")
        print()

    if not findings:
        print("ИНВАРИАНТ ДЕРЖИТСЯ: тестовых заглушек публикатора в materials не найдено.")
        return 0

    active_count = sum(1 for it in findings if it["is_active"])
    print(f"НАРУШЕНИЙ: {len(findings)} (из них активных: {active_count})")
    print()
    for it in findings:
        why = []
        if it["мусорный_заголовок"]:
            why.append("title в стоп-листе тестовых значений")
        if it["мусорное_тело"]:
            why.append("content.text — тестовая заглушка-плейсхолдер")
        status = "АКТИВЕН" if it["is_active"] else "выключен"
        print(f"  id={it['id']} · курс {it['course_id']} «{it['course_title']}» · {status}")
        print(f"     title={it['title']!r} · {it['external_uid']} · создан {it['created_at']}")
        print(f"     {'; '.join(why)}")
    print()
    print("Что делать: убедиться, что это отладочный прогон (см. tsk-465/tsk-467), удалить")
    print("строку через /db-check в режиме записи, либо занести в allowlist с причиной,")
    print("если материал легитимен, а совпадение случайное.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — верхний уровень CLI
        print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
        sys.exit(2)
