# -*- coding: utf-8 -*-
"""tsk-542: ручной зачёт ученикам курса 112 после двух правок контента.

ЗАЧЕМ
Две наведения порядка в курсе 112 «ЕГЭ по информатике» задним числом положили
обязательные элементы ПОЗАДИ текущей позиции части учеников:

1. **tsk-387 (02.08.2026)** — Excel-теория (курсы 164 «Основы работы в
   электронных таблицах», 19 материалов; 161 «Формулы и функции в Excel»,
   9 материалов + 3 задания) переехала из «Задание 9» (10-я тема курса 112)
   в «Задание 3» (4-я тема). Ученики, прошедшие 4-ю тему и ушедшие дальше,
   получили непройденный блок ПОЗАДИ себя.
2. **tsk-471 (30.07.2026)** — в курсе 156 «Задание 5 ЕГЭ» восстановлены
   (`is_active=false → true`) 5 вводных заданий: 3240 (5_1), 4820 (5_4),
   4821 (5_5), 3451 (5_6), 3450 (5_8). Ученики, закрывшие тему до 30.07,
   получили 5 новых обязательных заданий в уже пройденной теме.

`resolve_next_item` (learning_engine_service) при заданной позиции идёт только
ВПЕРЁД (tsk-261), но при входе БЕЗ позиции отдаёт первый незавершённый элемент
с начала дерева — то есть возвращает такого ученика назад, к теме, которую он
считал закрытой. Плюс `compute_course_state` держит тему в `IN_PROGRESS`.

РЕШЕНИЕ ОПЕРАТОРА (03.08.2026, AskUserQuestion)
* Excel-теория (161 + 164) — зачесть ВСЕМ девяти ученикам, прошедшим тему
  «Задание 3» (в т.ч. тем, кто Excel-теорию никогда не проходил: оператор
  выбрал вариант «всем, кто прошёл Задание 3»).
* Вводные задания курса 156 — зачесть шестерым, кто тему уже закрыл (40/40 по
  остальным элементам). Илья Рвачёв (4540) НЕ включён: он проходит тему прямо
  сейчас (12/45) и получит задания штатным ходом.

Инструмент — штатный `manual_progress_service.grant_course_subtree` (тот же
сервис, что за кнопкой «Зачесть тему» в карточке ученика; аудит-лог
`teacher.progress.granted`, отмена через `revoke_course_subtree`). Прецедент —
`scripts/tsk524_grandfather_recursion.py`.

Уже пройденное/зачтённое сервис пропускает (`skipped_already`), поэтому у
четверых, кому Excel-материалы зачли ещё в июле, добавятся только 3 задания
курса 161. Захара Грязнова (4500) и Полину Гребневу (4506) списки не включают —
оператор закрыл их вручную 03.08 в 06:50-06:52.

ЗАПУСК ТОЛЬКО НА СЕРВЕРЕ (прод-DSN только там в `.env`; локальный `.env` —
dev-БД, см. `feedback_local_env_prod_dsn_gotcha`):
  sudo -u app bash -c 'set -a; . /opt/lms/.env; set +a; cd /opt/lms; \
    ./venv/bin/python scripts/tsk542_grandfather_excel_and_vvod5.py'
  sudo -u app bash -c 'set -a; . /opt/lms/.env; set +a; cd /opt/lms; \
    DBCHECK_OK=1 ./venv/bin/python scripts/tsk542_grandfather_excel_and_vvod5.py --apply'
Dry-run по умолчанию.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
os.chdir(project_root)

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

ROOT_COURSE_ID = 112  # ЕГЭ по информатике

#: Excel-теория, переехавшая в «Задание 3» (tsk-387). Порядок — как в дереве.
EXCEL_COURSE_IDS = [164, 161]
#: Ученики, прошедшие тему «Задание 3» (39/39 собственных элементов курса 138).
EXCEL_STUDENT_IDS = [4504, 4507, 4511, 4512, 4519, 4520, 4526, 4540, 4543]

#: Курс «Задание 5 ЕГЭ» с восстановленными вводными заданиями (tsk-471).
VVOD5_COURSE_ID = 156
#: Ученики, закрывшие тему до восстановления (40/40 по остальным элементам).
VVOD5_STUDENT_IDS = [4507, 4511, 4512, 4519, 4520, 4526]

GRANTED_BY = 2  # teacher/admin «Виктор Комлев» — сессия, из которой принято решение

COMMENT_EXCEL = (
    "tsk-542: ретроактивный зачёт Excel-теории, переехавшей из темы «Задание 9» "
    "в тему «Задание 3» (tsk-387) — ученик прошёл «Задание 3» до переноса, блок "
    "оказался позади его текущей позиции."
)
COMMENT_VVOD5 = (
    "tsk-542: ретроактивный зачёт вводных заданий темы «Задание 5», "
    "восстановленных из is_active=false (tsk-471) — ученик закрыл тему до "
    "восстановления."
)

#: Фильтр движка: что вообще попадает в знаменатель курса.
_COUNTED = "is_active = true AND requirement_level IN ('required','skippable')"

_MISSING_TASKS_SQL = f"""
    WITH counted AS (
        SELECT id FROM tasks WHERE course_id = ANY(:course_ids) AND {_COUNTED}
    ),
    last_res AS (
        SELECT DISTINCT ON (tr.task_id) tr.task_id, tr.score, tr.max_score
        FROM task_results tr
        INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
        WHERE tr.user_id = :student_id AND tr.task_id IN (SELECT id FROM counted)
        ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
    )
    SELECT c.id
    FROM counted c
    LEFT JOIN last_res lr ON lr.task_id = c.id
    LEFT JOIN student_task_progress stp
        ON stp.task_id = c.id AND stp.student_id = :student_id AND stp.status = 'skipped'
    WHERE stp.task_id IS NULL
      AND (lr.task_id IS NULL OR lr.max_score = 0
           OR (lr.score::float / lr.max_score) < 0.5)
    ORDER BY c.id
