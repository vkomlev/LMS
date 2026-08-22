"""Сверка старых незачётов с нынешними правилами заданий (tsk-602).

Зачем. Правка `solution_rules.accepted_answers` не пересчитывает уже выставленные
вердикты, а истории изменения правил в базе нет (`task_audit` пишет только курс и
активность). Поэтому каждое расширение эталона оставляет позади незачёты, которые
по нынешним правилам были бы зачётами, и отличить их от настоящих ошибок ученика
можно только прогоном.

Что делает. Берёт авто-проверенные незачёты (`is_correct = false`, `checked_by IS NULL`)
и прогоняет каждый через НАСТОЯЩИЙ код проверки — `CheckingService.check_task`, тот же,
что работает на приёме ответа. Расхождение означает: правила задания изменились после
сдачи. Решение о пересчёте принимает человек — скрипт только показывает список.

Почему нельзя сверять SQL-запросом. Модель нормализации на SQL расходится с Python:
`[[:punct:]]` в PostgreSQL удаляет неразрывный пробел, а Python считает его пробельным.
На разборе tsk-602 это дало 4 ложных совпадения из 10. Единственный достоверный
критерий — вызов самого сервиса.

Только чтение. Ни одного UPDATE/INSERT; соединение открывается в режиме read-only.

tsk-636 сделал разовый скрипт РЕГУЛЯРНЫМ. Причина: находку 8 августа (10 работ с
незаслуженным незачётом) никто не заметил две недели, а за это время добавилась
одиннадцатая. Расхождение не падает, не пишется в лог и не видно на экране — значит
искать его должен планировщик, а не случайный разбор. Под планировщиком чек идёт
через общий вход ``scripts/weekly_checks.py stale-verdicts``; задачи ставит
``scripts/install_weekly_checks.ps1``.

Что тут НЕ доказывается. Расхождение с нынешними правилами само по себе не значит
«движок ошибся»: у всех десяти работ разбора tsk-636 причиной оказалась правка эталона
уже после сдачи. Отличать одно от другого умеет журнал ``task_audit`` (колонки
``old_answer_key`` / ``new_answer_key``, tsk-636, см. docs/ai/task-audit.md) — этот
скрипт только показывает, где смотреть.

Запуск (из корня LMS):
  python scripts/audit_stale_false_verdicts_tsk602.py
  python scripts/audit_stale_false_verdicts_tsk602.py --course 157
  python scripts/audit_stale_false_verdicts_tsk602.py --csv reviews/stale-verdicts.csv
  python scripts/audit_stale_false_verdicts_tsk602.py --quiet   # для планировщика

Коды выхода: 0 — расхождений нет; 1 — найдены; 2 — ошибка выполнения.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, unquote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.exceptions import DomainError  # noqa: E402
from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402


def _expected_of(solution_rules: Optional[dict[str, Any]]) -> str:
    """Короткая запись нынешнего эталона задания — чтобы человек видел, с чем сверялись."""
    rules = solution_rules or {}
    short_answer = rules.get("short_answer") or {}
    accepted = [a.get("value") for a in (short_answer.get("accepted_answers") or [])]
    if accepted:
        return " | ".join(str(v) for v in accepted)
    options = rules.get("correct_options") or []
    if options:
        return "варианты: " + ", ".join(str(v) for v in options)
    return "—"

# Задания с песочницей черепахи пропускаем: их проверка исполняет код ученика
# (app.services.turtle_sandbox), это не сравнение текста и не должно запускаться
# пачкой в аудите. Число пропущенных печатается — молчаливого сокращения охвата нет.
SQL = """
SELECT tr.id            AS result_id,
       tr.task_id,
       tr.user_id,
       tr.submitted_at,
       tr.score,
       tr.max_score,
       tr.answer_json,
       t.course_id,
       t.external_uid,
       t.max_score      AS task_max_score,
       t.task_content,
       t.solution_rules
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
WHERE tr.is_correct = false
  AND tr.checked_by IS NULL
  AND t.is_active
  AND (%(course_id)s IS NULL OR t.course_id = %(course_id)s)
ORDER BY tr.submitted_at
"""


def _prod_connection_params() -> dict[str, Any]:
    """Читает боевое подключение из .mcp.json. Пароль не печатается."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    dsn = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parsed = urlparse(dsn)
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def _should_stay_silent(
    *,
    quiet: bool,
    stale: list[Any],
    type_changed: list[Any],
    failed: list[Any],
) -> bool:
    """Печатать ли отчёт вообще (tsk-636, режим планировщика).

    Еженедельный чек, пишущий «всё хорошо» каждую неделю, приучает не читать журнал —
    и настоящая находка тонет среди пятидесяти пустых строк. Поэтому в тихом режиме
    молчим, когда смотреть не на что.

    Смежные сигналы молчанием НЕ считаются: работа, у которой сменили тип задания, и
    работа, не прошедшая проверку схемой, — это тоже повод посмотреть, просто другой.
    Иначе чек тихо сузил бы свой охват, а выглядел бы как «чисто».

    :param quiet: включён ли режим планировщика (``--quiet``).
    :param stale: работы, расходящиеся с нынешними правилами.
    :param type_changed: работы, у которых после сдачи сменили тип задания.
    :param failed: работы, которые не удалось проверить.
    :returns: True — не печатать ничего.
    """
    return quiet and not stale and not type_changed and not failed


