"""tsk-1168: сколько верных ответов SA_COM/TBL_COM срезал гейт «комментарий или файл».

Что считаем. Гейт tsk-419 (с 2026-07-26) ставит 0 сдаче SA_COM/TBL_COM без
комментария и без файла у пары «попытка+задание», даже если ответ совпал с
эталоном. Причина отказа в базе не хранится (`metrics` пуст), поэтому ищем по
признакам: незачёт, комментарий пуст, у задания нет тестов ввода/вывода — и
прогоняем ответ через настоящий `CheckingService.check_task`. Если движок
говорит «верно», незачёт поставил гейт, а не сверка.

Для каждой такой сдачи выводим: указан ли в теле файл (и из какой попытки —
случай tsk-1168, когда кабинет подставил вложение прошлой), последняя ли это
сдача задания и сдал ли ученик задание позже верно.

Только чтение: транзакция READ ONLY, прод-DSN из .mcp.json, пароль не печатается.

Запуск (из корня LMS):
  python scripts/tsk1168_audit_evidence_gate_losses.py [--csv путь]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

GATE_SINCE = "2026-07-26"

SQL = """
SELECT tr.id, tr.task_id, tr.user_id, tr.attempt_id, tr.submitted_at, tr.answer_json,
       t.task_content, t.solution_rules, t.max_score AS task_max_score,
       t.task_content->>'type' AS type,
       EXISTS (SELECT 1 FROM task_results r WHERE r.task_id = tr.task_id AND r.user_id = tr.user_id
               AND r.submitted_at > tr.submitted_at) AS has_later,
       EXISTS (SELECT 1 FROM task_results r WHERE r.task_id = tr.task_id AND r.user_id = tr.user_id
               AND r.submitted_at > tr.submitted_at AND r.is_correct) AS passed_later
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
JOIN attempts a ON a.id = tr.attempt_id
WHERE t.task_content->>'type' IN ('SA_COM', 'TBL_COM')
  AND tr.is_correct = false AND tr.score = 0 AND tr.checked_by IS NULL
  AND tr.submitted_at >= %(since)s
  AND coalesce(btrim(tr.answer_json->'response'->>'comment'), '') = ''
  AND coalesce(t.solution_rules->'io_tests', 'null'::jsonb) = 'null'::jsonb
  AND coalesce(a.time_expired, false) = false
ORDER BY tr.submitted_at
"""


def _claimed_attempt(answer_json: dict[str, Any]) -> int | None:
    """Номер попытки файла, указанного в теле ответа (по адресу вложения)."""
    meta = ((answer_json or {}).get("response") or {}).get("meta") or {}
    items = meta.get("attachments") if isinstance(meta, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    m = re.search(r"/attempts/(\d+)/attachments/", str(items[0].get("attachment_url") or ""))
    return int(m.group(1)) if m else None


def main() -> int:
    """Точка входа: печать сводки и списка, опционально CSV."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", help="куда сохранить список")
    args = ap.parse_args()

    dsn = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["learn_prod_db"]["args"][-1]
    service = CheckingService()
    found: list[dict[str, Any]] = []
    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL, {"since": GATE_SINCE})
            rows = cur.fetchall()
    for r in rows:
        try:
            content = TaskContent.model_validate(r["task_content"])
            rules = service.build_solution_rules(r["solution_rules"], fallback_max_score=r["task_max_score"] or 1)
            result = service.check_task(content, rules, StudentAnswer.model_validate(r["answer_json"]))
        except Exception as exc:  # noqa: BLE001 — одна битая строка не должна ронять отчёт
            print(f"пропуск {r['id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if result.is_correct is not True:
            continue
        claimed = _claimed_attempt(r["answer_json"])
        found.append({
            "task_result_id": r["id"], "submitted_at": r["submitted_at"].isoformat(timespec="minutes"),
            "user_id": r["user_id"], "task_id": r["task_id"], "type": r["type"], "attempt_id": r["attempt_id"],
            "value": ((r["answer_json"] or {}).get("response") or {}).get("value"),
            "file_in_body": "нет" if claimed is None else ("своя попытка" if claimed == r["attempt_id"] else f"попытка {claimed}"),
            "is_last": not r["has_later"], "passed_later": r["passed_later"],
        })

    print(f"Кандидатов (незачёт, без комментария, с {GATE_SINCE}): {len(rows)}")
    print(f"Движок подтверждает верный ответ: {len(found)}")
    print(f"  из них файл прошлой попытки в теле: {sum(f['file_in_body'].startswith('попытка') for f in found)}")
    print(f"  из них потом сдано верно: {sum(f['passed_later'] for f in found)}")
    print(f"  из них незачёт — последняя сдача задания: {sum(f['is_last'] for f in found)}")
    for f in found:
        print(" | ".join(str(f[k]) for k in f))
    if args.csv and found:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(found[0]))
            w.writeheader()
            w.writerows(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
