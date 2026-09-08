"""tsk-800: зачесть Глебу Анфалову (4512) задания 3472 и 4067 — решение оператора 08.09.

Почему. Условия обоих заданий до правки (`tsk800_fix_stems.py`) допускали больше
одного прочтения, и по каждому из них ученик отвечал ВЕРНО:

* **3472** — попытка 29.08 `17 / 16 / 8` полностью верна по прочтению «Ваня выигрывает
  при любом ходе Пети» (решатель даёт для него ровно 16);
* **4067** — попытка 29.08 `39 / 34 / 38 / 33` полностью верна по прочтению «неудачный
  ход = Петя упустил победу» (решатель даёт ровно 39). Это прочтение не выдумано
  учеником: в задании 4033 курса 1397 оно записано прямо определением.

Всего он потерял на этих двух заданиях семь попыток и не сдал ни одно, считая при
этом правильно. Больше никого правка не затрагивает: по обоим заданиям это
единственный ученик с попытками (проверено по `task_results` вместе с ролями).

Как. Штатный путь `manual_progress_service.grant_task` (tsk-297), а НЕ переписывание
вердикта существующей работы:

* история реальных попыток остаётся нетронутой и видимой — важно, потому что она и
  есть улика двусмысленности;
* дооценка одной работы задание бы не открыла: движок считает состояние по ПОСЛЕДНЕЙ
  сдаче, а на 3472 верная по прочтению попытка первая из пяти, последняя (`17/16/11`)
  неверна при любом чтении;
* синтетическая попытка пишется с ``root_course_id = NULL`` — лимит попыток ученика
  не расходуется, и зачёт обратим (`revoke_task`).

Запуск (из корня LMS)::

    python scripts/tsk800_grant_anfalov.py                       # dry-run, ROLLBACK
    DBCHECK_OK=1 python scripts/tsk800_grant_anfalov.py --apply   # COMMIT
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]

STUDENT_ID = 4512  # Глеб Анфалов
GRANTED_BY = 2  # Виктор Комлев (teacher/admin) — учётка оператора
TASK_IDS = [3472, 4067]
COMMENT = (
    "tsk-800: условие допускало второе прочтение, ученик ответил верно по нему "
    "(3472 — 17/16/8, 4067 — 39/34/38/33). Решение оператора 08.09."
)


def load_prod_dsn_asyncpg_style() -> str:
    """DSN роли lms_prod из .mcp.json, в формате postgresql+asyncpg:// для SQLAlchemy."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


def load_local_env() -> None:
    """Подставить переменные из локального .env — их требует `app.core.config`.

    Значения нужны только чтобы импорт сервисного слоя не упал (`VALID_API_KEYS`,
    пути загрузок). `DATABASE_URL` выставляется ПОСЛЕ и явно — на прод, иначе сюда
    подставился бы dev-адрес из того же файла.
    """
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main(apply: bool) -> int:
    load_local_env()
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.services import manual_progress_service
    from app.services.learning_engine_service import LearningEngineService

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-800: зачёт ученику {STUDENT_ID} по заданиям {TASK_IDS} — {mode} ===\n")

    engine_db = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine_db, class_=AsyncSession, expire_on_commit=False)
    lengine = LearningEngineService()

    try:
        async with session_factory() as db:
            # --- состояние ДО ---
            for tid in TASK_IDS:
                state = await lengine.compute_task_state(db, STUDENT_ID, tid)
                rows = (await db.execute(
                    text(
                        "SELECT count(*) AS n, count(*) FILTER (WHERE score > 0) AS passes "
                        "FROM task_results WHERE user_id = :u AND task_id = :t"
                    ),
                    {"u": STUDENT_ID, "t": tid},
                )).mappings().first()
                print(f"ДО  задание {tid}: состояние={state.state}, работ={rows['n']}, зачётов={rows['passes']}")
            print()

            for tid in TASK_IDS:
                result = await manual_progress_service.grant_task(
                    db,
                    student_id=STUDENT_ID,
                    task_id=tid,
                    granted_by=GRANTED_BY,
                    comment=COMMENT,
                )
                print(
                    f"задание {tid}: granted={result['granted']} already={result['already']} "
                    f"attempt_id={result['attempt_id']}"
                )

            # --- состояние ПОСЛЕ (внутри той же транзакции) ---
            print()
            for tid in TASK_IDS:
                state = await lengine.compute_task_state(db, STUDENT_ID, tid)
                if state.state != "PASSED":
                    raise AssertionError(f"задание {tid}: ожидалось PASSED, получено {state.state}")
                print(f"ПОСЛЕ задание {tid}: состояние={state.state}")

            # Реальные попытки ученика не должны быть тронуты.
            untouched = (await db.execute(
                text(
                    "SELECT count(*) AS n FROM task_results "
                    "WHERE user_id = :u AND task_id = ANY(:t) AND source_system = 'spw_web' "
                    "AND score = 0"
                ),
                {"u": STUDENT_ID, "t": TASK_IDS},
            )).mappings().first()
            if untouched["n"] != 7:
                raise AssertionError(
                    f"ожидал 7 нетронутых реальных работ ученика, вижу {untouched['n']}"
                )
            print(f"\nРеальные попытки ученика не тронуты: {untouched['n']} строк spw_web со score=0. OK")

            if apply:
                await db.commit()
                print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
            else:
                await db.rollback()
                print("\nDRY-RUN: откатил. Запусти с --apply при DBCHECK_OK=1.")
    finally:
        await engine_db.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(args.apply)))
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
