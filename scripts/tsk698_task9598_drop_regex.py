"""tsk-698 — снять обход `use_regex` у задания 9598 (курс 1407, Arduino `map()`).

Обход поставили в tsk-687, когда верный ответ заворачивался из-за пробелов вокруг
знаков. Общую причину починили в tsk-694 (шаг `strip_punctuation` ставит на место
знака пробел), но у 9598 этого шага в `normalization` нет — поэтому снять regex
недостаточно: нужно ещё вернуть заданию канонную нормализацию кода и добавить
второй эталон (ученик вправе не писать `int`, переменная уже объявлена в стеме).

План правки `tasks.solution_rules->'short_answer'` у id=9598:
  use_regex:      true -> false
  regex:          <строка> -> null
  normalization:  [trim, collapse_spaces, code_ast]
                  -> [trim, strip_punctuation, collapse_spaces, code_ast]
  accepted_answers: + "urovenj = map(syroe, 0, 1023, 0, 50);" (score 1)

Остальные ключи `solution_rules` не трогаются. Проверка не ослабляется по числам:
ответы с опечаткой (`102` вместо `1023`, `500` вместо `50`) остаются незачётом —
это проверяется прогоном РЕАЛЬНЫХ сдач до и после записи.

Запуск:
    python scripts/tsk698_task9598_drop_regex.py              # план, без записи
    DBCHECK_OK=1 python scripts/tsk698_task9598_drop_regex.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TASK_ID = 9598
OLD_REGEX = (
    r"(int\s+)?urovenj\s*=\s*map\s*\(\s*syroe\s*,\s*0\s*,\s*1023\s*,\s*0\s*,\s*50\s*\)\s*;?"
)
NEW_NORMALIZATION = ["trim", "strip_punctuation", "collapse_spaces", "code_ast"]
EXTRA_ANSWER = "urovenj = map(syroe, 0, 1023, 0, 50);"

# Реальные сдачи по заданию (task_results) + два контрольных ответа.
CONTROL_WRONG = [
    "urovenj = map(syroe, 0, 1023, 0, 500);",
    "urovenj = map(syroe, 0, 1023);",
]


def prod_dsn() -> str:
    """DSN боевой базы из .mcp.json (алиас learn_prod_db)."""
    cfg = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    for arg in cfg["mcpServers"]["learn_prod_db"]["args"]:
        if arg.startswith("postgresql://"):
            parts = urlsplit(arg)
            if "5.42.107.253" not in (parts.hostname or ""):
                raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
            return arg
    raise RuntimeError("DSN боевой базы не найден в .mcp.json")


def verdicts(rules: Dict[str, Any], answers: List[str]) -> Dict[str, bool]:
    """Вердикт по каждому ответу для данного блока правил (боевым кодом сервиса)."""
    from app.services.checking_service import CheckingService

    steps = rules.get("normalization") or []
    refs = [a.get("value") or "" for a in (rules.get("accepted_answers") or [])]
    out: Dict[str, bool] = {}
    for answer in answers:
        ok = False
        if rules.get("use_regex") and rules.get("regex"):
            import re

            norm = CheckingService._normalize_text(answer, steps)
            ok = bool(re.compile(rules["regex"]).fullmatch(norm))
        if not ok:
            ok = any(
                CheckingService._matches_short_answer(answer, ref, steps) for ref in refs
            )
        out[answer] = ok
    return out


def build_new_rules(old: Dict[str, Any]) -> Dict[str, Any]:
    """Новый блок solution_rules: точечная замена внутри short_answer."""
    new = json.loads(json.dumps(old))  # глубокая копия, чужие ключи не трогаем
    sa = new["short_answer"]
    sa["use_regex"] = False
    sa["regex"] = None
    sa["normalization"] = NEW_NORMALIZATION
    values = [a.get("value") for a in sa.get("accepted_answers") or []]
    if EXTRA_ANSWER not in values:
        sa.setdefault("accepted_answers", []).append({"value": EXTRA_ANSWER, "score": 1})
    return new


def main(apply: bool) -> int:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print(f"=== tsk-698: задание {TASK_ID} — {'APPLY (COMMIT)' if apply else 'DRY-RUN (ROLLBACK)'} ===\n")

    # --- состояние ДО, строка заблокирована на время правки ---
    cur.execute(
        "SELECT id, course_id, is_active, solution_rules FROM tasks WHERE id = %s FOR UPDATE",
        (TASK_ID,),
    )
    row = cur.fetchone()
    if row is None:
        print(f"Задание {TASK_ID} не найдено — нечего править.")
        conn.rollback()
        return 1

    old_rules = row["solution_rules"]
    old_sa = old_rules.get("short_answer") or {}
    print("ДО:")
    print(f"  use_regex      = {old_sa.get('use_regex')}")
    print(f"  regex          = {old_sa.get('regex')}")
    print(f"  normalization  = {old_sa.get('normalization')}")
    print(f"  accepted       = {[a.get('value') for a in old_sa.get('accepted_answers') or []]}")

    if old_sa.get("regex") != OLD_REGEX:
        print("\nСТОП: regex в базе не тот, что ожидался планом. Правила уже меняли — разобрать вручную.")
        conn.rollback()
        return 2

    # --- реальные сдачи учеников ---
    cur.execute(
        """
        SELECT DISTINCT answer_json->'response'->>'value' AS value
        FROM task_results
        WHERE task_id = %s AND answer_json->'response'->>'value' IS NOT NULL
        ORDER BY 1
        """,
        (TASK_ID,),
    )
    real = [r["value"] for r in cur.fetchall()]
    answers = real + CONTROL_WRONG
    print(f"\nРеальных сдач (различных ответов): {len(real)}; контрольных неверных: {len(CONTROL_WRONG)}")

    new_rules = build_new_rules(old_rules)
    before = verdicts(old_sa, answers)
    after = verdicts(new_rules["short_answer"], answers)

    print("\nВердикты (ответ | сейчас | после):")
    regressions = []
    for answer in answers:
        mark = "  "
        if before[answer] and not after[answer]:
            mark = "!!"
            regressions.append(answer)
        elif after[answer] and not before[answer]:
            mark = "+ "
        print(f"  {mark} {answer!r:50s} | {before[answer]!s:5s} | {after[answer]}")

    if regressions:
        print("\nСТОП: после правки перестают проходить ранее зачтённые ответы:")
        for answer in regressions:
            print(f"  {answer!r}")
        conn.rollback()
        return 3

    print("\nПОСЛЕ (план):")
    new_sa = new_rules["short_answer"]
    print(f"  use_regex      = {new_sa['use_regex']}")
    print(f"  regex          = {new_sa['regex']}")
    print(f"  normalization  = {new_sa['normalization']}")
    print(f"  accepted       = {[a.get('value') for a in new_sa['accepted_answers']]}")

    # --- запись ---
    cur.execute(
        "UPDATE tasks SET solution_rules = %s::jsonb WHERE id = %s",
        (json.dumps(new_rules, ensure_ascii=False), TASK_ID),
    )
    print(f"\nUPDATE затронул строк: {cur.rowcount}")

    # --- верификация в той же транзакции ---
    cur.execute("SELECT solution_rules FROM tasks WHERE id = %s", (TASK_ID,))
    saved = cur.fetchone()["solution_rules"]
    saved_sa = saved.get("short_answer") or {}
    checks = {
        "use_regex выключен": saved_sa.get("use_regex") is False,
        "regex снят": saved_sa.get("regex") is None,
        "нормализация обновлена": saved_sa.get("normalization") == NEW_NORMALIZATION,
        "второй эталон на месте": EXTRA_ANSWER
        in [a.get("value") for a in saved_sa.get("accepted_answers") or []],
        "остальные ключи целы": set(saved.keys()) == set(old_rules.keys()),
        "max_score не тронут": saved.get("max_score") == old_rules.get("max_score"),
    }
    print("\nВерификация записи:")
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")

    # правила из базы должны судить так же, как план
    saved_verdicts = verdicts(saved_sa, answers)
    same = saved_verdicts == after
    print(f"  {'OK  ' if same else 'FAIL'} вердикты по правилам ИЗ БАЗЫ совпали с планом")

    if not all(checks.values()) or not same:
        print("\nROLLBACK: верификация не прошла.")
        conn.rollback()
        return 4

    if apply:
        conn.commit()
        print("\nCOMMIT. Обход снят.")
    else:
        conn.rollback()
        print("\nROLLBACK — dry-run, база не изменена.")

    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="записать изменения в базу")
    sys.exit(main(apply=parser.parse_args().apply))
