"""Еженедельный отбор заданий, которые даются ученикам трудно (tsk-773).

Зачем. Дефект задания снаружи не виден: текст есть, правило проверки есть, ответ
формально принимается — а решить нельзя. Такие задания всплывали только когда ученик
не выдерживал и писал обращение (tsk-770 — эталон был длиной второго по краткости пути;
tsk-772 — из условия пропал список птиц, у другого задания выпала точка предложения).
Поведение учеников — самый ранний сигнал: задание, которое двое не сдали с первой
попытки, стоит посмотреть до того, как о нём напишут.

Что делает скрипт. Отбирает задания по сигналам за окно (по умолчанию неделя),
сверяет их с реестром ранее разобранных и делит на два потока:

* **впервые** — проверяем полноту, однозначность и правильность ответа
  (решатель + сверка с источником), очевидные дефекты правим сразу;
* **повторно** — корректность уже проверяли, значит дело в самом задании: сложность
  или понятность формулировки. Это методическая работа, идёт через ``/methodist``
  и решение оператора.

Скрипт только ЧИТАЕТ боевую базу; в реестр пишет отдельной командой ``--record``.

Запуск::

    python scripts/weekly_hard_tasks.py                  # отбор за 7 дней
    python scripts/weekly_hard_tasks.py --days 14        # другое окно
    python scripts/weekly_hard_tasks.py --report         # + сохранить отчёт в docs/qa
    python scripts/weekly_hard_tasks.py --record 6551 6553 --verdict fixed \\
        --note "эталон был неверен"                      # отметить разобранные
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("weekly-hard-tasks")

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "qa" / "weekly-hard-tasks-registry.json"
REPORT_DIR = ROOT / "docs" / "qa"

#: Порог отбора (решение оператора, tsk-773): задание попадает в разбор, если за окно
#: минимум ДВА ученика не сдали его с первой попытки, либо по нему была заявка помощи.
MIN_FAILED_FIRST = 2

SELECT_SIGNALS = """
WITH first_try AS (
    -- первая по времени сдача каждого ученика по каждому заданию за окно;
    -- ручные отметки преподавателя не считаем попыткой ученика
    SELECT DISTINCT ON (tr.user_id, tr.task_id)
           tr.user_id, tr.task_id, tr.is_correct AS first_ok
    FROM task_results tr
    WHERE tr.submitted_at >= now() - (%(days)s || ' days')::interval
      AND tr.source_system <> 'manual_teacher'
    ORDER BY tr.user_id, tr.task_id, tr.submitted_at
),
tries AS (
    SELECT tr.task_id,
           count(*) AS submissions,
           count(DISTINCT tr.user_id) AS students
    FROM task_results tr
    WHERE tr.submitted_at >= now() - (%(days)s || ' days')::interval
      AND tr.source_system <> 'manual_teacher'
    GROUP BY tr.task_id
),
helps AS (
    SELECT task_id, count(*) AS help_requests
    FROM help_requests
    WHERE created_at >= now() - (%(days)s || ' days')::interval
      AND task_id IS NOT NULL
    GROUP BY task_id
),
agg AS (
    SELECT f.task_id,
           count(*) FILTER (WHERE NOT f.first_ok) AS failed_first,
           count(*) AS students_first
    FROM first_try f GROUP BY f.task_id
)
SELECT t.id AS task_id, t.external_uid, t.course_id, c.title AS course_title,
       t.task_content->>'title' AS title,
       t.task_content->>'type' AS task_type,
       coalesce(a.failed_first, 0) AS failed_first,
       coalesce(a.students_first, 0) AS students,
       coalesce(tr.submissions, 0) AS submissions,
       coalesce(h.help_requests, 0) AS help_requests,
       t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon
FROM tasks t
JOIN courses c ON c.id = t.course_id
LEFT JOIN agg a ON a.task_id = t.id
LEFT JOIN tries tr ON tr.task_id = t.id
LEFT JOIN helps h ON h.task_id = t.id
WHERE t.is_active
  AND (coalesce(a.failed_first, 0) >= %(min_failed)s OR coalesce(h.help_requests, 0) > 0)