"""

_MISSING_MATERIALS_SQL = f"""
    SELECT m.id
    FROM materials m
    LEFT JOIN student_material_progress smp
        ON smp.material_id = m.id AND smp.student_id = :student_id
       AND smp.status IN ('completed','skipped')
    WHERE m.course_id = ANY(:course_ids) AND m.{_COUNTED}
      AND smp.material_id IS NULL
    ORDER BY m.id
"""

_TREE_COUNTS_SQL = """
    WITH RECURSIVE tree AS (
        SELECT CAST(:root AS int) AS cid
        UNION
        SELECT cp.course_id FROM course_parents cp JOIN tree t ON cp.parent_course_id = t.cid
    ),
    t_total AS (
        SELECT count(*) AS n FROM tasks
        WHERE course_id IN (SELECT cid FROM tree) AND is_active = true
          AND requirement_level IN ('required','skippable')
    ),
    m_total AS (
        SELECT count(*) AS n FROM materials
        WHERE course_id IN (SELECT cid FROM tree) AND is_active = true
          AND requirement_level IN ('required','skippable')
    ),
    last_res AS (
        SELECT DISTINCT ON (tr.task_id) tr.task_id, tr.score, tr.max_score
        FROM task_results tr
        INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
        INNER JOIN tasks tk ON tk.id = tr.task_id AND tk.is_active = true
          AND tk.requirement_level IN ('required','skippable')
        WHERE tr.user_id = :student_id
          AND tk.course_id IN (SELECT cid FROM tree)
        ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
    ),
    t_done AS (
        SELECT count(*) AS n FROM (
            SELECT task_id FROM last_res WHERE max_score > 0 AND (score::float / max_score) >= 0.5
            UNION
            SELECT stp.task_id FROM student_task_progress stp
            INNER JOIN tasks tk ON tk.id = stp.task_id AND tk.is_active = true
              AND tk.requirement_level IN ('required','skippable')
            WHERE stp.student_id = :student_id AND stp.status = 'skipped'
              AND tk.course_id IN (SELECT cid FROM tree)
        ) x
    ),
    m_done AS (
        SELECT count(*) AS n FROM student_material_progress smp
        INNER JOIN materials m ON m.id = smp.material_id AND m.is_active = true
          AND m.requirement_level IN ('required','skippable')
        WHERE smp.student_id = :student_id AND smp.status IN ('completed','skipped')
          AND m.course_id IN (SELECT cid FROM tree)
    )
    SELECT (SELECT n FROM t_done) + (SELECT n FROM m_done) AS done,
           (SELECT n FROM t_total) + (SELECT n FROM m_total) AS total
