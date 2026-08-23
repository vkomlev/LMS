# scripts/tsk646_cleanup_live_probe.py
"""
tsk-646: убрать следы живого прогона проверки признака ИИ-авторства.

**Что это было.** Чтобы убедиться, что признак виден преподавателю ДО кнопки
зачёта, 2026-08-23 на боевом контуре была сдана одна тестовая работа под
аккаунтом оператора (142). Прогон подтвердил цепочку целиком: приём ответа →
очередь → фоновый разбор → панель на экране проверки. После этого работа
осталась висеть в ОБЯЗАТЕЛЬНОЙ очереди преподавателя с бессмысленным ответом —
её увидел бы живой человек.

**Что удаляется.** Ровно то, что создал прогон, и ничего больше: попытка и её
результат, помеченные `source_system = 'tsk646_live'`. Метка ставилась при
создании именно ради этой уборки.

Отбор устроен так, что промахнуться нельзя: совпасть должны И метка, И номер
попытки, И владелец. Ни одно условие не убрано «для простоты» — этот скрипт
удаляет строки в боевой базе, и цена ошибки здесь несимметрична.

Зависимых строк у попытки нет (проверено до запуска: `assignment_event` и
`help_requests` по ней пусты), поэтому каскадов не требуется — но обе таблицы
проверяются заново перед удалением: пусто «вчера» не значит пусто сейчас.

Запуск (по умолчанию — предпросмотр, ничего не удаляет):
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk646_cleanup_live_probe.py
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk646_cleanup_live_probe.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Dict

from sqlalchemy import text

from app.db.session import async_session_factory

logger = logging.getLogger("tsk646.cleanup")

#: Метка, которой помечен прогон. Другие строки скрипт не видит вовсе.
PROBE_SOURCE = "tsk646_live"
#: Попытка и владелец прогона. Сверяются вместе с меткой, а не вместо неё.
PROBE_ATTEMPT_ID = 12822
PROBE_USER_ID = 142

_PREVIEW_SQL = """
    SELECT tr.id AS result_id, tr.task_id, tr.user_id, tr.checked_at,
           left(coalesce(tr.answer_json->'response'->>'text', ''), 70) AS preview
    FROM task_results tr
    WHERE tr.source_system = :src AND tr.attempt_id = :att AND tr.user_id = :usr
"""

_DEPENDENTS_SQL = """
    SELECT (SELECT count(*) FROM assignment_event WHERE attempt_id = :att) AS events,
           (SELECT count(*) FROM help_requests   WHERE attempt_id = :att) AS help
"""


async def collect() -> Dict[str, Any]:
    """Что именно будет удалено. Ничего не меняет."""
    async with async_session_factory() as db:
        rows = (await db.execute(
            text(_PREVIEW_SQL),
            {"src": PROBE_SOURCE, "att": PROBE_ATTEMPT_ID, "usr": PROBE_USER_ID},
        )).mappings().all()
        deps = (await db.execute(
            text(_DEPENDENTS_SQL), {"att": PROBE_ATTEMPT_ID}
        )).mappings().one()
        attempt = (await db.execute(
            text(
                "SELECT count(*) FROM attempts "
                " WHERE id = :att AND user_id = :usr AND source_system = :src"
            ),
            {"att": PROBE_ATTEMPT_ID, "usr": PROBE_USER_ID, "src": PROBE_SOURCE},
        )).scalar_one()
    return {"results": [dict(r) for r in rows], "attempt": attempt, "deps": dict(deps)}


async def apply() -> Dict[str, int]:
    """Удаляет строки прогона одной транзакцией."""
    async with async_session_factory() as db:
        removed_results = (await db.execute(
            text(
                "DELETE FROM task_results "
                " WHERE source_system = :src AND attempt_id = :att AND user_id = :usr"
            ),
            {"src": PROBE_SOURCE, "att": PROBE_ATTEMPT_ID, "usr": PROBE_USER_ID},
        )).rowcount or 0
        removed_attempt = (await db.execute(
            text(
                "DELETE FROM attempts "
                " WHERE id = :att AND user_id = :usr AND source_system = :src"
            ),
            {"att": PROBE_ATTEMPT_ID, "usr": PROBE_USER_ID, "src": PROBE_SOURCE},
        )).rowcount or 0
        await db.commit()
    return {"results": removed_results, "attempt": removed_attempt}


async def verify() -> Dict[str, int]:
    """Проверка ПОСЛЕ удаления: строк прогона не осталось нигде."""
    async with async_session_factory() as db:
        left_results = (await db.execute(
            text("SELECT count(*) FROM task_results WHERE source_system = :src"),
            {"src": PROBE_SOURCE},
        )).scalar_one()
        left_attempts = (await db.execute(
            text("SELECT count(*) FROM attempts WHERE source_system = :src"),
            {"src": PROBE_SOURCE},
        )).scalar_one()
    return {"results": left_results, "attempts": left_attempts}


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-646: уборка следов живого прогона")
    parser.add_argument("--apply", action="store_true", help="удалить (без флага — предпросмотр)")
    args = parser.parse_args()

    report = await collect()
    print(f"Попыток с меткой {PROBE_SOURCE!r} (id={PROBE_ATTEMPT_ID}, ученик {PROBE_USER_ID}): "
          f"{report['attempt']}")
    print(f"Результатов к удалению: {len(report['results'])}")
    for row in report["results"]:
        print(f"  result #{row['result_id']}  задание {row['task_id']}  "
              f"проверено: {row['checked_at'] or 'нет'}  {row['preview']}")
    print(f"Зависимые строки попытки: assignment_event={report['deps']['events']}, "
          f"help_requests={report['deps']['help']}")

    if report["deps"]["events"] or report["deps"]["help"]:
        # Зависимых быть не должно: прогон их не создавал. Появились — значит
        # номер попытки указывает не туда, и удалять вслепую нельзя.
        print("\nСТОП: у попытки есть зависимые строки, которых прогон не создавал. "
              "Разберитесь вручную, скрипт ничего не удалил.")
        return 1

    if not args.apply:
        print("\nЭто предпросмотр. Для удаления добавьте --apply")
        return 0

    removed = await apply()
    left = await verify()
    print(f"\nУдалено: результатов {removed['results']}, попыток {removed['attempt']}")
    print(f"Осталось строк с меткой {PROBE_SOURCE!r}: "
          f"результатов {left['results']}, попыток {left['attempts']}")
    return 0 if left["results"] == 0 and left["attempts"] == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
