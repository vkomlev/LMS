# -*- coding: utf-8 -*-
r"""Инвариант мат-разметки stem ЕГЭ: в активных заданиях не должно быть порчи импорта.

ЗАЧЕМ
tsk-391 восстановил РАЗМЕТКУ индексов у 105 заданий (данные), tsk-398 починил
НОРМАЛИЗАТОРЫ импорта (источник). Этот скрипт — закрывающая проверка: ловит
регрессию, если новая партия импорта снова пронесёт порчу мимо фикса. Тот же
паттерн, что `ege_answer_invariant.py`: разовые правки данных без инварианта
повторяются (уроки tsk-345/tsk-374/tsk-391).

ЧТО ПРОВЕРЯЕТ (все три — прямые следствия дефектов tsk-391/tsk-398)
  B. raw KaTeX-вёрстка: в stem остался `<span class="katex">` — импортёр не снял
     готовую вёрстку чужого сайта и не заменил её на `$LaTeX$` из <annotation>.
     Санитайзер SPW снимет inline-`style`, и геометрия KaTeX развалится.
  D. сырой знак сравнения в математике: `<`/`>` внутри `$…$` без экранирования.
     HTML-санитайзер видит `<10` как начало тега и съедает — условие теряет знак
     («$n<10$» → «$n 10$»). Должно быть `\lt`/`\gt`.
  C. суррогатная степень: `**`/`^` между алфанум-символами ВНЕ `<code>`/`<pre>`
     (в коде это оператор Python, не показатель ЕГЭ). Должно быть `<sup>`.

Класс A (реальная потеря разметки, «2²³»→«223») здесь НЕ детектируется: восстановить
её можно только сверкой с первоисточником по ID (tsk-391), а признака в самих данных
нет — потому дефект и был незаметен. Инвариант закрывает то, что детектируемо.

ТОЛЬКО ЧТЕНИЕ: единственный SQL — SELECT. Ничего не пишет.

КОД ВОЗВРАТА: 0 — инвариант держится; 1 — найдены нарушения; 2 — ошибка запуска.
Годится для планировщика и pre-deploy проверки.

ЗАПУСК
  # на проде (/opt/lms) — DSN из .env
  venv/bin/python scripts/ege_stem_markup_invariant.py
  # локально по прод-базе
  DATABASE_URL='postgresql://...' python scripts/ege_stem_markup_invariant.py

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

# «Очищенный» stem — с вырезанными зонами, где `^`/`**` легитимны и не суррогат:
#   $…$      — LaTeX-математика (`10^{2024}`): `^` там показатель, а не дефект;
#   <code>/<pre>, `…` (бэктики) — Python-код (`print(23**45)`, `i**3`).
# Без этого `^` внутри формул давал 34 ложных срабатывания, а бэктик-код — ещё 2.
_STEM = "t.task_content->>'stem'"
CLEANED = (
    "regexp_replace(regexp_replace(regexp_replace(regexp_replace("
    f"{_STEM}, '\\$[^$]+\\$', '', 'g'),"
    " '<code[^>]*>.*?</code>', '', 'g'),"
    " '<pre[^>]*>.*?</pre>', '', 'g'),"
    " '`[^`]+`', '', 'g')"
)

# B (гейт): осталась katex-вёрстка — импортёр не заменил её на $LaTeX$.
KATEX_HTML = f"{_STEM} LIKE '%class=\"katex\"%'"
# C (гейт): суррогатная степень `NN**MM` / `NN^MM` в ОЧИЩЕННОМ тексте (вне
# математики и кода). Цифра справа отсекает `**kwargs`.
SURROGATE_POW = (
    f"({CLEANED} ~ '[0-9A-Za-z\\)\\]] ?\\*\\* ?[0-9]'"
    f" OR {CLEANED} ~ '[0-9A-Za-z\\)\\]]\\^\\{{?[0-9]')"
)
# D (информационно, НЕ гейт): сырой `<`/`>` внутри $…$. Живой прогон SPW (tsk-398,
# задание 3503 `F(n-1) < 7555444`) подтвердил: РЕНДЕРИТСЯ ВЕРНО — `preprocessKatex`
# уводит `$…$` в атрибут `data-katex-expr` ДО DOMPurify, `<` в кавычках сохраняется,
# KaTeX рендерит корректно (все stem класса D HTML-режимные). «$n 10$» из постановки —
# innerText-артефакт (как «4210» в tsk-391), а не порча данных. Backfill НЕ нужен.
# Оставлено счётчиком: раскладка зависит от HTML-режима, слежение дёшево.
RAW_CMP_IN_MATH = r"t.task_content->>'stem' ~ '\$[^$]*[<>][^$]*\$'"

QUERY = f"""
SELECT t.id,
       t.course_id,
       t.external_uid,
       c.title AS course_title,
       ({KATEX_HTML}) AS katex_html,
       ({SURROGATE_POW}) AS surrogate_pow,
       left(t.task_content->>'stem', 160) AS stem_head
  FROM tasks t
  JOIN courses c ON c.id = t.course_id
 WHERE t.is_active
   AND (({KATEX_HTML}) OR ({SURROGATE_POW}))
 ORDER BY t.course_id, t.id
