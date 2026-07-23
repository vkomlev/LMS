# -*- coding: utf-8 -*-
"""Инвариант ОГЭ-заданий: ответ обязателен там, где проверка автоматическая.

ЧЕМ ОТЛИЧАЕТСЯ ОТ ЕГЭ
Для ЕГЭ правило простое: всё авто-проверяемое, поэтому активное задание без ответа —
всегда дефект (см. scripts/ege_answer_invariant.py). У ОГЭ так нельзя: там ручная
проверка ЗАКОННА для части 2, и слепой перенос ЕГЭ-инварианта дал бы сотню ложных
срабатываний. Поэтому здесь правило зависит от НОМЕРА задания.

СТРУКТУРА ОГЭ ПО ИНФОРМАТИКЕ (проверено по данным прода 2026-07-24)
  Задания 1-12  — часть 1, краткий ответ. Ответ обязателен, ручной проверки быть
                  не должно (её появление = тот самый маркер «ответ не разобрали»).
  Задание 14    — электронная таблица: 3 вопроса, из них 2 имеют проверяемый
                  короткий ответ, третий (построение формулы) проверяется человеком.
                  Ответ обязателен, но ручная проверка при этом ЗАКОННА.
  Задания 13,15,16 — часть 2 (презентация/текст, Робот в КуМир, программа):
                  проверяются человеком, ответа может не быть. Но если ответа нет,
                  задание ОБЯЗАНО стоять на ручной проверке — иначе оно молча
                  принимает любой ввод и никем не оценивается.

ЧТО ПРОВЕРЯЕТ
  A. Часть 1 (1-12) без ответа.
  B. Часть 1 (1-12) на ручной проверке — для полностью авто-проверяемой части это
     след неразобранного ответа.
  C. Задание 14 вообще без ответа (проверяемые вопросы обязаны иметь эталон).
  D. Часть 2 (13,15,16) без ответа И без ручной проверки — молча непроверяемое.
  E. Задание с номером, для которого правило не задано — чтобы новый курс
     (например wp:oge-z17) не проскользнул мимо проверки молча.
Задание из allowlist исключается из всех проверок.

ТОЛЬКО ЧТЕНИЕ: единственный SQL — SELECT. Ничего не пишет.
КОД ВОЗВРАТА: 0 — инвариант держится; 1 — нарушения; 2 — ошибка запуска.

ЗАПУСК
  venv/bin/python scripts/oge_answer_invariant.py            # на проде (/opt/lms)
  python scripts/oge_answer_invariant.py --json

ВНИМАНИЕ: локальный .env указывает на DEV-базу. Скрипт печатает хост и имя БД —
сверяйтесь с ними, прежде чем делать вывод о проде.
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

# ---------------------------------------------------------------------------
# Таблица правил. Спецификация ОГЭ меняется по годам — правится только этот блок.
# ---------------------------------------------------------------------------
PART1 = range(1, 13)          # 1-12: ответ обязателен, ручной проверки быть не должно
PARTIAL_AUTO = {14}           # 14: ответ обязателен, ручная проверка законна (2 из 3 вопросов)
MANUAL_PART2 = {13, 15, 16}   # 13,15,16: ответ не обязателен, но нужна пометка ручной проверки

NO_ANSWER = """
    COALESCE(jsonb_array_length(t.solution_rules->'short_answer'->'accepted_answers'), 0) = 0
AND COALESCE(t.solution_rules->>'text_answer', '') = ''
AND COALESCE(jsonb_array_length(t.solution_rules->'correct_options'), 0) = 0
AND COALESCE(t.solution_rules->'quiz'->'correct', '[]'::jsonb) IN ('[]'::jsonb, 'null'::jsonb)
"""

QUERY = f"""
SELECT t.id,
       t.course_id,
       t.external_uid,
       c.course_uid,
       c.title AS course_title,
       (regexp_match(c.course_uid, 'wp:oge-z(\\d+)$'))[1]::int AS zadanie,
       ({NO_ANSWER}) AS no_answer,
       COALESCE((t.solution_rules->>'manual_review_required')::bool, false) AS manual_review,
       left(t.task_content->>'answer_raw', 80) AS answer_raw
  FROM tasks t
  JOIN courses c ON c.id = t.course_id
 WHERE t.is_active
   AND c.course_uid ~ '^wp:oge-z\\d+$'
 ORDER BY (regexp_match(c.course_uid, 'wp:oge-z(\\d+)$'))[1]::int, t.id
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
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("allow") or {}