def _recheck(service: CheckingService, row: dict[str, Any]) -> Optional[bool]:
    """
    Прогоняет одну работу через нынешние правила задания.

    Args:
        service: Сервис проверки (тот же, что на приёме ответа).
        row: Строка выборки — работа вместе с заданием.

    Returns:
        True/False — нынешний вердикт; None, если вердикта нет (работа ушла бы
        в ручную очередь) либо проверка неприменима.
    """
    task_content = TaskContent.model_validate(row["task_content"])
    solution_rules = service.build_solution_rules(
        row["solution_rules"], fallback_max_score=row["task_max_score"] or 1
    )
    answer = StudentAnswer.model_validate(row["answer_json"])
    return service.check_task(task_content, solution_rules, answer).is_correct


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Показать незачёты, которые по нынешним правилам были бы зачётами"
    )
    parser.add_argument("--course", type=int, default=None, help="Ограничить одним курсом")
    parser.add_argument("--csv", type=Path, default=None, help="Выгрузить находки в CSV")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Режим планировщика: при находках отчёт печатается целиком, "
             "а когда смотреть не на что — ни строки.",
    )
    args = parser.parse_args()

    # Драйвер импортируется здесь, а не на уровне модуля: тогда чистые функции
    # скрипта проверяются тестами в окружении проекта, где psycopg2 не установлен.
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(**_prod_connection_params())
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(SQL, {"course_id": args.course})
    rows = cur.fetchall()
    conn.close()

    service = CheckingService()
    stale: list[dict[str, Any]] = []
    skipped_turtle = 0
    type_changed: list[tuple[int, int, str]] = []
    failed: list[tuple[int, str]] = []

    for row in rows:
        if (row["solution_rules"] or {}).get("turtle_sim") is not None:
            skipped_turtle += 1
            continue
        try:
            verdict = _recheck(service, row)
        except DomainError as exc:
            # Тип задания сменили после сдачи (SA_COM → TBL_COM, SC → MC и т.п.).
            # Это не сбой аудита, а отдельный вид расхождения: работу нынешними
            # правилами не проверить в принципе — ответ хранит прежний тип.
            if "не совпадает с типом задачи" in str(exc):
                type_changed.append((row["result_id"], row["task_id"], str(exc)))
            else:
                failed.append((row["result_id"], f"DomainError: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 — сводка по непроверяемым внизу
            failed.append((row["result_id"], f"{type(exc).__name__}: {exc}"))
            continue
        if verdict is True:
            stale.append(
                {
                    "result_id": row["result_id"],
                    "task_id": row["task_id"],
                    "course_id": row["course_id"],
                    "user_id": row["user_id"],
                    "submitted_at": row["submitted_at"].isoformat(),
                    "external_uid": row["external_uid"],
                    "answer": (row["answer_json"] or {}).get("response", {}).get("value")
                    or (row["answer_json"] or {}).get("response", {}).get("selected_option_ids"),
                    "expected": _expected_of(row["solution_rules"]),
                }
            )

    if _should_stay_silent(
        quiet=args.quiet, stale=stale, type_changed=type_changed, failed=failed
    ):
        return 0

    print("=== Сверка старых незачётов с нынешними правилами (tsk-602) ===")
    print(f"Проверено работ: {len(rows) - skipped_turtle} (пропущено с песочницей черепахи: {skipped_turtle})")
    print(f"Расходятся с нынешними правилами: {len(stale)}")
    print(f"Тип задания сменили после сдачи: {len(type_changed)}")
    if failed:
        print(f"Не удалось проверить: {len(failed)} — правила или ответ не проходят валидацию схемой")
        for result_id, err in failed[:10]:
            print(f"   result {result_id}: {err}")
        if len(failed) > 10:
            print(f"   … и ещё {len(failed) - 10}")

    for item in stale:
        print(
            f"\nresult {item['result_id']} — задание {item['task_id']} "
            f"(курс {item['course_id']}, {item['external_uid']})"
        )
        print(f"   ученик {item['user_id']}, сдано {item['submitted_at']}")
        print(f"   ответ ученика: {item['answer']!r}")
        print(f"   нынешний эталон: {item['expected']} — сейчас это зачёт")

    if type_changed:
        print("\n--- Тип задания сменили после сдачи (нынешними правилами не проверить) ---")
        for result_id, task_id, err in type_changed:
            print(f"   result {result_id} (задание {task_id}): {err}")

    if args.csv and stale:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(stale[0].keys()))
            writer.writeheader()
            writer.writerows(stale)
        print(f"\nCSV: {args.csv}")

    print(
        "\nЭто список для человека, а не команда к действию: вердикт мог быть верным "
        "на момент сдачи. Разбор tsk-636 показал, что так и было у всех десяти работ — "
        "эталон правили уже после сдачи. Проверить это по конкретному заданию: "
        "SELECT changed_at, changed_by, old_answer_key, new_answer_key FROM task_audit "
        "WHERE task_id = <id> AND new_answer_key IS NOT NULL ORDER BY changed_at; "
        "Решение о пересчёте принимает методист."
    )
    return 1 if stale else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(2)
