"""tsk-597: погасить ложные уведомления «Эскалация: проверка зависла».

Крон эскалации отбирал кандидатов по ТИПУ задания, а очередь проверки — по
`manual_review_required`. Оси разъехались, и методисту приходили уведомления по
работам, которых в его очереди нет и быть не может: `SA_COM`/`TBL_COM` с
`manual_review_required=false` проверяет автомат, `checked_at` у них не
проставляется никогда. Замер на проде 2026-08-08: 502 кандидата, из них 502
ложных; накоплено 72 уведомления (36 работ × 2 методиста).

Предикат крона исправлен в `escalation_service.py` — этот скрипт убирает то,
что он успел создать до починки.

**Запускать ТОЛЬКО после выката исправленного крона.** Иначе очередь
наполнится заново в ближайший тик (5 минут).

Условие удаления сознательно НЕ «все `review_escalated`», а «работа НЕ требует
ручной проверки» — тот же предикат, что у обязательной очереди. Если между
чтением и удалением крон успеет создать НАСТОЯЩУЮ эскалацию, она уцелеет.

Перед удалением строки выгружаются в JSON рядом со скриптом — удаление
обратимо.

Запуск на сервере (под app, не под root — tsk-394):
    sudo -u app bash -lc "cd /opt/lms && venv/bin/python scripts/tsk597_clear_false_escalations.py --dry-run"
    sudo -u app bash -lc "cd /opt/lms && venv/bin/python scripts/tsk597_clear_false_escalations.py --apply"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.db.session import async_session_factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("tsk597")

# «Работа НЕ требует обязательной ручной проверки» — отрицание того же
# предиката, по которому живёт очередь преподавателя
# (`teacher_queue_service.mandatory_review_sql`). Держим здесь копией
# осознанно: скрипт разовый и обязан отработать на той форме предиката,
# которая действовала в момент разбора, даже если сам предикат позже изменят.
_FALSE_ESCALATION_WHERE = """
    n.kind = 'review_escalated'
    AND EXISTS (
        SELECT 1
        FROM task_results tr
        JOIN tasks t ON t.id = tr.task_id
        WHERE tr.id = (n.payload->>'result_id')::int
          AND NOT (
              t.task_content->>'type' = 'TA'
              OR (t.task_content->>'type' IN ('SA','SA_COM','TBL_COM')
                  AND COALESCE(
                      (t.solution_rules->>'manual_review_required')::boolean, false
                  ) IS TRUE)
          )
    )
"""


async def main(apply: bool) -> int:
    backup_path = (
        Path(__file__).resolve().parent.parent
        / "reviews"
        / "tsk597-false-escalations-backup.json"
    )

    async with async_session_factory() as db:
        # Ниже во всех трёх запросах подставляется КОНСТАНТА модуля, а не ввод:
        # у скрипта вообще нет строковых аргументов (только --apply/--dry-run).
        rows = (await db.execute(text(f"""
            SELECT n.id, n.user_id, n.kind, n.title, n.content, n.payload,
                   n.read_at, n.modified_at
            FROM notifications n
            WHERE {_FALSE_ESCALATION_WHERE}
            ORDER BY n.id
        """))).mappings().all()  # nosec B608

        total = (await db.execute(text(
            "SELECT COUNT(*) FROM notifications WHERE kind = 'review_escalated'"
        ))).scalar_one()

        logger.info(
            "уведомлений «проверка зависла» всего: %s, из них ложных: %s",
            total, len(rows),
        )
        if len(rows) != total:
            logger.warning(
                "НЕ все уведомления ложные — %s останутся нетронутыми "
                "(это ожидаемо, если крон уже создал настоящую эскалацию)",
                total - len(rows),
            )
        if not rows:
            logger.info("удалять нечего")
            return 0

        backup = [
            {
                "id": int(r["id"]), "user_id": int(r["user_id"]),
                "kind": r["kind"], "title": r["title"], "content": r["content"],
                "payload": r["payload"],
                "read_at": r["read_at"].isoformat() if r["read_at"] else None,
                "modified_at": r["modified_at"].isoformat() if r["modified_at"] else None,
            }
            for r in rows
        ]
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(
                {
                    "task": "tsk-597",
                    "taken_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "ложные эскалации: работа не требует ручной проверки",
                    "rows": backup,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("бэкап %s строк записан: %s", len(backup), backup_path)

        if not apply:
            logger.info("сухой прогон: удаления НЕ было (нужен --apply)")
            for r in rows[:5]:
                logger.info(
                    "  пример: id=%s методист=%s работа=%s",
                    r["id"], r["user_id"], r["payload"].get("result_id"),
                )
            return 0

        # Транзакция: удаление и проверка остатка идут вместе. Проверка ВНУТРИ
        # транзакции, до commit — иначе «удалили лишнее» выяснилось бы уже
        # после того, как откатывать нечего.
        deleted = (await db.execute(text(f"""
            DELETE FROM notifications n
            WHERE {_FALSE_ESCALATION_WHERE}
            RETURNING n.id
        """))).fetchall()  # nosec B608

        left_false = (await db.execute(text(f"""
            SELECT COUNT(*) FROM notifications n WHERE {_FALSE_ESCALATION_WHERE}
        """))).scalar_one()  # nosec B608
        left_total = (await db.execute(text(
            "SELECT COUNT(*) FROM notifications WHERE kind = 'review_escalated'"
        ))).scalar_one()

        if left_false != 0 or len(deleted) != len(rows):
            await db.rollback()
            logger.error(
                "откат: удалено %s из %s, ложных осталось %s",
                len(deleted), len(rows), left_false,
            )
            return 1

        await db.commit()
        logger.info(
            "удалено %s ложных уведомлений; «проверка зависла» осталось %s "
            "(это настоящие или ноль)",
            len(deleted), left_total,
        )
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="выполнить удаление")
    p.add_argument("--dry-run", action="store_true", help="только показать (по умолчанию)")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply)))
