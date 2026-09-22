"""tsk-1059: подчистить тестовые guest-попытки живой проверки задания 8012 (прод).

Живая проверка (tsk-1059) сдала на задание 8012 три ответа под анонимной
guest-сессией через реальный публичный эндпоинт `/api/v1/learning/guest/attempts`
(тот же путь, которым идёт настоящий посетитель демо-курса): «репутация»
(зачёт), «доверие клиентов» (зачёт, старый эталон не сломан), «деньги»
(незачёт, контроль). Строки не привязаны ни к одному реальному ученику
(`attributed_user_id IS NULL`), изолированы одной guest-сессией — подчистка
не задевает ничьи данные, только гигиена (конвенция tsk-325: тестовые
артефакты не оставлять на проде).

Протокол /db-check (режим записи): dry-run по умолчанию, сверка выборки
перед удалением, транзакция, верификация после.

Запуск (из корня LMS):
  python scripts/tsk1059_cleanup_guest_probe.py
  DBCHECK_OK=1 python scripts/tsk1059_cleanup_guest_probe.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1059.cleanup")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUEST_SESSION_ID = "a1a78004-4ae4-4932-b6ef-deca7cf4b6ed"
GUEST_ATTEMPT_IDS = [110, 111, 112]


def prod_dsn() -> Dict[str, Any]:
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-1059: удалить тестовые guest_attempt/guest_session")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            "SELECT id, task_id, is_correct, attributed_user_id FROM guest_attempt "
            "WHERE id = ANY(%s)",
            (GUEST_ATTEMPT_IDS,),
        )
        rows = cur.fetchall()
        logger.info("Найдено guest_attempt: %d", len(rows))
        for r in rows:
            logger.info("  id=%s task_id=%s is_correct=%s attributed_user_id=%s",
                        r["id"], r["task_id"], r["is_correct"], r["attributed_user_id"])
        if any(r["attributed_user_id"] is not None for r in rows):
            logger.error("Найдены строки, привязанные к реальному пользователю — останов")
            conn.rollback()
            return 1
        if len(rows) != len(GUEST_ATTEMPT_IDS):
            logger.error("Ожидалось %d строк, найдено %d — останов", len(GUEST_ATTEMPT_IDS), len(rows))
            conn.rollback()
            return 1

        if not args.apply:
            logger.info("DRY-RUN: удаление не выполнено.")
            conn.rollback()
            return 0

        cur.execute("DELETE FROM guest_attempt WHERE id = ANY(%s)", (GUEST_ATTEMPT_IDS,))
        deleted_attempts = cur.rowcount
        cur.execute("DELETE FROM guest_session WHERE id = %s", (GUEST_SESSION_ID,))
        deleted_session = cur.rowcount

        cur.execute("SELECT count(*) AS c FROM guest_attempt WHERE id = ANY(%s)", (GUEST_ATTEMPT_IDS,))
        remaining = cur.fetchone()["c"]
        logger.info("Верификация: удалено попыток %d, сессий %d, осталось строк %d",
                    deleted_attempts, deleted_session, remaining)
        if remaining != 0:
            logger.error("Верификация не прошла — откат")
            conn.rollback()
            return 1

        conn.commit()
        logger.info("COMMIT выполнен: тестовые артефакты подчищены")
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
