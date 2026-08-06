# -*- coding: utf-8 -*-
"""Инвариант: активное авто-проверяемое задание не должно молчать про

предупреждение автора об ошибке в ответе источника (tsk-368/569).

ЗАЧЕМ
Разбор tsk-368 (2026-08-06) показал класс дефекта: автор курса прямо в тексте
условия пишет, что ответ источника неверен («‼️ Внимание, предварительно есть
ошибка в ответе!», «Внимание, в ответе на сайте ошибка», «На мой взгляд,
ошибка в правильном ответе», «В ответе ошибка»). До tsk-569 ни импорт
(`TaskAdapter._build_solution_rules`), ни одноразовые скрипты, которые потом
мехаnически «повышали» задания до auto-check (класс tsk362_upgrade_manual_to_auto.py),
текст условия не читали — 6 из 9 живых случаев tsk-368 к моменту разбора
оказались с `manual_review_required=false`, хотя текст условия прямо
предупреждал об ошибке. Начиная с tsk-569 сам импорт (CB
`monolith/external_tasks/normalizer/source_warning.py`) форсит ручную
проверку при новом импорте — но это НЕ переиздаёт уже залитые задания
(тот же принцип, что у `clean_pdf_stem`/`clean_web_stem`, см. плейбук §11.1).
Этот скрипт — закрывающая проверка по всей активной базе, а не только по
8 заданиям исходного разбора.

ДЕТЕКТОР ДУБЛИРУЕТ (сознательно, не импортирует) регэксп из
`D:\\Work\\ContentBackbone\\monolith\\external_tasks\\normalizer\\source_warning.py`
— два независимых рантайма/venv. При правке эвристики в одном месте
проверить и поправить второе (аналог принципа §9 плейбука
`test_normalize_mirrors_lms`).

ЧТО ПРОВЕРЯЕТ
Активное задание, чей `task_content->>'stem'` содержит связку
«ошибка ... ответ» (в любом порядке, в пределах одного предложения), но
`manual_review_required` не `true` — сигнал, что предупреждение источника
осталось непрочитанным автоматикой.

ТОЛЬКО ЧТЕНИЕ: единственный SQL — SELECT. Ничего не пишет.

КОД ВОЗВРАТА: 0 — инвариант держится (или все находки в allowlist);
1 — есть непрощённые находки; 2 — ошибка запуска.

ЗАПУСК
  # на проде (/opt/lms) — DSN из .env
  venv/bin/python scripts/check_source_warning_tasks.py
  # локально по прод-базе
  DATABASE_URL='postgresql://...' python scripts/check_source_warning_tasks.py

ВНИМАНИЕ: локальный .env указывает на DEV-базу. Скрипт печатает хост и имя БД
в шапке — сверяйтесь с ними, прежде чем делать вывод о проде.
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

# --- детектор: копия monolith/external_tasks/normalizer/source_warning.py (CB) ---
_TAG_RE = re.compile(r"<[^>]+>")
_WARNING_RE = re.compile(
    r"ошибк\w*[^.!?]{0,60}ответ\w*|ответ\w*[^.!?]{0,60}ошибк\w*",
    re.IGNORECASE,
)


def _visible_text(html: str) -> str:
    return _TAG_RE.sub(" ", html or "")


def has_source_error_warning(stem_html: Optional[str]) -> bool:
    if not stem_html:
        return False
    return bool(_WARNING_RE.search(_visible_text(stem_html)))


# --- конец копии ---

QUERY = """
SELECT t.id, t.course_id, t.external_uid, c.title AS course_title,
       t.task_content->>'stem' AS stem,
       COALESCE((t.solution_rules->>'manual_review_required')::bool, false) AS manual_review
  FROM tasks t
  JOIN courses c ON c.id = t.course_id
 WHERE t.is_active
   AND t.task_content->>'stem' IS NOT NULL
"""

TOTAL_QUERY = "SELECT count(*) FROM tasks WHERE is_active"


def _dsn() -> str:
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
    m = re.search(r"@([^/:]+)(?::\d+)?/([^?]+)", dsn)
    return f"{m.group(1)}/{m.group(2)}" if m else "неизвестно"


def _load_allowlist(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("allow") or {}


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Проверка: предупреждение источника об ошибке в ответе "
                     "не проигнорировано (manual_review_required)")
    ap.add_argument(
        "--allowlist",
        default=str(Path(__file__).with_name("check_source_warning_tasks_allowlist.json")),
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
        if not has_source_error_warning(r["stem"]):
            continue
        if r["manual_review"]:
            continue  # уже на ручной проверке — инвариант держится
        stem_text = _visible_text(r["stem"] or "")
        m = _WARNING_RE.search(stem_text)
        item = {
            "id": r["id"],
            "course_id": r["course_id"],
            "course_title": r["course_title"],
            "external_uid": r["external_uid"],
            "snippet": (stem_text[max(0, m.start() - 20): m.end() + 20].strip()
                        if m else ""),
        }
        (allowed if str(r["id"]) in allow else findings).append(item)

    if args.json:
        print(json.dumps({"total_active": total, "findings": findings,
                          "allowed": allowed}, ensure_ascii=False, indent=2))
        return 1 if findings else 0

    print(f"База: {_where(dsn)}")
    print(f"Всего активных заданий: {total}")
    print(f"Разрешённых исключений в allowlist: {len(allow)}")
    print()

    if allowed:
        print(f"Пропущено по allowlist: {len(allowed)}")
        for it in allowed:
            print(f"  id={it['id']} ({it['external_uid']}) — "
                  f"{allow[str(it['id'])].get('reason', '')}")
        print()

    if not findings:
        print("ИНВАРИАНТ ДЕРЖИТСЯ: ни одно активное задание с предупреждением "
              "источника об ошибке в ответе не стоит вне ручной проверки.")
        return 0

    print(f"НАРУШЕНИЙ: {len(findings)}")
    print()
    for it in findings:
        print(f"  id={it['id']} · курс {it['course_id']} «{it['course_title']}»")
        print(f"     {it['external_uid']} — manual_review_required=false, "
              f"но текст несёт предупреждение:")
        print(f"     ...{it['snippet']}...")
    print()
    print("Что делать: /db-check — проставить manual_review_required=true "
          "точечно (не менять сам ответ вслепую), перерешать вручную по "
          "образцу tsk-368, при необходимости пополнить allowlist ложными "
          "срабатываниями эвристики (с обоснованием).")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — верхний уровень CLI
        print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
        sys.exit(2)