def violation(z: Optional[int], no_answer: bool, manual: bool) -> Optional[str]:
    """Нарушено ли правило для задания номер z. Возвращает причину или None."""
    if z is None or not (z in PART1 or z in PARTIAL_AUTO or z in MANUAL_PART2):
        return (f"правило для задания №{z} не задано — новый курс проскользнул бы "
                f"мимо проверки; допишите его в таблицу правил скрипта")
    if z in PART1:
        if no_answer:
            return "часть 1 (задания 1-12): ответ обязателен, но его нет"
        if manual:
            return ("часть 1 (задания 1-12) проверяется автоматически, "
                    "а задание стоит на ручной проверке — след неразобранного ответа")
        return None
    if z in PARTIAL_AUTO:
        if no_answer:
            return ("задание 14: проверяемые вопросы (2 из 3) обязаны иметь эталон, "
                    "но ответа нет вовсе")
        return None
    # MANUAL_PART2: ответа может не быть, но тогда нужна пометка ручной проверки
    if no_answer and not manual:
        return ("часть 2: ответа нет и ручная проверка не включена — "
                "задание молча принимает любой ввод и никем не оценивается")
    return None


async def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка инварианта ответов ОГЭ")
    ap.add_argument(
        "--allowlist",
        default=str(Path(__file__).with_name("oge_answer_invariant_allowlist.json")),
        help="JSON с разрешёнными исключениями",
    )
    ap.add_argument("--json", action="store_true", help="вывести находки как JSON")
    args = ap.parse_args()

    allow = _load_allowlist(Path(args.allowlist))
    dsn = _dsn()

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(QUERY)
    finally:
        await conn.close()

    findings, allowed = [], []
    by_z: dict[int, dict[str, int]] = {}
    for r in rows:
        z = r["zadanie"]
        stat = by_z.setdefault(z, {"всего": 0, "без_ответа": 0, "ручная": 0})
        stat["всего"] += 1
        stat["без_ответа"] += int(r["no_answer"])
        stat["ручная"] += int(r["manual_review"])

        why = violation(z, r["no_answer"], r["manual_review"])
        if why is None:
            continue
        item = {
            "id": r["id"], "задание": z, "course_id": r["course_id"],
            "course_title": r["course_title"], "external_uid": r["external_uid"],
            "причина": why, "answer_raw": r["answer_raw"],
        }
        (allowed if str(r["id"]) in allow else findings).append(item)

    if args.json:
        print(json.dumps({"total_active": len(rows), "by_zadanie": by_z,
                          "findings": findings, "allowed": allowed},
                         ensure_ascii=False, indent=2))
        return 1 if findings else 0

    print(f"База: {_where(dsn)}")
    print(f"Активных ОГЭ-заданий: {len(rows)}")
    print(f"Разрешённых исключений в allowlist: {len(allow)}")
    print()
    print("Раскладка по номеру задания (всего / без ответа / на ручной проверке):")
    for z in sorted(by_z):
        s = by_z[z]
        part = ("часть 1" if z in PART1 else
                "задание 14 (2 из 3 вопросов)" if z in PARTIAL_AUTO else
                "часть 2, ручная" if z in MANUAL_PART2 else "ПРАВИЛО НЕ ЗАДАНО")
        print(f"  №{z:<3} {s['всего']:>4} / {s['без_ответа']:>3} / {s['ручная']:>3}   {part}")
    print()

    if allowed:
        print(f"Пропущено по allowlist: {len(allowed)}")
        for it in allowed:
            print(f"  id={it['id']} ({it['external_uid']}) — {allow[str(it['id'])].get('reason', '')}")
        print()

    if not findings:
        print("ИНВАРИАНТ ДЕРЖИТСЯ: у части 1 и задания 14 эталоны на месте,")
        print("а задания части 2 без ответа честно стоят на ручной проверке.")
        return 0

    print(f"НАРУШЕНИЙ: {len(findings)}")
    print()
    for it in findings:
        print(f"  id={it['id']} · задание №{it['задание']} · курс {it['course_id']} «{it['course_title']}»")
        print(f"     {it['external_uid']} — {it['причина']}")
        if it["answer_raw"]:
            # Ответ уже лежит в задании, просто не прогнан нормализатором (частый случай).
            print(f"     answer_raw: {it['answer_raw']!r}  <- ответ есть, нужен разбор")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — верхний уровень CLI
        print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
        sys.exit(2)
