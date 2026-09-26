"""tsk-1130: дописать синонимы в эталоны шести SA-заданий курса qa-manual (прод).

Зачем. Аудит дерева `wp:qa-manual%` (tsk-1130): задания «ответь одним словом»,
у которых ни одной формы эталона нет ни в стеме, ни в материалах своего урока, —
ученик называет понятие своим словом и получает незачёт (класс 8053, 8012).
План форм — `tsk1130_synonyms_plan.json` (собран `tsk1130_build_synonyms_plan.py`).
Сдач по этим заданиям на момент правки нет — пересчитывать вердикты нечего.

Протокол /db-check (режим записи): dry-run по умолчанию; текущий эталон
сверяется со снимком разведки (число и первый элемент); только добавление,
старые формы остаются первыми; движок проверки подтверждает, что и старый
эталон, и каждый новый синоним дают зачёт; одна транзакция, верификация до COMMIT.

Запуск (из корня LMS):
  .venv/Scripts/python.exe scripts/tsk1130_apply_synonyms.py
  DBCHECK_OK=1 .venv/Scripts/python.exe scripts/tsk1130_apply_synonyms.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")  # Settings при импорте сервиса проверки

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1130.apply")

PLAN = json.loads((Path(__file__).resolve().parent / "tsk1130_synonyms_plan.json").read_text(encoding="utf-8"))

#: Снимок разведки 2026-09-26: (число форм, первая форма).
BEFORE: Dict[int, Tuple[int, str]] = {
    8379: (12, "плохой"),
    8404: (13, "средняя"),
    8109: (9, "реализацией"),
    8161: (2, "влить"),
    8165: (1, "ожидаемо"),
    9383: (1, "дополнить"),
}


def prod_dsn() -> Dict[str, Any]:
    """Параметры боевой базы из `.mcp.json` (пароль не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    p = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(host=p.hostname, port=p.port or 5432, dbname=p.path.lstrip("/"),
                user=unquote(p.username or ""), password=unquote(p.password or ""))


def accepts(service: CheckingService, row: Any, rules_json: Any, value: str) -> bool:
    """Засчитывает ли движок проверки ответ `value` при эталоне `rules_json`."""
    content = TaskContent.model_validate(row["task_content"])
    rules = service.build_solution_rules(rules_json, fallback_max_score=row["max_score"] or 1)
    answer = StudentAnswer.model_validate({"type": "SA", "response": {"value": value}})
    return service.check_task(content, rules, answer).is_correct is True


def main() -> int:
    """Сверка, план, запись."""
    parser = argparse.ArgumentParser(description="tsk-1130: синонимы эталонов qa-manual")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    service = CheckingService()
    logger.info("Режим: %s", "APPLY" if args.apply else "DRY-RUN")
    try:
        cur.execute(
            "SELECT id, task_content, solution_rules, max_score FROM tasks "
            "WHERE id = ANY(%s) AND is_active FOR UPDATE",
            (list(BEFORE),),
        )
        rows = {r["id"]: r for r in cur.fetchall()}
        new_acc: Dict[int, list] = {}
        for task_id, (count, first) in BEFORE.items():
            row = rows.get(task_id)
            if row is None:
                raise RuntimeError(f"{task_id}: задание не найдено или выключено")
            acc = row["solution_rules"]["short_answer"]["accepted_answers"]
            if len(acc) != count or acc[0].get("value") != first:
                raise RuntimeError(f"{task_id}: эталон изменился со снимка: {json.dumps(acc, ensure_ascii=False)}")
            have = {a.get("value") for a in acc}
            add = [f for f in PLAN[str(task_id)] if f not in have]
            new_acc[task_id] = list(acc) + [{"score": acc[0].get("score", 1), "value": f} for f in add]
            rules = json.loads(json.dumps(row["solution_rules"]))
            rules["short_answer"]["accepted_answers"] = new_acc[task_id]
            if not accepts(service, row, rules, first):
                raise RuntimeError(f"{task_id}: старый эталон «{first}» перестал засчитываться")
            refused = [f for f in add if not accepts(service, row, rules, f)]
            if refused:
                raise RuntimeError(f"{task_id}: движок не засчитывает {refused}")
            logger.info("%s: %d → %d форм (+%s)", task_id, count, len(new_acc[task_id]), ", ".join(add[:6]) + ("…" if len(add) > 6 else ""))

        if not args.apply:
            logger.info("DRY-RUN: запись не выполнена.")
            conn.rollback()
            return 0

        for task_id, acc in new_acc.items():
            cur.execute(
                "UPDATE tasks SET solution_rules = jsonb_set(solution_rules, "
                "'{short_answer,accepted_answers}', %s::jsonb, true), updated_at = now() WHERE id = %s",
                (json.dumps(acc, ensure_ascii=False), task_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"{task_id}: обновлено {cur.rowcount} строк")
        cur.execute(
            "SELECT id, jsonb_array_length(solution_rules->'short_answer'->'accepted_answers') n "
            "FROM tasks WHERE id = ANY(%s)",
            (list(BEFORE),),
        )
        for r in cur.fetchall():
            if r["n"] != len(new_acc[r["id"]]):
                raise RuntimeError(f"{r['id']}: верификация не прошла")
        conn.commit()
        logger.info("COMMIT: обновлено %d заданий", len(new_acc))
        return 0
    except Exception:  # noqa: BLE001 — любая осечка откатывает всю правку
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
