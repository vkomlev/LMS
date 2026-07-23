# -*- coding: utf-8 -*-
"""tsk-321 (хвост): заполнить эталонные ответы 14 ЕГЭ-заданий, застрявших без ответа.

КОНТЕКСТ
28 активных ЕГЭ-заданий несут solution_rules с пустым ответом и
manual_review_required=true (маркер «ответ не разобрали»). Отчёт-инвентарь:
reviews/2026-07-23-ege-28-bez-otveta-inventar.md. Здесь заполняются 14, у которых
верный ответ подтверждён ДВУМЯ независимыми признаками (решённый близнец в базе
ИЛИ живой забор из первоисточника) — маппинг в scripts/tsk321_answers.json.
Оставшиеся 14 (спорные, недоделки, яндекс без авторизации, tg:ege без источника)
здесь НЕ трогаются.

ЧТО ДЕЛАЕТ
Для каждого id из JSON:
  - строит short_answer (SA_COM): regex=null, use_regex=false,
    normalization=["trim","lower"], accepted_answers=[{score:1, value:V}, ...];
  - выставляет manual_review_required=false (задание становится авто-проверяемым);
  - остальные поля solution_rules не меняет.

ГАРД (идемпотентность / blast-radius)
UPDATE трогает строку ТОЛЬКО если её текущий short_answer = JSON null И
manual_review_required=true. Уже заполненные или иначе устроенные задания
пропускаются (SKIP). Обратимо: вернуть short_answer=null, manual_review_required=true.

ЗАПУСК (на проде /opt/lms, DSN из .env)
  dry-run (по умолчанию, транзакция откатывается):
    venv/bin/python scripts/tsk321_fill_answers.py --answers scripts/tsk321_answers.json
  применить:
    DBCHECK_OK=1 venv/bin/python scripts/tsk321_fill_answers.py --answers scripts/tsk321_answers.json --apply
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


def _dsn() -> str:
    """DSN прод-БД: из env DATABASE_URL или из .env, в форме asyncpg (без +asyncpg)."""
    url: Optional[str] = os.environ.get("DATABASE_URL")
    if not url:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise SystemExit("DATABASE_URL не найден ни в env, ни в .env")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


def _build_short_answer(values: list[str]) -> dict[str, Any]:
    """Собрать блок short_answer в форме уже работающих SA_COM (mirror задания 2197)."""
    return {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": [{"score": 1, "value": v} for v in values],
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True, help="путь к JSON с маппингом ответов")
    ap.add_argument("--apply", action="store_true", help="применить (иначе dry-run с откатом)")
    args = ap.parse_args()

    if args.apply and os.environ.get("DBCHECK_OK") != "1":
        raise SystemExit("--apply требует префикс DBCHECK_OK=1 (протокол /db-check пройден)")

    payload = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    answers: dict[str, dict[str, Any]] = payload["answers"]

    conn = await asyncpg.connect(_dsn())
    tx = conn.transaction()
    await tx.start()

    applied, skipped = 0, 0
    try:
        for tid_str, rec in answers.items():
            tid = int(tid_str)
            row = await conn.fetchrow(
                "SELECT external_uid, course_id, solution_rules FROM tasks WHERE id=$1", tid
            )
            if row is None:
                print(f"[SKIP] id={tid}: строки нет в БД")
                skipped += 1
                continue
            sr: dict[str, Any] = json.loads(row["solution_rules"]) if isinstance(
                row["solution_rules"], str
            ) else dict(row["solution_rules"])

            cur_sa = sr.get("short_answer")
            cur_mrr = sr.get("manual_review_required")
            if cur_sa is not None or cur_mrr is not True:
                print(
                    f"[SKIP] id={tid} ({row['external_uid']}): гард не прошёл "
                    f"(short_answer={cur_sa!r}, manual_review_required={cur_mrr!r})"
                )
                skipped += 1
                continue

            values: list[str] = rec["values"]
            sr["short_answer"] = _build_short_answer(values)
            sr["manual_review_required"] = False

            res = await conn.execute(
                "UPDATE tasks SET solution_rules=$1::jsonb "
                "WHERE id=$2 AND solution_rules->'short_answer'='null'::jsonb "
                "AND (solution_rules->>'manual_review_required')::bool IS TRUE",
                json.dumps(sr, ensure_ascii=False),
                tid,
            )
            ok = res.endswith(" 1")
            applied += int(ok)
            mark = "OK" if ok else "NOCHANGE"
            print(
                f"[{mark}] id={tid} ЕГЭ№{rec.get('ege')} ({row['external_uid']}, c{row['course_id']}) "
                f"| verify: {rec.get('verify')}\n         accepted={values}"
            )

        print(f"\nИтого: применимо={applied}, пропущено={skipped}, всего={len(answers)}")
        if args.apply:
            await tx.commit()
            print("РЕЖИМ: --apply → транзакция ЗАФИКСИРОВАНА.")
        else:
            await tx.rollback()
            print("РЕЖИМ: dry-run → транзакция ОТКАЧЕНА (записи нет).")
    except Exception:
        await tx.rollback()
        print("ОШИБКА → транзакция откачена.")
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
