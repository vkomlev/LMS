# -*- coding: utf-8 -*-
"""tsk-950: записать черновики критериев оценивания через боевой API.

Запуск на сервере под `app`:
    /opt/lms/venv/bin/python tsk950_apply_criteria.py tsk950_criteria.json [--apply]

Без --apply — только чтение и план. Ключ сервиса читается из /opt/lms/.env
внутри скрипта (в argv и логи не попадает). Для каждого задания:
GET /tasks/{id} → если grading_criteria уже есть — пропуск → иначе PATCH с полным
solution_rules, где добавлен блок grading_criteria (status=draft, origin=manual).
Остальное правило не трогается; content_provenance ставит сам обработчик.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

BASE = "http://127.0.0.1:8000/api/v1"
ENV = Path("/opt/lms/.env")


def _api_key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("VALID_API_KEYS="):
            return line.split("=", 1)[1].split(",")[0].strip().strip('"').strip("'")
    raise SystemExit("VALID_API_KEYS не найден в .env")


def _call(method: str, path: str, key: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-API-Key", key)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} → {e.code}: {detail[:400]}") from e


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    apply = "--apply" in sys.argv
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    key = _api_key()
    done = skipped = failed = 0
    for tid_s, crit in sorted(plan.items(), key=lambda kv: int(kv[0])):
        tid = int(tid_s)
        try:
            task = _call("GET", f"/tasks/{tid}", key)
        except RuntimeError as e:
            print(f"[FAIL] {tid}: {e}")
            failed += 1
            continue
        rules = task.get("solution_rules") or {}
        if rules.get("grading_criteria"):
            print(f"[SKIP] {tid}: критерии уже есть (status={rules['grading_criteria'].get('status')})")
            skipped += 1
            continue
        if rules.get("short_answer") and (rules["short_answer"] or {}).get("accepted_answers"):
            print(f"[SKIP] {tid}: у задания есть эталон — не тот класс, проверить руками")
            skipped += 1
            continue
        new_rules = dict(rules)
        new_rules["grading_criteria"] = {
            "must": crit["must"],
            "accept": crit.get("accept", []),
            "reject": crit.get("reject", []),
            "notes": crit.get("notes"),
            "status": "draft",
            "origin": "manual",
        }
        if not apply:
            print(f"[PLAN] {tid}: must={len(crit['must'])} accept={len(crit.get('accept', []))} "
                  f"reject={len(crit.get('reject', []))} notes={len(crit.get('notes') or '')} "
                  f"mrr={rules.get('manual_review_required')} auto={rules.get('auto_check')}")
            continue
        try:
            updated = _call("PATCH", f"/tasks/{tid}", key, {"solution_rules": new_rules})
        except RuntimeError as e:
            print(f"[FAIL] {tid}: {e}")
            failed += 1
            continue
        gc = (updated.get("solution_rules") or {}).get("grading_criteria") or {}
        ok = gc.get("status") == "draft" and len(gc.get("must", [])) == len(crit["must"])
        print(f"[{'OK' if ok else 'WARN'}] {tid}: записано, status={gc.get('status')}, must={len(gc.get('must', []))}")
        done += 1
    print(f"итого: записано {done}, пропущено {skipped}, ошибок {failed}, режим={'APPLY' if apply else 'PLAN'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
