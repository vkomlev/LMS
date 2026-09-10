"""tsk-884: вернуть в условие задания 4809 потерянную деталь (эталон 14).

Задание 4809 «Число допустимых кодов для буквы Ю» (курс 155,
`external_uid` = `lms:tsk109:c155:10`) — наш пересказ экзаменационной задачи.
При сокращении из условия ушло ограничение «закодирован ВЕСЬ алфавит, известны
лишь некоторые кодовые слова»: в пересказе Ю подана как НОВАЯ, десятая буква к
девяти занятым. По такому тексту верно 15, а эталон стоит 14 — трое учеников
независимо дали 15 и получили незачёт.

Решение оператора (10.09) — вариант А: переписать условие под эталон 14,
формулировку взять экзаменационную, как в первоисточнике (задание 3792,
`wp_nav:4:f6c96838`, курс 1382, тот же набор кодов, тот же эталон 14).

Проверено счётом (`scratchpad/solve.py`): свободная ветвь ровно одна — `010`;
она с потомками до шести бит даёт 15 кодов. Если отдать Ю код `010`, свободных
ветвей не остаётся вовсе и остальным 23 буквам места нет, поэтому `010`
недопустим и остаётся 14.

Протокол /db-check (режим записи):
  * dry-run по умолчанию — печатает текущее и новое условие;
  * перед записью текущий `stem` сверяется ДОСЛОВНО с тем, что видел автор
    правки: разошёлся (правил кто-то ещё) — выход без записи;
  * `course_id` и `order_position` не трогаем, поэтому триггер
    `trg_set_task_order_position` соседей не сдвигает; `updated_at` проставит
    `trg_task_set_updated_at`, запись содержимого попадёт в журнал аудита
    (`trg_task_audit_update`) — это ожидаемо;
  * всё в одной транзакции, после записи — верификация выборкой, затем COMMIT.

Запуск (из корня LMS):
  python scripts/fix_task4809_stem_tsk884.py
  DBCHECK_OK=1 python scripts/fix_task4809_stem_tsk884.py --apply
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
logger = logging.getLogger("tsk884.fix4809")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_ID = 4809
EXPECTED_UID = "lms:tsk109:c155:10"

# Условие, которое лежит в базе сейчас (сверяется дословно перед записью).
OLD_STEM = (
    "<p>Все заглавные буквы закодированы неравномерным двоичным кодом "
    "(условие Фано).<br>Уже заняты коды: И&nbsp;=&nbsp;110, Н&nbsp;=&nbsp;011, "
    "Ф&nbsp;=&nbsp;00, О&nbsp;=&nbsp;1111, Р&nbsp;=&nbsp;11100, "
    "М&nbsp;=&nbsp;11101, А&nbsp;=&nbsp;1001, Т&nbsp;=&nbsp;101, "
    "К&nbsp;=&nbsp;1000.<br>Сколько существует допустимых кодов длиной "
    "≤&nbsp;6 бит для новой буквы <strong>Ю</strong>, чтобы условие Фано "
    "сохранялось? Введите целое число.</p>"
)
OLD_TITLE = "Число допустимых кодов для буквы Ю"

# Новое условие — экзаменационная формулировка первоисточника 3792.
NEW_STEM = (
    "<p>Все заглавные буквы русского алфавита закодированы неравномерным "
    "двоичным кодом, в котором никакое кодовое слово не является началом "
    "другого кодового слова (условие Фано). Это условие обеспечивает "
    "возможность однозначной расшифровки закодированных сообщений."
    "<br>Кодовые слова для некоторых букв известны: И&nbsp;— 110, "
    "Н&nbsp;— 011, Ф&nbsp;— 00, О&nbsp;— 1111, Р&nbsp;— 11100, "
    "М&nbsp;— 11101, А&nbsp;— 1001, Т&nbsp;— 101, К&nbsp;— 1000."
    "<br>Сколько существует способов назначить для буквы <strong>Ю</strong> "
    "кодовое слово, длина которого не превышает шести двоичных знаков? "
    "Введите целое число.</p>"
)
NEW_TITLE = "Способы назначить код букве Ю"


def prod_dsn() -> Dict[str, Any]:
    """Параметры подключения к боевой базе из `.mcp.json` (пароль не печатаем)."""
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
    parser = argparse.ArgumentParser(description="Переписать условие задания 4809 (tsk-884)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, course_id, external_uid, order_position, "
                "task_content->>'stem' AS stem, task_content->>'title' AS title, "
                "solution_rules->'short_answer'->'accepted_answers' AS accepted "
                "FROM tasks WHERE id = %s",
                (TASK_ID,),
            )
            row = cur.fetchone()

            if row is None:
                logger.error("Задание %s не найдено", TASK_ID)
                return 1
            if row["external_uid"] != EXPECTED_UID:
                logger.error(
                    "Не тот идентификатор: ожидали %s, в базе %s", EXPECTED_UID, row["external_uid"]
                )
                return 1
            if row["stem"] != OLD_STEM or row["title"] != OLD_TITLE:
                logger.error(
                    "Условие в базе разошлось с ожидаемым — правил кто-то ещё, запись отменена.\n"
                    "В базе сейчас:\n%s\n%s",
                    row["title"],
                    row["stem"],
                )
                return 1

            logger.info("Задание %s (курс %s, %s)", row["id"], row["course_id"], row["external_uid"])
            logger.info("Эталон остаётся прежним: %s", json.dumps(row["accepted"], ensure_ascii=False))
            logger.info("\n--- было ---\n%s\n%s", row["title"], row["stem"])
            logger.info("\n--- станет ---\n%s\n%s", NEW_TITLE, NEW_STEM)

            if not args.apply:
                logger.info("\nDry-run: ничего не записано. Запуск с --apply выполнит правку.")
                return 0

            cur.execute(
                "UPDATE tasks SET task_content = jsonb_set("
                "  jsonb_set(task_content, '{stem}', to_jsonb(%s::text), true),"
                "  '{title}', to_jsonb(%s::text), true"
                ") WHERE id = %s",
                (NEW_STEM, NEW_TITLE, TASK_ID),
            )
            logger.info("Затронуто строк: %s", cur.rowcount)

            cur.execute(
                "SELECT task_content->>'stem' AS stem, task_content->>'title' AS title, "
                "order_position, updated_at, "
                "solution_rules->'short_answer'->'accepted_answers' AS accepted "
                "FROM tasks WHERE id = %s",
                (TASK_ID,),
            )
            after = cur.fetchone()
            ok = (
                after["stem"] == NEW_STEM
                and after["title"] == NEW_TITLE
                and after["order_position"] == row["order_position"]
                and after["accepted"] == row["accepted"]
            )
            if not ok:
                conn.rollback()
                logger.error("Верификация не прошла — откат.")
                return 1

            conn.commit()
            logger.info("Записано и проверено. updated_at = %s", after["updated_at"])
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
