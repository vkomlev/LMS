"""tsk-1202: убрать из условия 10369 (курс 109 «Списки») подсказку про `set`.

Множества в Python-цепочке идут позже списков (курс 105) — решение оператора 03.10:
задание оставить в «Списках», подсказку заменить на перебор с проверкой `in`.
Ответ (5) не меняется. Точечный jsonb_set по stem, остальное task_content не трогается.

Запуск (из корня LMS):
  python scripts/tsk1202_fix_10369_hint.py
  DBCHECK_OK=1 python scripts/tsk1202_fix_10369_hint.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk1132_move_foreign_tasks import prod_dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1202.hint")

TASK_ID = 10369
OLD = "(подсказка: различные элементы удобно получить через `set`)"
NEW = ("(подсказка: переберите список и складывайте в новый список только те\n"
       "числа, которых в нём ещё нет, — проверка `in`)")


def main() -> int:
    """Заменить подсказку в одной транзакции с проверкой до и после."""
    ap = argparse.ArgumentParser(description="tsk-1202: подсказка 10369")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SELECT task_content->>'stem', course_id FROM tasks WHERE id = %s", (TASK_ID,))
        stem, course_id = cur.fetchone()
        flat = stem.replace("\n", " ")
        if course_id != 109 or OLD.replace("\n", " ") not in flat:
            logger.error("Состояние не совпадает с разведкой — останов")
            conn.rollback()
            return 1
        # в исходнике подсказка разорвана переносом строки — заменяем по нормализованному тексту
        start = flat.index(OLD)
        new_stem = stem[:start] + NEW + stem[start + len(OLD):]
        cur.execute(
            "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb(%s::text)) "
            "WHERE id = %s AND course_id = 109",
            (new_stem, TASK_ID),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"обновлено {cur.rowcount} строк")
        cur.execute("SELECT task_content->>'stem' FROM tasks WHERE id = %s", (TASK_ID,))
        logger.info("ПОСЛЕ:\n%s", cur.fetchone()[0])
        if not args.apply:
            logger.info("DRY-RUN: откат")
            conn.rollback()
            return 0
        conn.commit()
        logger.info("COMMIT")
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — откат")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
