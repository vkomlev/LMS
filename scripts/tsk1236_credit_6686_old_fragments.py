"""tsk-1236: зачёт сдач задания 6686, давших корректный адрес из СТАРЫХ фрагментов.

Что и почему. Старое условие 6686 («25», «6.», «12.», «8.1») допускало 6 корректных
IP-адресов, а эталон 12.8.1.256 был невалиден (октет 256) — засчитать нельзя было ни один
ответ. Задание переписано (`tsk1236_fix_ip_fragment_tasks.py`). Решение оператора
2026-10-05: зачесть тем, кто дал любой корректный адрес из старых фрагментов.

Вердикт не выдумывается: каждая работа прогоняется через настоящий
`CheckingService.check_task` с правилом, где `accepted_answers` — все 6 корректных адресов
старых фрагментов (найдены перебором тут же, `old_fragment_addresses`). Новое правило
задания (буквы) к старым ответам не применимо. Соседние работы (256.12.8.1, 12.8.1.25)
движок обязан признать неверными — иначе стоп.

Пишутся те же поля, что у ручной дооценки (образец tsk-602): `score`, `is_correct`,
`checked_at`, `checked_by=2`, пояснение ученику в `metrics.comment`.

Запуск (из корня LMS):
  python scripts/tsk1236_credit_6686_old_fragments.py
  DBCHECK_OK=1 python scripts/tsk1236_credit_6686_old_fragments.py --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import traceback
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
import psycopg2.extras

from app.schemas.checking import StudentAnswer
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService

TASK_ID = 6686
OLD_FRAGMENTS = ["25", "6.", "12.", "8.1"]
#: работа → (ученик, ответ) — ожидаемое состояние «до»: незачёт без ручной проверки.
TARGETS: dict[int, tuple[int, str]] = {
    39769: (4568, "6.12.8.125"),
    40373: (4601, "8.112.6.25"),
    40374: (4601, "8.16.12.25"),
}
UNTOUCHED: dict[int, tuple[int, str]] = {
    39766: (4568, "12.8.1.25"),
    39767: (4568, "256.12.8.1"),
}
CHECKED_BY = 2
VERDICT_COMMENT = (
    "tsk-1236: в прежнем условии задания было несколько правильных адресов, а эталон был "
    "записан с ошибкой. Ваш адрес корректен — работа засчитана. Условие задания исправлено."
)


def valid_ip(s: str) -> bool:
    """Корректный IPv4: четыре числа 0..255 без ведущих нулей."""
    parts = s.split(".")
    return len(parts) == 4 and all(
        p.isdigit() and not (len(p) > 1 and p[0] == "0") and int(p) <= 255 for p in parts
    )


def old_fragment_addresses() -> list[str]:
    """Все корректные адреса из старых фрагментов."""
    return sorted({"".join(p) for p in permutations(OLD_FRAGMENTS) if valid_ip("".join(p))})


def _prod_params() -> dict[str, Any]:
    """Боевое подключение из .mcp.json. Пароль не печатается."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(host=parsed.hostname, port=parsed.port or 5432, dbname=parsed.path.lstrip("/"),
                user=unquote(parsed.username or ""), password=unquote(parsed.password or ""))


def _verdict(service: CheckingService, row: dict[str, Any], old_rules: dict[str, Any]) -> tuple[Any, int]:
    """Вердикт движка по работе при правиле из старых фрагментов."""
    content = TaskContent.model_validate(row["task_content"])
    rules = service.build_solution_rules(old_rules, fallback_max_score=1)
    result = service.check_task(content, rules, StudentAnswer.model_validate(row["answer_json"]))
    return result.is_correct, result.score


def main() -> int:
    """Сверить, прогнать через движок и (с --apply) записать зачёт."""
    parser = argparse.ArgumentParser(description="Зачёт старых сдач 6686 (tsk-1236)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    addresses = old_fragment_addresses()
    print(f"Корректные адреса старых фрагментов ({len(addresses)}): {addresses}")
    if len(addresses) != 6:
        print("ОТКАЗ: ожидалось 6 адресов.")
        return 1

    conn = psycopg2.connect(**_prod_params())
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    service = CheckingService()
    try:
        cur.execute(
            "SELECT tr.*, t.task_content, t.solution_rules FROM task_results tr "
            "JOIN tasks t ON t.id = tr.task_id WHERE tr.id = ANY(%s) FOR UPDATE OF tr",
            (list(TARGETS) + list(UNTOUCHED),),
        )
        rows = {r["id"]: r for r in cur.fetchall()}
        old_rules = copy.deepcopy(next(iter(rows.values()))["solution_rules"])
        old_rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": a} for a in addresses]
        old_rules["short_answer"]["normalization"] = ["trim", "collapse_spaces"]

        for rid, (uid, ans) in {**TARGETS, **UNTOUCHED}.items():
            row = rows.get(rid)
            value = ((row or {}).get("answer_json") or {}).get("response", {}).get("value")
            if row is None or row["task_id"] != TASK_ID or row["user_id"] != uid or value != ans:
                print(f"ОТКАЗ: работа {rid} не совпала с ожидаемой.")
                conn.rollback()
                return 1
            if row["is_correct"] is not False or row["score"] != 0 or row["checked_by"] is not None:
                print(f"ОТКАЗ: работа {rid} уже не нетронутый незачёт — чужое решение не затираем.")
                conn.rollback()
                return 1
            verdict, score = _verdict(service, row, old_rules)
            expected = rid in TARGETS
            print(f"{rid} ученик {uid} ответ {ans!r}: движок is_correct={verdict} score={score}")
            if (verdict is True) != expected:
                print(f"ОТКАЗ: движок разошёлся с планом по {rid}.")
                conn.rollback()
                return 1

        if not args.apply:
            conn.rollback()
            print("\nDRY-RUN: ничего не записано.")
            return 0

        now = datetime.now(timezone.utc)
        for rid in TARGETS:
            cur.execute(
                """
                UPDATE task_results
                SET is_correct = true, score = 1, checked_at = %(now)s, checked_by = %(by)s,
                    metrics = CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END
                              || jsonb_build_object('comment', %(comment)s::text)
                WHERE id = %(id)s AND is_correct = false AND checked_by IS NULL
                """,
                {"id": rid, "now": now, "by": CHECKED_BY, "comment": VERDICT_COMMENT},
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"{rid}: обновлено {cur.rowcount} строк")
        cur.execute(
            "SELECT id, is_correct, score, checked_by, metrics->>'comment' c FROM task_results WHERE id = ANY(%s)",
            (list(TARGETS) + list(UNTOUCHED),),
        )
        for r in cur.fetchall():
            want = (True, 1, CHECKED_BY, VERDICT_COMMENT) if r["id"] in TARGETS else (False, 0, None, None)
            if (r["is_correct"], r["score"], r["checked_by"], r["c"]) != want:
                raise RuntimeError(f"верификация {r['id']} не прошла")
        conn.commit()
        print(f"\nCOMMIT: засчитано {len(TARGETS)} работ, соседние не тронуты.")
        return 0
    except Exception as exc:  # noqa: BLE001 — любая осечка откатывает правку
        conn.rollback()
        traceback.print_exc()
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
