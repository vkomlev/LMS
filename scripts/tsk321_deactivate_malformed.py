# -*- coding: utf-8 -*-
"""tsk-321 (хвост): деактивировать 2 недоделанных вспомогательных задания №5.

КОНТЕКСТ
4820 (lms:c156:vvod:5_4) и 4821 (lms:c156:vvod:5_5) — «Дана строка S. …» —
сформулированы с АБСТРАКТНЫМ входом S без конкретной строки, поэтому
фиксированного ответа у них не бывает (в отличие от соседей 5_2/5_3, где вход
задан числами и ответ есть). Оба несут пустой ответ + manual_review_required=true.
Пока автор не доопределит вход и ответ — задания скрываются из активных, чтобы
ученик не упирался в непроверяемое задание.

ГАРД / ОБРАТИМОСТЬ
UPDATE только для явных id, только если is_active=true И ответа нет. Обратимо
(вернуть is_active=true). dry-run по умолчанию.

ЗАПУСК (на проде /opt/lms)
  venv/bin/python scripts/tsk321_deactivate_malformed.py            # dry-run
  DBCHECK_OK=1 venv/bin/python scripts/tsk321_deactivate_malformed.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Optional

import asyncpg

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

IDS = [4820, 4821]


def _dsn() -> str:
    url: Optional[str] = os.environ.get("DATABASE_URL")
    if not url:
        for line in (Path(__file__).resolve().parents[1] / ".env").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise SystemExit("DATABASE_URL не найден")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.apply and os.environ.get("DBCHECK_OK") != "1":
        raise SystemExit("--apply требует префикс DBCHECK_OK=1")

    conn = await asyncpg.connect(_dsn())
    tx = conn.transaction()
    await tx.start()
    done = 0
    try:
        for tid in IDS:
            row = await conn.fetchrow(
                "SELECT external_uid, is_active FROM tasks WHERE id=$1", tid
            )
            if row is None:
                print(f"[SKIP] id={tid}: нет в БД")
                continue
            res = await conn.execute(
                "UPDATE tasks SET is_active=false "
                "WHERE id=$1 AND is_active=true "
                "AND COALESCE(jsonb_array_length(solution_rules->'short_answer'->'accepted_answers'),0)=0",
                tid,
            )
            ok = res.endswith(" 1")
            done += int(ok)
            print(f"[{'OK' if ok else 'NOCHANGE'}] id={tid} ({row['external_uid']}) is_active={row['is_active']} -> false")
        print(f"\nИтого деактивировано: {done}/{len(IDS)}")
        if args.apply:
            await tx.commit()
            print("РЕЖИМ: --apply → ЗАФИКСИРОВАНО.")
        else:
            await tx.rollback()
            print("РЕЖИМ: dry-run → ОТКАЧЕНО.")
    except Exception:
        await tx.rollback()
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
