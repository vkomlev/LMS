"""tsk-770: вернуть баллы за ответы, которые были верными, но не засчитались.

Партии ``oge:reshu`` (t2/t4/t7) синтетические: эталоны считала языковая модель и
ошибалась. Ученики отвечали ВЕРНО и получали незачёт. Эталоны уже исправлены
(``tsk770_fix_oge_*_etalons.py``, ``tsk770_rebuild_broken_tasks.py``), но вердикты
прошлых сдач движок не пересчитывает — их надо поправить отдельно.

Правится вердикт САМОЙ сдачи, а не выставляется ручной зачёт отдельной строкой.
Причина: ручной зачёт (``manual_progress_service.grant_task``) создаёт НОВУЮ строку
``task_results`` и тем самым тратит ещё одну попытку — именно так задание 6551 стало
3/3. Здесь же ошиблась проверка, а не ученик: его строка и должна стать зачётной.

Отбор строгий и вычисляемый, без списка id: берутся только те незачётные сдачи, где
ответ ученика совпадает с ТЕКУЩИМ (уже исправленным) эталоном. Ответы, неверные и
после исправления, не трогаются.

``student_course_state`` тут не пересчитывается: состояние задания считается на лету
по ``task_results``, поэтому зачёт виден ученику сразу; процент курса освежает
фоновый тик (``course_dependency_state_cron_service``) и ближайший ``resolve_next_item``.

Запуск::

    python scripts/tsk770_restore_student_verdicts.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk770_restore_student_verdicts.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk770")

#: Сдачи, где ответ совпадает с текущим эталоном, но зачёта нет.
SELECT_WRONGLY_FAILED = """
    SELECT tr.id, tr.user_id, tr.task_id, t.external_uid, tr.submitted_at,
           tr.answer_json#>>'{response,value}' AS ans,
           t.solution_rules#>>'{short_answer,accepted_answers,0,value}' AS etalon,
           tr.score, tr.max_score, tr.is_correct, tr.source_system
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE t.external_uid ~ '^oge:reshu:t(2|4|7):'
      AND tr.is_correct IS NOT TRUE
      AND upper(trim(coalesce(tr.answer_json#>>'{response,value}', ''))) =
          upper(trim(coalesce(
              t.solution_rules#>>'{short_answer,accepted_answers,0,value}', '')))
      AND coalesce(t.solution_rules#>>'{short_answer,accepted_answers,0,value}', '') <> ''
    ORDER BY tr.user_id, tr.task_id, tr.submitted_at
"""


def dsn_from_mcp(alias: str = "learn_prod_db") -> str:
    """Строка подключения из .mcp.json проекта (в код её не хардкодим)."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json")
                     .read_text(encoding="utf-8"))
    return cfg["mcpServers"][alias]["args"][-1].split("?")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только показать)")
    parser.add_argument("--backup-dir", default="reviews")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    conn = psycopg2.connect(dsn_from_mcp())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(SELECT_WRONGLY_FAILED)
    rows = cur.fetchall()

    if not rows:
        logger.info("Незачётов за верный ответ не найдено.")
        conn.close()
        return 0

    logger.info("Незачётов за верный ответ: %d", len(rows))
    by_student: dict[int, list[Any]] = {}
    for row in rows:
        by_student.setdefault(row["user_id"], []).append(row)
    for student_id, items in sorted(by_student.items()):
        logger.info("  ученик %s:", student_id)
        for row in items:
            logger.info(
                "    [%s] задание %s (%s), %s, ответ %r = эталон %r, было %s/%s",
                row["id"], row["task_id"], row["external_uid"],
                row["submitted_at"].strftime("%d.%m %H:%M"), row["ans"],
                row["etalon"], row["score"], row["max_score"],
            )

    if not args.apply:
        logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = Path(args.backup_dir) / f"{stamp}-tsk770-verdicts-backup.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(
        json.dumps([{k: (v.isoformat() if hasattr(v, "isoformat") else v)
                     for k, v in dict(row).items()} for row in rows],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Снимок сдач до правки: %s", backup_path)

    ids = [row["id"] for row in rows]
    try:
        for row in rows:
            # max_score у этих заданий равен 1; берём его же, а не константу.
            cur.execute(
                """
                UPDATE task_results
                SET is_correct = TRUE,
                    score = max_score,
                    checked_at = now()
                WHERE id = %s
                """,
                (row["id"],),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"[{row['id']}] UPDATE затронул {cur.rowcount} строк")

        # Верификация поштучно и из базы.
        cur.execute(
            "SELECT id, is_correct, score, max_score FROM task_results "
            "WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        for row in cur.fetchall():
            if not row["is_correct"] or row["score"] != row["max_score"]:
                raise RuntimeError(
                    f"[{row['id']}] после UPDATE is_correct={row['is_correct']}, "
                    f"score={row['score']} из {row['max_score']}"
                )
        conn.commit()
        logger.info("Зачтено и проверено: %d сдач", len(rows))
    except Exception:
        conn.rollback()
        logger.exception("Откат транзакции — изменения не применены")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
