"""tsk-597 / tsk-598: погасить ложные эскалации проверки у методиста.

Два уведомления с ОДНИМ дефектом: и таймаут-эскалация
(`escalation_service`, kind `review_escalated`), и эскалация завершения курса
(`learning_engine_service`, kind `course_pending_review`) отбирали работы по
ТИПУ задания, а очередь проверки — по `manual_review_required`. Оси
разъехались, и методиста звали к работам, которых в его очереди нет и быть не
может: `SA_COM`/`TBL_COM` с `manual_review_required=false` проверяет автомат,
`checked_at` у них не проставляется никогда.

Замер на проде 2026-08-08: у таймаут-эскалации 502 кандидата из 502 ложные
(72 уведомления), у завершения курса — 824 «pending», настоящая из них ОДНА
(26 уведомлений).

Оба предиката исправлены (`escalation_service.py`, `learning_engine_service.py`)
— этот скрипт убирает то, что они успели создать до починки.

**Запускать ТОЛЬКО после выката исправлений.** Иначе очередь наполнится заново
в ближайший тик крона (5 минут).

Условие удаления сознательно НЕ «все эскалации», а «ни одна работа не требует
обязательной ручной проверки» — тот же предикат, что у очереди. Если между
чтением и удалением появится НАСТОЯЩАЯ эскалация, она уцелеет.

Перед удалением строки выгружаются в JSON — удаление обратимо.

Запуск на сервере (под app, не под root — tsk-394):
    sudo -u app bash -lc "cd /opt/lms && venv/bin/python scripts/tsk597_clear_false_escalations.py --dry-run"
    sudo -u app bash -lc "cd /opt/lms && DBCHECK_OK=1 venv/bin/python scripts/tsk597_clear_false_escalations.py --apply"

`.env` и корень проекта скрипт подхватывает сам — переменные окружения снаружи
задавать не нужно.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
# `.env` читается ДО импорта app.*: `app.db.session` собирает Settings на
# импорте и падает без DATABASE_URL. `utf-8-sig` — файл на сервере может быть
# с BOM. Передавать DSN через переменную в командной строке нельзя: аргументы
# `sudo` попадают в auth.log и в вывод `ps`.
load_dotenv(_ROOT / ".env", encoding="utf-8-sig")
sys.path.insert(0, str(_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("tsk597")

# «Работа требует обязательной ручной проверки» — то же, по чему живёт очередь
# преподавателя (`teacher_queue_service.mandatory_review_sql`). Держим здесь
# копией осознанно: скрипт разовый и обязан отработать на той форме предиката,
# которая действовала в момент разбора, даже если сам предикат позже изменят.
_IS_MANDATORY = """
    t.task_content->>'type' = 'TA'
    OR (t.task_content->>'type' IN ('SA','SA_COM','TBL_COM')
        AND COALESCE(
            (t.solution_rules->>'manual_review_required')::boolean, false
        ) IS TRUE)
"""

# Два вида уведомлений с ОДНИМ дефектом оси, поэтому и гасятся вместе:
#   `review_escalated`      — таймаут-эскалация (tsk-597), одна работа в
#                             `payload.result_id`;
#   `course_pending_review` — эскалация завершения курса (tsk-598), СПИСОК
#                             работ в `payload.pending_result_ids`.
# Второе уведомление считается ложным, только если ложны ВСЕ работы списка:
# если среди них есть хоть одна, реально ждущая преподавателя, методиста
# позвали по делу и трогать уведомление нельзя.
_FALSE_ESCALATION_WHERE = (
    """

    (
        n.kind = 'review_escalated'
        AND EXISTS (
            SELECT 1
            FROM task_results tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.id = (n.payload->>'result_id')::int
              AND NOT (__MANDATORY__)
        )
    )
    OR (
        n.kind = 'course_pending_review'
        AND jsonb_typeof(n.payload->'pending_result_ids') = 'array'
        AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(n.payload->'pending_result_ids') AS x(rid)
            JOIN task_results tr ON tr.id = x.rid::int
            JOIN tasks t ON t.id = tr.task_id
            WHERE __MANDATORY__
        )
    )
"""
    # Плейсхолдер, а не f-строка: bandit помечает динамическую сборку SQL
    # (B608), а `# nosec` на многострочном литерале некуда поставить, не
    # уронив комментарий ВНУТРЬ самого SQL. Подстановка одной константы
    # модуля — не ввод, поэтому обходимся заменой без форматирования.
    .replace("__MANDATORY__", _IS_MANDATORY)
)


async def main(apply: bool) -> int:
    # Имя с меткой времени, а не фиксированное. Первый прогон (tsk-597, 72
    # строки `review_escalated`) писал в постоянный файл, и второй прогон
    # (tsk-598, 26 строк `course_pending_review`) его затёр — обратимость
    # первого удаления пропала молча, а именно ради неё бэкап и делается.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = (
        Path(__file__).resolve().parent.parent
        / "reviews"
        / f"tsk597-false-escalations-backup-{stamp}.json"
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
            "SELECT COUNT(*) FROM notifications "
            "WHERE kind IN ('review_escalated','course_pending_review')"
        ))).scalar_one()

        logger.info(
            "эскалаций проверки всего: %s, из них ложных: %s",
            total, len(rows),
        )
        by_kind: dict[str, int] = {}
        for r in rows:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        for kind, n in sorted(by_kind.items()):
            logger.info("  %s: %s", kind, n)
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
                    "task": "tsk-597 + tsk-598",
                    "taken_at": datetime.now(timezone.utc).isoformat(),
                    "reason": (
                        "ложные эскалации проверки: ни одна работа не требует "
                        "обязательной ручной проверки"
                    ),
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
                    "  пример: id=%s вид=%s методист=%s работа(ы)=%s",
                    r["id"], r["kind"], r["user_id"],
                    r["payload"].get("result_id")
                    or r["payload"].get("pending_result_ids"),
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
            "SELECT COUNT(*) FROM notifications "
            "WHERE kind IN ('review_escalated','course_pending_review')"
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
            "удалено %s ложных уведомлений; эскалаций проверки осталось %s "
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
