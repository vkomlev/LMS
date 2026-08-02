# -*- coding: utf-8 -*-
"""tsk-524 follow-up: зачесть подкурс «Рекурсия в Python» (course_id=1451)
ученикам, которые ЗАВЕРШИЛИ курс 88 ДО того, как в него добавили этот подкурс.

ЗАЧЕМ
`compute_course_state` (learning_engine_service) считает завершённость курса по
ВСЕМ активным required/skippable заданиям и материалам его дерева. Добавление
9 заданий + 1 материала в дерево курса 88 (tsk524_recursion_subcourse.py)
задним числом увеличило знаменатель: все 13 учеников, у кого
`student_course_state.state='COMPLETED'` по курсу 88, при следующем пересчёте
получили бы `IN_PROGRESS`. Курс 112 «ЕГЭ по информатике» требует курс 88
COMPLETED (`course_dependencies`) — и ВСЕ 13 этих учеников сейчас активно
учатся в 112 (`user_courses.is_active=true`, `student_course_state.state
IN_PROGRESS` по 112). Пересчёт курса 88 триггерится уже при обычном запросе
"следующего задания" в 112 (`resolve_next_item` вызывает `compute_course_state`
на required-курсе) — то есть у живого ученика при следующем визите.

Решение оператора (2026-08-02, через AskUserQuestion): зачесть новый подраздел
этим 13 ученикам задним числом штатным инструментом «Зачесть тему»
(`manual_progress_service.grant_course_subtree`, тот же сервис, что стоит за
кнопкой в карточке ученика). Это НЕ хак: аудит-лог (`audit_service.
TEACHER_PROGRESS_GRANTED`) фиксирует происхождение, отмена доступна через
`revoke_course_subtree`. Новые/текущие (`IN_PROGRESS`) ученики курса 88 проходят
9 заданий как обычные обязательные — этот скрипт их не трогает.

ЗАПУСК ТОЛЬКО НА СЕРВЕРЕ (прод-DSN только там в `.env`; локальный `.env` —
dev-БД, см. `feedback_local_env_prod_dsn_gotcha`):
  sudo -u app bash -c 'set -a; . /opt/lms/.env; set +a; cd /opt/lms; \
    ./venv/bin/python scripts/tsk524_grandfather_recursion.py'
  sudo -u app bash -c 'set -a; . /opt/lms/.env; set +a; cd /opt/lms; \
    ./venv/bin/python scripts/tsk524_grandfather_recursion.py --apply'
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

NEW_COURSE_ID = 1451  # Рекурсия в Python
ROOT_COURSE_ID = 88   # Python для ЕГЭ
DEPENDENT_COURSE_ID = 112  # ЕГЭ по информатике (требует ROOT_COURSE_ID COMPLETED)
GRANTED_BY = 2  # teacher/admin «Виктор Комлев» — сессия, из которой принято решение
COMMENT = (
    "tsk-524: ретроактивный зачёт нового блока «Рекурсия в Python» ученикам, "
    "завершившим курс 88 до добавления этого блока — чтобы не откатывать "
    "завершённость курса 88 и не блокировать зависимый курс 112."
)


async def main(apply: bool) -> None:
    from sqlalchemy import text
    from app.db.session import async_session_factory
    from app.services import manual_progress_service
    from app.services.learning_engine_service import LearningEngineService

    engine = LearningEngineService()

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT scs88.student_id, scs112.state AS state_112, "
                    "       uc112.is_active AS enrolled_112 "
                    "FROM student_course_state scs88 "
                    "LEFT JOIN student_course_state scs112 "
                    "  ON scs112.student_id = scs88.student_id AND scs112.course_id = :dep "
                    "LEFT JOIN user_courses uc112 "
                    "  ON uc112.user_id = scs88.student_id AND uc112.course_id = :dep "
                    "WHERE scs88.course_id = :root AND scs88.state = 'COMPLETED' "
                    "ORDER BY scs88.student_id"
                ),
                {"root": ROOT_COURSE_ID, "dep": DEPENDENT_COURSE_ID},
            )
        ).fetchall()
        student_ids = [int(r[0]) for r in rows]

        print("=" * 78)
        print(f"tsk-524 · зачёт подраздела {NEW_COURSE_ID} ученикам с завершённым "
              f"курсом {ROOT_COURSE_ID} · {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)
        print(f"Учеников с course_id={ROOT_COURSE_ID} state=COMPLETED: {len(student_ids)}")
        for sid, state_112, enrolled_112 in rows:
            print(f"  student_id={sid:>5}  курс_{DEPENDENT_COURSE_ID}: "
                  f"state={state_112!r:<14} enrolled={enrolled_112}")

        if not apply:
            print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
            return

        results = []
        for sid in student_ids:
            res = await manual_progress_service.grant_course_subtree(
                db,
                student_id=sid,
                course_id=NEW_COURSE_ID,
                granted_by=GRANTED_BY,
                comment=COMMENT,
            )
            await db.commit()
            results.append((sid, res))
            print(f"  student_id={sid:>5}: заданий={res['tasks_affected']}, "
                  f"материалов={res['materials_affected']}, "
                  f"уже было={res['skipped_already']}, "
                  f"квизов пропущено={res['skipped_quiz']}")

        print("\nВерификация после зачёта:")
        ok = True
        for sid in student_ids:
            state_after = await engine.compute_course_state(
                db, sid, ROOT_COURSE_ID, update_state_table=True
            )
            await db.commit()
            mark = "OK" if state_after.state == "COMPLETED" else "!!"
            if state_after.state != "COMPLETED":
                ok = False
            print(f"  student_id={sid:>5}: курс_{ROOT_COURSE_ID}.state={state_after.state} [{mark}]")

        print("\n" + ("ВСЕ 13 ОСТАЛИСЬ COMPLETED" if ok else "ЕСТЬ РАСХОЖДЕНИЯ — проверить вручную"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="tsk-524: grandfather-зачёт подраздела «Рекурсия»")
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