"""


async def _missing(db, student_id: int, course_ids: list[int]) -> tuple[list[int], list[int]]:
    """Недостающие задания и материалы ученика по дереву указанных курсов."""
    from sqlalchemy import text

    params = {"student_id": student_id, "course_ids": course_ids}
    tasks = [int(r[0]) for r in (await db.execute(text(_MISSING_TASKS_SQL), params)).fetchall()]
    materials = [int(r[0]) for r in (await db.execute(text(_MISSING_MATERIALS_SQL), params)).fetchall()]
    return tasks, materials


async def _counts(db, student_id: int, root: int) -> tuple[int, int]:
    """done/total по дереву курса — тем же фильтром, что у compute_course_state."""
    from sqlalchemy import text

    row = (await db.execute(
        text(_TREE_COUNTS_SQL), {"student_id": student_id, "root": root}
    )).fetchone()
    return int(row[0]), int(row[1])


async def main(apply: bool) -> None:
    from app.db.session import async_session_factory
    from app.services import manual_progress_service

    plan: list[tuple[int, list[int], str, str]] = []
    for sid in EXCEL_STUDENT_IDS:
        plan.append((sid, EXCEL_COURSE_IDS, COMMENT_EXCEL, "Excel-теория (161+164)"))
    for sid in VVOD5_STUDENT_IDS:
        plan.append((sid, [VVOD5_COURSE_ID], COMMENT_VVOD5, "Вводные задания темы «Задание 5» (156)"))

    async with async_session_factory() as db:
        print("=" * 78)
        print(f"tsk-542 · ретроактивный зачёт в курсе {ROOT_COURSE_ID} · "
              f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)

        before: dict[int, tuple[int, int]] = {}
        total_tasks = total_materials = 0
        print("\nПлан (что именно недостаёт каждому ученику):")
        for sid, course_ids, _comment, label in plan:
            if sid not in before:
                before[sid] = await _counts(db, sid, ROOT_COURSE_ID)
            tasks, materials = await _missing(db, sid, course_ids)
            total_tasks += len(tasks)
            total_materials += len(materials)
            print(f"  ученик {sid:>5} · {label}")
            print(f"      заданий  {len(tasks):>3}: {tasks if tasks else '—'}")
            print(f"      материалов {len(materials):>3}: "
                  f"{materials if len(materials) <= 8 else str(materials[:8]) + f' …+{len(materials) - 8}'}")

        print(f"\nИТОГО к зачёту: заданий {total_tasks}, материалов {total_materials}, "
              f"операций {len(plan)} по {len(before)} ученикам.")
        print("\nСостояние до зачёта (курс 112, done/total):")
        for sid in sorted(before):
            done, total = before[sid]
            print(f"  ученик {sid:>5}: {done}/{total}")

        if not apply:
            print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
            return

        print("\nЗапись:")
        for sid, course_ids, comment, label in plan:
            for cid in course_ids:
                res = await manual_progress_service.grant_course_subtree(
                    db,
                    student_id=sid,
                    course_id=cid,
                    granted_by=GRANTED_BY,
                    comment=comment,
                )
                await db.commit()
                print(f"  ученик {sid:>5} · курс {cid:>4} ({label}): "
                      f"заданий={res['tasks_affected']}, материалов={res['materials_affected']}, "
                      f"уже было={res['skipped_already']}, квизов пропущено={res['skipped_quiz']}")

        print("\nВерификация после зачёта:")
        ok = True
        for sid, course_ids, _comment, label in plan:
            tasks, materials = await _missing(db, sid, course_ids)
            mark = "OK" if not tasks and not materials else "!!"
            if tasks or materials:
                ok = False
            print(f"  ученик {sid:>5} · {label}: осталось незачтённым "
                  f"заданий={len(tasks)}, материалов={len(materials)} [{mark}]")

        print("\nКурс 112 (done/total) до → после:")
        for sid in sorted(before):
            done_b, total_b = before[sid]
            done_a, total_a = await _counts(db, sid, ROOT_COURSE_ID)
            if total_a != total_b:
                ok = False
            print(f"  ученик {sid:>5}: {done_b}/{total_b} → {done_a}/{total_a}"
                  f"{'  [!! знаменатель изменился]' if total_a != total_b else ''}")

        print("\n" + ("ВСЁ ЗАЧТЕНО, РАСХОЖДЕНИЙ НЕТ" if ok else "ЕСТЬ РАСХОЖДЕНИЯ — разобрать вручную"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="tsk-542: ретроактивный зачёт Excel-теории и вводных заданий темы «Задание 5»"
    )
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
