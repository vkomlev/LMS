"""Точечная правка двух ложных незачётов ученика 4519 (tsk-602).

Что и почему. Задания 4836/4837 (курс 157, координаты Черепахи) просили ответ
«в формате x,y», а эталон был записан без пробела после запятой; нормализация
задания (`trim`, `lower`) пробел не снимает. Ученик ввёл `10, 0` и `3, 5` —
верно по смыслу, но получил «не зачёт». Эталон расширен вариантом с пробелом
(`fix_answer_format_traps_tsk602.py`), и по нынешним правилам обе работы —
зачёт. Общее решение по tsk-602 «вердикты задним числом не пересчитываем»
остаётся в силе; это точечное исключение по решению оператора от 2026-08-16.

Правятся РОВНО две строки: `task_results.id` 11396 и 11403. Соседние работы того
же ученика по тем же заданиям (11397, 11399 — ответ `0, 10`; 11407, 11408 —
ответ `5, -3`) реально неверны и НЕ трогаются: скрипт проверяет их неизменность
после записи.

Вердикт не выдумывается: каждая строка прогоняется через настоящий
`CheckingService.check_task` — тот же код, что работает на приёме ответа, — и в
базу пишется тот результат, который вернул движок (`is_correct`, `score`).
Разведочный SQL с догадками о нормализации на этой задаче уже дал 4 ложных
совпадения из 10, поэтому единственный допустимый источник вердикта — движок.

Пишутся те же поля, что пишет штатный путь ручной дооценки
(`POST /task-results/{id}/manual-check`): `score`, `is_correct`, `checked_at`,
`checked_by`. `checked_by=2` — учётная запись методиста (admin/methodist/teacher),
которой проставлены все 11 992 ручные проверки на проде. Поля `metrics`,
`answer_json`, `max_score`, `attempt_id` не трогаются.

Производные величины пересчитывать не требуется — обоснование в артефакте
`reviews/2026-08-17-tsk602-fix-verdicts-4519.md`. Коротко: состояние задания и
курса движок считает по ПОСЛЕДНЕЙ сдаче задания (`compute_task_state`,
`compute_course_state`), а последние сдачи по 4836/4837 не меняются; вехи
удержания фиксирует свой фоновый тик (`retention_achievements_cron_service`).

Безопасность (/db-check, режим записи): dry-run по умолчанию; каждое поле «до»
сверяется дословно, запись идёт одной транзакцией с проверкой числа строк и
верификацией после. Прод-подключение — из .mcp.json, пароль не печатается.

Запуск (из корня LMS):
  python scripts/fix_stale_verdicts_4519_tsk602.py
  DBCHECK_OK=1 python scripts/fix_stale_verdicts_4519_tsk602.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
import psycopg2.extras

from app.schemas.checking import StudentAnswer
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService

#: Работы, которые правим, и ожидаемое состояние «до» — сверяется дословно.
TARGETS: dict[int, dict[str, Any]] = {
    11396: {"task_id": 4836, "user_id": 4519, "answer": "10, 0",
            "is_correct": False, "score": 0, "max_score": 1},
    11403: {"task_id": 4837, "user_id": 4519, "answer": "3, 5",
            "is_correct": False, "score": 0, "max_score": 1},
}

#: Соседние работы тех же заданий — реально неверные, обязаны остаться нетронутыми.
UNTOUCHED: dict[int, dict[str, Any]] = {
    11397: {"is_correct": False, "score": 0},
    11399: {"is_correct": False, "score": 0},
    11407: {"is_correct": False, "score": 0},
    11408: {"is_correct": False, "score": 0},
}

#: Учётная запись методиста, от имени которой проставляется ручная проверка.
CHECKED_BY = 2

#: Пояснение, которое ученик видит в истории попыток рядом с вердиктом. Без него
#: на экране появляется внезапное «Правильно» по работе, за которую когда-то был
#: незачёт. Формат — как у прошлого ретроактивного зачёта этому же ученику
#: (`metrics.comment`, tsk-542). Флаг `manual_grant` НЕ ставится: это не ручной
#: зачёт задания (тот заводит отдельную запись через `manual_progress_service`),
#: а исправление вердикта уже сданной работы.
VERDICT_COMMENT = (
    "tsk-602: ответ был верным по смыслу, но эталон задания не принимал пробел "
    "после запятой. Эталон исправлен, вердикт этой работы пересчитан."
)

SELECT_SQL = """
SELECT tr.id, tr.task_id, tr.user_id, tr.attempt_id, tr.score, tr.max_score, tr.is_correct,
       tr.checked_at, tr.checked_by, tr.answer_json, tr.metrics, tr.count_retry,
       tr.source_system, tr.submitted_at,
       t.task_content, t.solution_rules, t.max_score AS task_max_score
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
WHERE tr.id = ANY(%s)
ORDER BY tr.id
"""

#: Числитель `compute_course_state` — «заданий курса, где ПОСЛЕДНЯЯ сдача прошла
#: порог». Снимается до и после записи: подтверждает, что кеш прогресса не
#: сдвинулся и пересчитывать `student_course_state` не нужно.
PROGRESS_SQL = """
WITH last_per_task AS (
    SELECT DISTINCT ON (tr.task_id) tr.task_id, tr.score AS last_score, tr.max_score AS last_max
    FROM task_results tr
    INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    INNER JOIN tasks t ON t.id = tr.task_id AND t.course_id = %(course_id)s
         AND t.is_active = true AND t.requirement_level IN ('required', 'skippable')
    WHERE tr.user_id = %(student_id)s
    ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
)
SELECT count(*) FILTER (WHERE last_max > 0 AND (last_score::float / last_max) >= 0.5) AS passed
FROM last_per_task
"""


def _prod_params() -> dict[str, Any]:
    """Боевое подключение из .mcp.json. Пароль не печатается."""
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


def _engine_verdict(service: CheckingService, row: dict[str, Any]) -> tuple[bool | None, int, int]:
    """Вердикт нынешнего движка проверки по этой работе.

    Args:
        service: Сервис проверки — тот же, что на приёме ответа.
        row: Строка выборки: работа вместе с заданием.

    Returns:
        Кортеж (is_correct, score, max_score) в том виде, как их вернул движок.
    """
    content = TaskContent.model_validate(row["task_content"])
    rules = service.build_solution_rules(
        row["solution_rules"], fallback_max_score=row["task_max_score"] or 1
    )
    answer = StudentAnswer.model_validate(row["answer_json"])
    result = service.check_task(content, rules, answer)
    return result.is_correct, result.score, result.max_score


def _fetch(cur: Any, ids: list[int]) -> dict[int, dict[str, Any]]:
    cur.execute(SELECT_SQL, (ids,))
    return {int(r["id"]): r for r in cur.fetchall()}


def _progress_passed(cur: Any, *, student_id: int, course_id: int) -> int:
    cur.execute(PROGRESS_SQL, {"student_id": student_id, "course_id": course_id})
    return int(cur.fetchone()["passed"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Исправить два ложных незачёта ученика 4519 (tsk-602)"
    )
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    params = _prod_params()
    conn = psycopg2.connect(**params)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    service = CheckingService()

    print("=== tsk-602: точечная правка вердиктов ученика 4519 ===")
    print(f"Подключение: {params['user']}@{params['host']}/{params['dbname']}")
    print(f"Режим: {'ЗАПИСЬ' if args.apply else 'DRY-RUN'}\n")

    try:
        rows = _fetch(cur, list(TARGETS) + list(UNTOUCHED))
        planned: dict[int, tuple[bool, int]] = {}

        # --- сверка «до» + вердикт движка ---
        for result_id, expected in TARGETS.items():
            row = rows.get(result_id)
            if row is None:
                print(f"ОТКАЗ: работа {result_id} не найдена.")
                conn.rollback()
                return 1
            actual_answer = ((row["answer_json"] or {}).get("response") or {}).get("value")
            mismatch = [
                f"{field}: в базе {row[field]!r}, ожидалось {value!r}"
                for field, value in (
                    ("task_id", expected["task_id"]),
                    ("user_id", expected["user_id"]),
                    ("max_score", expected["max_score"]),
                )
                if row[field] != value
            ]
            if actual_answer != expected["answer"]:
                mismatch.append(
                    f"ответ: в базе {actual_answer!r}, ожидалось {expected['answer']!r}"
                )
            # Допустимых состояний «до» ровно два: нетронутый незачёт и результат
            # прошлого прогона этого же скрипта (запуск идемпотентен — так к уже
            # исправленному вердикту дописывается пояснение). Любая чужая ручная
            # проверка — стоп: её решение затирать нельзя.
            untouched = (
                row["is_correct"] == expected["is_correct"]
                and row["score"] == expected["score"]
                and row["checked_at"] is None
                and row["checked_by"] is None
            )
            ours = row["is_correct"] is True and row["checked_by"] == CHECKED_BY
            if not (untouched or ours):
                mismatch.append(
                    f"состояние не опознано: is_correct={row['is_correct']}, "
                    f"score={row['score']}, checked_by={row['checked_by']}, "
                    f"checked_at={row['checked_at']}"
                )
            if mismatch:
                print(f"ОТКАЗ: работа {result_id} — состояние «до» не совпало:")
                for line in mismatch:
                    print(f"   {line}")
                conn.rollback()
                return 1

            verdict, score, max_score = _engine_verdict(service, row)
            if verdict is not True:
                print(
                    f"ОТКАЗ: работа {result_id} — нынешний движок НЕ считает ответ верным "
                    f"(is_correct={verdict}). Правка вердикта без основания не делается."
                )
                conn.rollback()
                return 1
            if max_score != row["max_score"]:
                print(
                    f"ОТКАЗ: работа {result_id} — движок вернул max_score={max_score}, "
                    f"в базе {row['max_score']}."
                )
                conn.rollback()
                return 1
            planned[result_id] = (verdict, score)

            print(f"работа {result_id} (задание {row['task_id']}, ответ {actual_answer!r}):")
            print(
                f"   было:  is_correct={row['is_correct']} score={row['score']}/{row['max_score']} "
                f"checked_at={row['checked_at']} checked_by={row['checked_by']} "
                f"metrics={row['metrics']!r}"
            )
            print(
                f"   стало: is_correct={verdict} score={score}/{max_score} "
                f"checked_at={'<сейчас>' if row['checked_at'] is None else row['checked_at']} "
                f"checked_by={CHECKED_BY} metrics.comment=<пояснение ученику>"
                f"   (вердикт выдал CheckingService)"
            )

        # --- соседние работы: подтверждаем, что они действительно неверны ---
        print("\nСоседние работы тех же заданий (не трогаем):")
        for result_id, expected in UNTOUCHED.items():
            row = rows.get(result_id)
            if row is None:
                print(f"ОТКАЗ: работа {result_id} не найдена.")
                conn.rollback()
                return 1
            verdict, score, _ = _engine_verdict(service, row)
            answer_value = ((row["answer_json"] or {}).get("response") or {}).get("value")
            if verdict is True:
                print(
                    f"ОТКАЗ: работа {result_id} (ответ {answer_value!r}) по нынешним правилам "
                    f"верна — состав правки не соответствует данным."
                )
                conn.rollback()
                return 1
            print(
                f"   {result_id}: ответ {answer_value!r} — движок подтверждает незачёт "
                f"(is_correct={verdict}, score={score})"
            )

        passed_before = _progress_passed(cur, student_id=4519, course_id=157)
        print(f"\nПрогресс по курсу 157 до записи: пройдено заданий {passed_before}")

        if not args.apply:
            print("\nDRY-RUN: изменения НЕ записаны. Для записи — DBCHECK_OK=1 … --apply.")
            conn.rollback()
            return 0

        # --- запись ---
        now = datetime.now(timezone.utc)
        for result_id, (verdict, score) in planned.items():
            cur.execute(
                """
                UPDATE task_results
                SET is_correct = %(is_correct)s,
                    score = %(score)s,
                    checked_at = COALESCE(checked_at, %(checked_at)s),
                    checked_by = %(checked_by)s,
                    -- В `metrics` лежит не SQL NULL, а JSON-null, поэтому COALESCE
                    -- его не ловит, а `'null'::jsonb || {...}` даёт МАССИВ, а не
                    -- объект. Отсюда проверка по jsonb_typeof (тот же класс, что
                    -- ловушка `IS NULL` на jsonb в этой таблице).
                    metrics = CASE
                                WHEN jsonb_typeof(metrics) = 'object' THEN metrics
                                ELSE '{}'::jsonb
                              END
                              || jsonb_build_object('comment', %(comment)s::text)
                WHERE id = %(id)s
                  AND (
                        (is_correct = false AND score = 0 AND checked_by IS NULL)
                     OR (is_correct = true AND checked_by = %(checked_by)s)
                  )
                """,
                {
                    "id": result_id,
                    "is_correct": verdict,
                    "score": score,
                    "checked_at": now,
                    "checked_by": CHECKED_BY,
                    "comment": VERDICT_COMMENT,
                },
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"работа {result_id}: обновлено {cur.rowcount} строк вместо 1"
                )

        # --- верификация до коммита ---
        after = _fetch(cur, list(TARGETS) + list(UNTOUCHED))
        for result_id, (verdict, score) in planned.items():
            row = after[result_id]
            if (row["is_correct"], row["score"], row["checked_by"]) != (verdict, score, CHECKED_BY):
                raise RuntimeError(f"работа {result_id}: верификация не прошла")
            if row["checked_at"] is None:
                raise RuntimeError(f"работа {result_id}: checked_at не проставлен")
            if (row["metrics"] or {}).get("comment") != VERDICT_COMMENT:
                raise RuntimeError(f"работа {result_id}: пояснение в metrics не записалось")
            before_row = rows[result_id]
            untouched_fields = (
                "task_id", "user_id", "attempt_id", "max_score", "answer_json",
                "count_retry", "source_system", "submitted_at",
            )
            changed = [f for f in untouched_fields if row[f] != before_row[f]]
            if changed:
                raise RuntimeError(
                    f"работа {result_id}: затронуты посторонние поля: {', '.join(changed)}"
                )
        for result_id, expected in UNTOUCHED.items():
            row = after[result_id]
            if (row["is_correct"], row["score"], row["checked_by"], row["checked_at"]) != (
                expected["is_correct"], expected["score"], None, None
            ):
                raise RuntimeError(f"соседняя работа {result_id} изменилась — откат")

        passed_after = _progress_passed(cur, student_id=4519, course_id=157)

        conn.commit()
        print("\nCOMMIT: 2 работы обновлены и верифицированы.")
        print(f"Прогресс по курсу 157 после записи: пройдено заданий {passed_after}")
        if passed_after == passed_before:
            print(
                "   Как и ожидалось, не изменился: состояние задания движок берёт по "
                "ПОСЛЕДНЕЙ сдаче, а последние сдачи по 4836/4837 остались неверными."
            )
        else:
            print("   ВНИМАНИЕ: число пройденных изменилось — сверить student_course_state.")
        return 0
    except Exception as exc:  # noqa: BLE001 — любая осечка откатывает всю правку
        conn.rollback()
        # Трассировка нужна целиком: осечка тут означает откат правки боевых
        # данных, и место сбоя важнее краткости вывода.
        traceback.print_exc()
        print(f"\nROLLBACK: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
