# -*- coding: utf-8 -*-
"""tsk-788: принимать у задания 2310 слитную запись ответа — так его печатает источник.

Зачем. Ответ здесь — ДВА числа (задание 1 и задание 2 из условия), в LMS эталон записан
через пробел: ``1150 2652``. Оба числа подтверждены независимым пересчётом по
приложенной таблице (``tsk788_recompute_robot_energy_2310.py``: максимум из дешевейших
путей до каждой финальной клетки — 1150, самый дорогой маршрут — 2652).

Но на sdamgia этот ответ напечатан СЛИТНО: ``11502652`` — без разделителя, в отличие от
остальных многочисленных ответов партии, где стоит «&». Ученик, сверяющийся с
источником, напишет слитно и получит незачёт: нормализация склеенное число с парой
чисел не сопоставит.

Почему правка узкая. Заданий, где источник печатает ответ слитно, в партии ровно два:
sdamgia 27415 (LMS 2307) и 27603 (LMS 2310). У 2307 слитная форма уже принимается —
её добавили в tsk-687 после того, как ученик так и ответил. 2310 — последнее незакрытое
задание того же класса. Остальным 12 заданиям с многочисленным эталоном слитная форма
НЕ добавляется: там источник ставит разделитель, и слитных попыток в сдачах нет —
плодить варианты без оснований значит расширять то, что принимается, наугад.

Read-only без ``--apply``.

Запуск::

    python scripts/tsk788_add_glued_answer_2310.py
    DBCHECK_OK=1 python scripts/tsk788_add_glued_answer_2310.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

logger = logging.getLogger("tsk788")

TASK_ID = 2310
#: Эталон, к которому добавляется слитная форма. Сверяется перед записью: если в базе
#: лежит другое значение, значит задание успели поправить, и правка вслепую опасна.
EXPECTED = "1150 2652"


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
    cur.execute(
        """
        SELECT id, is_active, task_content->>'title' AS title, solution_rules
        FROM tasks WHERE id = %s
        """,
        (TASK_ID,),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"задание {TASK_ID} не найдено")

    rules = row["solution_rules"] or {}
    accepted = (rules.get("short_answer") or {}).get("accepted_answers") or []
    values = [item.get("value") for item in accepted]
    logger.info("[%s] %s (активно: %s)", row["id"], row["title"], row["is_active"])
    logger.info("  принимается сейчас: %s", values)

    if EXPECTED not in values:
        raise RuntimeError(
            f"ожидали эталон {EXPECTED!r}, а в базе {values!r} — правку не применяем"
        )
    glued = EXPECTED.replace(" ", "")
    if glued in values:
        logger.info("  слитная форма %r уже принимается — делать нечего", glued)
        conn.close()
        return 0

    logger.info("  добавляем слитную форму: %r", glued)
    if not args.apply:
        logger.info("\nСухой прогон. Для записи: DBCHECK_OK=1 ... --apply")
        conn.close()
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = (Path(args.backup_dir)
                   / f"{stamp}-tsk788-2310-solution-rules-backup.json")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(
        json.dumps({"id": row["id"], "title": row["title"], "solution_rules": rules},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("  снимок solution_rules до правки: %s", backup_path)

    try:
        cur.execute(
            """
            UPDATE tasks
            SET solution_rules = jsonb_set(
                    solution_rules,
                    '{short_answer,accepted_answers}',
                    (solution_rules #> '{short_answer,accepted_answers}')
                        || jsonb_build_array(jsonb_build_object('score', %s::int,
                                                                'value', %s::text)),
                    false)
            WHERE id = %s
            """,
            (accepted[0].get("score", 1), glued, TASK_ID),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"UPDATE затронул {cur.rowcount} строк")

        cur.execute(
            """
            SELECT jsonb_agg(ae.val->>'value') AS vals
            FROM tasks t
            CROSS JOIN LATERAL jsonb_array_elements(
                     t.solution_rules #> '{short_answer,accepted_answers}') AS ae(val)
            WHERE t.id = %s
            """,
            (TASK_ID,),
        )
        actual = cur.fetchone()["vals"]
        if actual != [EXPECTED, glued]:
            raise RuntimeError(f"после UPDATE принимается {actual!r}, "
                               f"ждали {[EXPECTED, glued]!r}")
        conn.commit()
        logger.info("  записано и проверено: принимается %s", actual)
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
