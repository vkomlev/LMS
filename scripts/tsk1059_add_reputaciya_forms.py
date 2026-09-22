"""tsk-1059: добавить словоформы «репутация» в эталон задания 8012 (прод).

Зачем. Задание 8012 (курс 1285, `wp:qa-manual-g1-l1#q9`) спрашивает про
составляющую цены дефекта, связанную с тем, что дефект в проде видят
посторонние люди, а не команда — по первоисточнику урока (материал 3154)
это «репутация» (пункт 4), а не «доверие клиентов» (пункт 3, про уже
пострадавших клиентов). Заголовок самой задачи — «Недооценка репутации
дефекта» — называет правильное слово, которого эталон не принимает.
Похоже, `accepted_answers` скопирован с соседнего задания 8010 под другой
пункт урока и не переписан. Разведка — tsk-1059.

Приём генерации форм — тот же, что в tsk-796 (`gen_wordforms_tsk796.py`):
pymorphy3, все формы одной леммы, дедуп по строке. Правило — не сужать:
существующие 9 форм «доверие клиентов» не трогаются, добавляются только
9 форм леммы «репутация».

Протокол /db-check (режим записи):
  * dry-run по умолчанию;
  * перед записью текущий `accepted_answers` сверяется дословно со снимком,
    сделанным при разведке — разошлось, значит эталон поменяли между
    снимком и запуском, писать вслепую нельзя;
  * запись только добавляет элементы, существующие остаются на местах и
    первыми (от этого зависит `guest_diagnostic_service._reference_answer`);
  * всё в одной транзакции, после записи — верификация выборкой, потом COMMIT.

Запуск (из корня LMS):
  python scripts/tsk1059_add_reputaciya_forms.py
  DBCHECK_OK=1 python scripts/tsk1059_add_reputaciya_forms.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1059.apply")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TASK_ID = 8012

#: Снимок `accepted_answers`, сделанный при разведке (SELECT из tasks, id=8012,
#: 2026-09-22) — сверяется с текущим состоянием перед записью, чтобы не
#: перезаписать эталон, который мог поменять методист между снимком и запуском.
BEFORE = [
    {"score": 1, "value": "доверие клиентов"},
    {"score": 1, "value": "доверия клиентов"},
    {"score": 1, "value": "доверию клиентов"},
    {"score": 1, "value": "доверием клиентов"},
    {"score": 1, "value": "доверии клиентов"},
    {"score": 1, "value": "доверий клиентов"},
    {"score": 1, "value": "довериям клиентов"},
    {"score": 1, "value": "довериями клиентов"},
    {"score": 1, "value": "довериях клиентов"},
]

#: Словоформы леммы «репутация» (pymorphy3, дедуп по строке — см. разведку
#: tsk-1059 в scratchpad сессии). Именительный/винительный мн.ч. совпадают
#: с формами ед.ч. («репутации») и уже покрыты дедупом.
ADD_FORMS = [
    "репутация",
    "репутации",
    "репутацию",
    "репутацией",
    "репутациею",
    "репутаций",
    "репутациям",
    "репутациями",
    "репутациях",
]


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


def same_accepted(current: Any, expected: List[Dict[str, Any]]) -> bool:
    """Сверка текущего `accepted_answers` со снимком разведки."""
    if not isinstance(current, list) or len(current) != len(expected):
        return False
    for got, want in zip(current, expected):
        if (got or {}).get("value") != want.get("value"):
            return False
        if (got or {}).get("score") != want.get("score"):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="tsk-1059: добавить формы «репутация» в accepted_answers задания 8012"
    )
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    dsn = prod_dsn()
    logger.info("=== tsk-1059: словоформы «репутация» в задание %s ===", TASK_ID)
    logger.info("Подключение: %s@%s/%s", dsn["user"], dsn["host"], dsn["dbname"])
    logger.info("Режим: %s", "APPLY" if args.apply else "DRY-RUN")
    logger.info("Форм к добавлению: %d — %s", len(ADD_FORMS), ", ".join(ADD_FORMS))

    conn = psycopg2.connect(**dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            "SELECT solution_rules->'short_answer'->'accepted_answers' AS accepted "
            "FROM tasks WHERE id = %s AND is_active = true",
            (TASK_ID,),
        )
        row = cur.fetchone()
        if row is None:
            logger.error("Задание %s не найдено или выключено — останов", TASK_ID)
            conn.rollback()
            return 1
        if not same_accepted(row["accepted"], BEFORE):
            logger.error(
                "Эталон изменился с момента снимка разведки — сейчас %s. "
                "Писать вслепую нельзя, останов.",
                json.dumps(row["accepted"], ensure_ascii=False),
            )
            conn.rollback()
            return 1

        score = BEFORE[0]["score"]
        new_accepted = list(BEFORE) + [{"value": form, "score": score} for form in ADD_FORMS]

        logger.info("Текущих форм: %d, после записи: %d", len(BEFORE), len(new_accepted))

        if not args.apply:
            logger.info("DRY-RUN: план проверен, запись не выполнена.")
            conn.rollback()
            return 0

        cur.execute(
            "UPDATE tasks "
            "SET solution_rules = jsonb_set("
            "    solution_rules, '{short_answer,accepted_answers}', %s::jsonb, true) "
            "WHERE id = %s",
            (json.dumps(new_accepted, ensure_ascii=False), TASK_ID),
        )

        cur.execute(
            "SELECT solution_rules->'short_answer'->'accepted_answers' AS accepted "
            "FROM tasks WHERE id = %s",
            (TASK_ID,),
        )
        verify = cur.fetchone()
        values = [a.get("value") for a in verify["accepted"]]
        logger.info("Верификация внутри транзакции: %d вариантов", len(values))
        for v in values:
            logger.info("  - %s", v)

        if len(values) != len(new_accepted) or "репутация" not in values:
            logger.error("Верификация не прошла — откат")
            conn.rollback()
            return 1

        conn.commit()
        logger.info("COMMIT выполнен: задание %s, добавлено %d форм", TASK_ID, len(ADD_FORMS))
        return 0
    except Exception:
        conn.rollback()
        logger.exception("ОШИБКА — транзакция откачена, база не изменена")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
