"""tsk-1199: задание 3087 (ЕГЭ 22, `tg:ege:886`) — убрать реплику автора канала, дать подсказку.

В условие при импорте из ТГ попала реплика автора канала «Внимание. На мой взгляд,
ошибка в правильном ответе. Если я не прав, напишите в комментариях.» Эталон 7 верен
(проверено решателем: максимум одновременных — 9, процесс 105 длительностью 7 мс входит в
каждую девятку; расписание с девятью процессами на [23; 30) построено). Остальной текст
дословно совпадает с первоисточником — Яндекс (Рогов А.), он же в банке как 3536
(`wp_nav:22:4b58d07c`, тот же файл 22.xls, эталон 7).

Подсказка (hints_text) наводит без ответа (правило tsk-855). У 3536 прежняя подсказка
велела строить расписание «как можно раньше» — это даёт 8 процессов на 3 мс, то есть
ведёт к неверному ответу; заменяется той же подсказкой.

Протокол /db-check: dry-run по умолчанию; текущее условие и подсказка сверяются
дословно; course_id/order_position/solution_rules не трогаем; content_provenance —
source='manual_script' (tsk-760); одна транзакция, верификация, затем COMMIT.

Запуск (из корня LMS):
  python scripts/tsk1199_fix_3087_stem_hint.py
  DBCHECK_OK=1 python scripts/tsk1199_fix_3087_stem_hint.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1199.fix3087")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTHOR_REMARK = (
    "Внимание. На мой взгляд, ошибка в правильном ответе. "
    "Если я не прав, напишите в комментариях.<br>"
)
OLD_HINT_3536 = (
    "Сначала постройте расписание «как можно раньше» для всех процессов (начало зависимого — "
    "сразу по завершении самого позднего предшественника). Отметьте интервалы всех процессов на "
    "одной временной шкале и для каждого момента посчитайте, сколько интервалов его покрывают — "
    "так вы найдёте, какое максимальное число процессов вообще может идти одновременно, а затем — "
    "самый длинный отрезок с этим числом."
)
HINT = (
    "В условии нет требования завершить всё как можно раньше, поэтому процесс можно запускать "
    "не сразу, а позже — когда удобно. Расписание «как можно раньше» здесь занижает число "
    "одновременных процессов: если у вас получилось 8, это ещё не максимум. "
    "Ищите наибольший набор процессов, в котором ни один не зависит от другого ни напрямую, "
    "ни через цепочку, — такие процессы можно сдвинуть и запустить вместе. Удобно разбить "
    "процессы на группы, связанные зависимостями, и в каждой группе найти свой наибольший набор. "
    "Затем посмотрите, какие процессы входят в любой такой наибольший набор: отрезок не может "
    "длиться дольше самого короткого из них. Учтите, что внутри отрезка один процесс группы "
    "может сменить другой, зависящий от него, — число работающих при этом не падает."
)
EXPECTED_HINTS: Dict[int, List[str]] = {3087: [], 3536: [OLD_HINT_3536]}
UIDS = {3087: "tg:ege:886", 3536: "wp_nav:22:4b58d07c"}


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
    """Сверить, показать и (с --apply) записать правку условия и подсказок."""
    parser = argparse.ArgumentParser(description="Условие и подсказка 3087/3536 (tsk-1199)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    conn = psycopg2.connect(**prod_dsn())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for tid, uid in UIDS.items():
                cur.execute(
                    "SELECT external_uid, order_position, task_content, solution_rules "
                    "FROM tasks WHERE id = %s FOR UPDATE",
                    (tid,),
                )
                row = cur.fetchone()
                if row is None or row["external_uid"] != uid:
                    logger.error("Задание %s не то или не найдено", tid)
                    conn.rollback()
                    return 1
                content = dict(row["task_content"])
                if content.get("hints_text") != EXPECTED_HINTS[tid]:
                    logger.error("Подсказка %s разошлась с ожидаемой: %s", tid, content.get("hints_text"))
                    conn.rollback()
                    return 1
                stem = content["stem"]
                fields = ["task_content.hints_text"]
                if tid == 3087:
                    if stem.count(AUTHOR_REMARK) != 1:
                        logger.error("Реплика автора в условии 3087 не найдена дословно")
                        conn.rollback()
                        return 1
                    stem = stem.replace(AUTHOR_REMARK, "")
                    fields.insert(0, "task_content.stem")
                content["stem"] = stem
                content["hints_text"] = [HINT]
                content["has_hints"] = True
                logger.info("\n=== %s (%s) ===\n--- условие ---\n%s\n--- подсказка ---\n%s",
                            tid, uid, stem, HINT)
                if not args.apply:
                    continue
                prov = {
                    "source": "manual_script",
                    "fields": fields,
                    "edited_at": now,
                    "edited_by": 0,
                    "script": "scripts/tsk1199_fix_3087_stem_hint.py",
                    "task": "tsk-1199",
                    "reason": "реплика автора ТГ-канала снята из условия; подсказка без ответа (tsk-855)",
                }
                cur.execute(
                    "UPDATE tasks SET task_content = %s::jsonb, content_provenance = %s::jsonb WHERE id = %s",
                    (json.dumps(content, ensure_ascii=False), json.dumps(prov, ensure_ascii=False), tid),
                )
                cur.execute(
                    "SELECT task_content, order_position, solution_rules FROM tasks WHERE id = %s", (tid,)
                )
                after = cur.fetchone()
                ok = (
                    after["task_content"]["stem"] == stem
                    and "На мой взгляд" not in after["task_content"]["stem"]
                    and after["task_content"]["hints_text"] == [HINT]
                    and after["task_content"]["hints_video"] == row["task_content"]["hints_video"]
                    and after["order_position"] == row["order_position"]
                    and after["solution_rules"] == row["solution_rules"]
                )
                if not ok:
                    conn.rollback()
                    logger.error("Верификация %s не прошла — откат.", tid)
                    return 1
            if not args.apply:
                conn.rollback()
                logger.info("\nDry-run: ничего не записано.")
                return 0
            conn.commit()
            logger.info("\nЗаписано и проверено: %s", list(UIDS))
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
