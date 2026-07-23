# -*- coding: utf-8 -*-
"""Инвариант ЕГЭ-заданий: у активного задания обязан быть эталонный ответ.

ЗАЧЕМ
ЕГЭ по информатике — экзамен с коротким авто-проверяемым ответом; ручной проверки
у него нет by design. Поэтому активное ЕГЭ-задание без ответа означает, что оно
не проверяется, а ученик упирается в тупик.

Разбор tsk-321 показал, как такие задания копились МОЛЧА: импорт, не сумев
разобрать ответ, ставил manual_review_required=true и оставлял ответ только в
task_content.answer_raw. Каждая прошлая задача (tsk-358/362/367/371/373) чинила
свою партию по external_uid, но закрывающей проверки инварианта не было — поэтому
задания с другим режимом сбоя проваливались в щели и всплыли лишь побочно (tsk-350).
Этот скрипт и есть та закрывающая проверка.

ЧТО ПРОВЕРЯЕТ (обе проверки — прямые следствия первопричины)
  A. Нет ответа: пусты одновременно short_answer.accepted_answers, text_answer,
     correct_options и quiz.correct.
  B. Вернулся маркер-парковка: manual_review_required=true (для ЕГЭ это не режим
     оценивания, а след неразобранного ответа).
Задание, указанное в allowlist, из обеих проверок исключается.

ТОЛЬКО ЧТЕНИЕ: единственный SQL — SELECT. Ничего не пишет.

КОД ВОЗВРАТА: 0 — инвариант держится; 1 — найдены нарушения; 2 — ошибка запуска.
Годится для планировщика и pre-deploy проверки.

ЗАПУСК
  # на проде (/opt/lms) — DSN из .env
  venv/bin/python scripts/ege_answer_invariant.py
  # локально по прод-базе
  DATABASE_URL='postgresql://...' python scripts/ege_answer_invariant.py

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

# Охват «ЕГЭ-задание» — тот же, что у разбора tsk-350/tsk-321.
EGE_SCOPE = """
    c.course_uid LIKE 'wp:zadanie-%'
 OR c.course_uid LIKE 'lms:tsk347:hard:%'
 OR t.course_id IN (138, 139)
"""

# Пусто по всем четырём формам ответа.
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
       c.title AS course_title,
       ({NO_ANSWER}) AS no_answer,
       COALESCE((t.solution_rules->>'manual_review_required')::bool, false) AS manual_review,
       left(t.task_content->>'answer_raw', 80) AS answer_raw
  FROM tasks t
  JOIN courses c ON c.id = t.course_id
 WHERE t.is_active
   AND ({EGE_SCOPE})
   AND (({NO_ANSWER})
        OR COALESCE((t.solution_rules->>'manual_review_required')::bool, false))
 ORDER BY t.course_id, t.id
"""

TOTAL_QUERY = f"""
SELECT count(*) FROM tasks t JOIN courses c ON c.id = t.course_id
 WHERE t.is_active AND ({EGE_SCOPE})
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


async def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка инварианта эталонных ответов ЕГЭ")
    ap.add_argument(
        "--allowlist",
        default=str(Path(__file__).with_name("ege_answer_invariant_allowlist.json")),
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
            "нет_ответа": r["no_answer"],
            "ручная_проверка": r["manual_review"],
            "answer_raw": r["answer_raw"],
        }
        (allowed if str(r["id"]) in allow else findings).append(item)

    if args.json:
        print(json.dumps({"total_active": total, "findings": findings,
                          "allowed": allowed}, ensure_ascii=False, indent=2))
        return 1 if findings else 0

    print(f"База: {_where(dsn)}")
    print(f"Активных ЕГЭ-заданий: {total}")
    print(f"Разрешённых исключений в allowlist: {len(allow)}")
    print()

    if allowed:
        print(f"Пропущено по allowlist: {len(allowed)}")
        for it in allowed:
            print(f"  id={it['id']} ({it['external_uid']}) — {allow[str(it['id'])].get('reason', '')}")
        print()

    if not findings:
        print("ИНВАРИАНТ ДЕРЖИТСЯ: у всех активных ЕГЭ-заданий есть эталонный ответ,")
        print("ни одно не стоит на ручной проверке.")
        return 0

    print(f"НАРУШЕНИЙ: {len(findings)}")
    print()
    for it in findings:
        why = []
        if it["нет_ответа"]:
            why.append("нет эталонного ответа")
        if it["ручная_проверка"]:
            why.append("стоит на ручной проверке (для ЕГЭ это маркер неразобранного ответа)")
        print(f"  id={it['id']} · курс {it['course_id']} «{it['course_title']}»")
        print(f"     {it['external_uid']} — {'; '.join(why)}")
        if it["answer_raw"]:
            # Ответ уже лежит в задании, просто не прогнан нормализатором — самый частый случай.
            print(f"     answer_raw: {it['answer_raw']!r}  <- ответ есть, нужен разбор")
    print()
    print("Что делать: см. reviews/2026-07-23-ege-28-bez-otveta-inventar.md")
    print("(разбор первопричины и порядок заполнения через /db-check).")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — верхний уровень CLI
        print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
        sys.exit(2)