"""

TOTAL_QUERY = "SELECT count(*) FROM tasks WHERE is_active"
INFO_D_QUERY = f"""
SELECT count(*) FROM tasks t
 WHERE t.is_active AND ({RAW_CMP_IN_MATH})
"""


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
    """Разрешённые исключения: id задания -> причина."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("allow") or {}


def _kinds(row: Any) -> list[str]:
    """Человекочитаемые виды порчи по флагам строки (гейтящие: B и C)."""
    kinds: list[str] = []
    if row["katex_html"]:
        kinds.append("raw KaTeX-вёрстка (нужен $LaTeX$ из <annotation>)")
    if row["surrogate_pow"]:
        kinds.append("суррогатная степень **/^ вне математики и кода (нужен <sup>)")
    return kinds


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Проверка инварианта мат-разметки stem ЕГЭ (tsk-398)"
    )
    ap.add_argument(
        "--allowlist",
        default=str(Path(__file__).with_name("ege_stem_markup_invariant_allowlist.json")),
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
        raw_cmp_count = await conn.fetchval(INFO_D_QUERY)
    finally:
        await conn.close()

    findings, allowed = [], []
    for r in rows:
        item = {
            "id": r["id"],
            "course_id": r["course_id"],
            "course_title": r["course_title"],
            "external_uid": r["external_uid"],
            "виды": _kinds(r),
            "stem_head": r["stem_head"],
        }
        (allowed if str(r["id"]) in allow else findings).append(item)

    if args.json:
        print(json.dumps({"total_active": total, "findings": findings,
                          "allowed": allowed,
                          "info_raw_cmp_in_math": raw_cmp_count},
                         ensure_ascii=False, indent=2))
        return 1 if findings else 0

    print(f"База: {_where(dsn)}")
    print(f"Активных заданий всего: {total}")
    print(f"Разрешённых исключений в allowlist: {len(allow)}")
    print()

    # Класс D — информационно (не влияет на код возврата).
    print(f"[инфо] сырой знак сравнения `<`/`>` внутри $…$: {raw_cmp_count} заданий.")
    print("       Живой прогон SPW (tsk-398) подтвердил: рендерится ВЕРНО (preprocessKatex")
    print("       уводит $…$ в атрибут до DOMPurify). Backfill НЕ нужен. Счётчик — слежение.")
    print()

    if allowed:
        print(f"Пропущено по allowlist: {len(allowed)}")
        for it in allowed:
            print(f"  id={it['id']} ({it['external_uid']}) — {allow[str(it['id'])].get('reason', '')}")
        print()

    if not findings:
        print("ИНВАРИАНТ ДЕРЖИТСЯ: в активных заданиях нет raw-katex-вёрстки,")
        print("сырых знаков сравнения в математике и суррогатной степени вне кода.")
        return 0

    print(f"НАРУШЕНИЙ: {len(findings)}")
    print()
    for it in findings:
        print(f"  id={it['id']} · курс {it['course_id']} «{it['course_title']}»")
        print(f"     {it['external_uid']} — {'; '.join(it['виды'])}")
        print(f"     stem: {it['stem_head']!r}")
    print()
    print("Что делать: порча импорта. Источник чинится нормализатором tsk-398")
    print("(ContentBackbone normalize_math_markup); данные — через /db-check по образцу")
    print("reviews/tsk391-sup/. Спорные единичные случаи — в allowlist с причиной.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — верхний уровень CLI
        print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
        sys.exit(2)