ORDER BY coalesce(a.failed_first, 0) DESC, coalesce(h.help_requests, 0) DESC, t.id
"""


def dsn(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def load_registry() -> dict[str, Any]:
    """Реестр ранее разобранных заданий."""
    if not REGISTRY.exists():
        return {}
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def save_registry(registry: dict[str, Any]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")


def fetch_signals(days: int) -> list[dict[str, Any]]:
    conn = psycopg2.connect(dsn())
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(SELECT_SIGNALS, {"days": days, "min_failed": MIN_FAILED_FIRST})
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def build_report(rows: list[dict[str, Any]], registry: dict[str, Any],
                 days: int) -> tuple[list[dict], list[dict], list[dict], str]:
    """Разделить находки на потоки и собрать текст отчёта.

    Потоков три: «повторно» (корректность уже проверяли — значит вопрос к сложности),
    «впервые» (проверяем корректность) и «вводный курс» (сигнал о ясности материала).
    """
    first_time, repeated, onboarding = [], [], []
    for row in rows:
        history = registry.get(str(row["task_id"]), {}).get("reviews", [])
        row["reviews"] = history
        if history:
            repeated.append(row)
        elif (row["external_uid"] or "").startswith("lms:onboarding:"):
            # Вводный курс — вопросы на понимание прочитанного. Ошибка с первой
            # попытки здесь часть обучения, а не признак дефекта: ученик читает,
            # отвечает, ошибается, перечитывает. Сигнал ценен, но он про ясность
            # МАТЕРИАЛА, а не про корректность задания — отсюда отдельный список.
            onboarding.append(row)
        else:
            first_time.append(row)

    lines = [
        f"# Трудные задания за {days} дн. — {date.today().isoformat()}",
        "",
        f"Отобрано {len(rows)}: впервые {len(first_time)}, повторно {len(repeated)}, "
        f"вводный курс {len(onboarding)}.",
        f"Порог: не сдали с первой попытки ≥{MIN_FAILED_FIRST} учеников либо была "
        "заявка помощи.",
        "",
    ]

    if repeated:
        lines += [
            "## Повторно — смотреть сложность и понятность",
            "",
            "Корректность этих заданий уже проверяли: раз ученики снова спотыкаются, "
            "дело не в дефекте, а в самом задании. Разбор через `/methodist`; "
            "переписывание условия и смена сложности — решение оператора.",
            "",
            "| Задание | Курс | Название | Не с 1-й | Помощь | Разбирали |",
            "|---|---|---|---|---|---|",
        ]
        for row in repeated:
            when = ", ".join(r["date"] for r in row["reviews"])
            lines.append(
                f"| {row['task_id']} `{row['external_uid'] or ''}` | {row['course_id']} "
                f"{row['course_title']} | {row['title']} | "
                f"{row['failed_first']} из {row['students']} | "
                f"{row['help_requests']} | {when} |"
            )
        lines.append("")

    if first_time:
        lines += [
            "## Впервые — проверить корректность",
            "",
            "Полнота условия (все ли данные на месте), однозначность (нет ли второго "
            "верного ответа), правильность эталона. Инструменты: "
            "`scripts/tsk772_solve_oge_batches.py` для решаемых партий ОГЭ и сверка "
            "с первоисточником. Очевидные дефекты правятся сразу, по протоколу "
            "`/db-check`.",
            "",
            "| Задание | Курс | Название | Не с 1-й | Помощь | Эталон |",
            "|---|---|---|---|---|---|",
        ]
        for row in first_time:
            lines.append(
                f"| {row['task_id']} `{row['external_uid'] or ''}` | {row['course_id']} "
                f"{row['course_title']} | {row['title']} | "
                f"{row['failed_first']} из {row['students']} | "
                f"{row['help_requests']} | `{row['etalon']}` |"
            )
        lines.append("")

    if onboarding:
        lines += [
            "## Вводный курс — сигнал о ясности материала",
            "",
            "Это вопросы на понимание прочитанного: ошибка с первой попытки здесь "
            "нормальна и дефектом задания не является. Смотреть иначе — если "
            "большинство отвечает неверно, скорее непонятен разъясняющий материал "
            "перед вопросом. Это работа `/methodist` по тексту раздела, а не проверка "
            "эталона.",
            "",
            "| Задание | Раздел | Вопрос | Не с 1-й | Помощь |",
            "|---|---|---|---|---|",
        ]
        for row in onboarding:
            lines.append(
                f"| {row['task_id']} | {row['course_id']} {row['course_title']} | "
                f"{row['title']} | {row['failed_first']} из {row['students']} | "
                f"{row['help_requests']} |"
            )
        lines.append("")

    lines += [
        "## После разбора",
        "",
        "Отметить рассмотренные, чтобы в следующий раз они попали в поток «повторно»:",
        "",
        "```bash",
        "python scripts/weekly_hard_tasks.py --record <id ...> --verdict "
        "fixed|ok|methodist --note \"что решили\"",
        "```",
        "",
    ]
    return first_time, repeated, onboarding, "\n".join(lines)


def record(task_ids: list[int], verdict: str, note: str) -> None:
    """Отметить задания как разобранные — история накапливается, а не затирается."""
    registry = load_registry()
    stamp = date.today().isoformat()
    for task_id in task_ids:
        entry = registry.setdefault(str(task_id), {"reviews": []})
        entry["reviews"].append({"date": stamp, "verdict": verdict, "note": note})
    save_registry(registry)
    logger.info("Отмечено разобранными: %s (вердикт %s)", task_ids, verdict)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="окно наблюдения в днях")
    parser.add_argument("--report", action="store_true",
                        help="сохранить отчёт в docs/qa")
    parser.add_argument("--record", nargs="+", type=int, metavar="ID",
                        help="отметить задания разобранными")
    parser.add_argument("--verdict", default="ok",
                        choices=["fixed", "ok", "methodist"],
                        help="fixed — нашли и починили дефект; ok — задание исправно; "
                             "methodist — отдано на методический пересмотр")
    parser.add_argument("--note", default="", help="короткий комментарий в реестр")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if args.record:
        record(args.record, args.verdict, args.note)
        return 0

    rows = fetch_signals(args.days)
    registry = load_registry()
    first_time, repeated, onboarding, text = build_report(rows, registry, args.days)

    logger.info("%s", text)
    if args.report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{date.today().isoformat()}-weekly-hard-tasks.md"
        path.write_text(text, encoding="utf-8")
        logger.info("Отчёт сохранён: %s", path)
    logger.info("Итого: впервые %d, повторно %d, вводный курс %d",
                len(first_time), len(repeated), len(onboarding))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
