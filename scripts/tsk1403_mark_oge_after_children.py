"""tsk-1403: разметка материалов 15 узлов ОГЭ — «после подкурсов-основ».

Вариант В (решение оператора 22.10): в узлах «Задание N» корня ОГЭ 1080 в
голове остаются вводные — «Задание N ОГЭ…», «Что нужно знать», «Что проверяет…»,
«Видеотеория…»; метод, разборы, «Частые ошибки», «Проверь себя», «Итог»,
видеоразборы получают `placement='after_children'` и после включения рубильника
`NODE_AFTER_CHILDREN_ENABLED` идут после подкурсов узла. Материалы не
двигаются: переиздание из CB не создаёт дублей, якоря заданий целы.

НЕ ЗАПУСКАТЬ с --apply без «ОК» оператора. Порядок: разметка → флаг true →
обход предпросмотра по 1111 и 1130.

Запуск (под app на сервере, DSN из окружения):
    python scripts/tsk1403_mark_oge_after_children.py            # план, ROLLBACK
    python scripts/tsk1403_mark_oge_after_children.py --apply    # запись
Откат: UPDATE materials SET placement='head' WHERE id = ANY(<id из лога>).
"""
import asyncio
import logging
import re
import sys

import asyncpg

from tsk1330_replace_review_subcourses import _dsn

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tsk1403")

ROOT = 1080
# Проверка 23.10 (прод, чтение): правило отбирает 16 узлов 1111…1181 (задания 1–16),
# в задаче записано «15» — сверить с оператором до --apply.
EXPECTED_NODES = 16
# Вводные урока узла — остаются в голове.
HEAD_TITLE = re.compile(
    r"^(Задание \d+ ОГЭ|Что нужно знать|Что проверяет|Видеотеория)", re.IGNORECASE
)

NODES_SQL = """
SELECT c.id FROM course_parents cp JOIN courses c ON c.id = cp.course_id
WHERE cp.parent_course_id = $1 AND c.title ~ '^Задание [0-9]+\\.'
ORDER BY cp.order_number NULLS LAST, c.id
"""


def is_head(title: str) -> bool:
    """Вводный материал урока узла — остаётся до подкурсов."""
    return bool(HEAD_TITLE.match(title.strip()))


async def main(apply: bool) -> None:
    """Показать план разметки, записать в транзакции при --apply и проверить."""
    conn = await asyncpg.connect(_dsn())
    try:
        nodes = [r["id"] for r in await conn.fetch(NODES_SQL, ROOT)]
        log.info("узлов: %s %s", len(nodes), nodes)
        if len(nodes) != EXPECTED_NODES:
            raise RuntimeError(f"ожидалось {EXPECTED_NODES} узлов, найдено {len(nodes)}")
        rows = await conn.fetch(
            "SELECT id, course_id, order_position, is_active, placement, title "
            "FROM materials WHERE course_id = ANY($1::int[]) "
            "ORDER BY course_id, order_position NULLS LAST, id",
            nodes,
        )
        after_ids: list[int] = []
        for r in rows:
            target = "head" if is_head(r["title"]) else "after_children"
            if target == "after_children":
                after_ids.append(r["id"])
            log.info(
                "%s  узел %s  m%s op=%s %s  %s",
                "AFTER" if target != "head" else "head ",
                r["course_id"], r["id"], r["order_position"],
                "" if r["is_active"] else "(неактивен)", r["title"][:60],
            )
        per_node = {n: sum(1 for r in rows if r["course_id"] == n and r["id"] in after_ids) for n in nodes}
        empty = [n for n, k in per_node.items() if k == 0]
        if empty:
            raise RuntimeError(f"узлы без after-материалов: {empty} — правило заголовков не сошлось")
        log.info("к разметке: %s материалов; id: %s", len(after_ids), after_ids)

        tr = conn.transaction()
        await tr.start()
        try:
            res = await conn.execute(
                "UPDATE materials SET placement = 'after_children' "
                "WHERE id = ANY($1::int[]) AND placement = 'head'",
                after_ids,
            )
            check = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE id = ANY($1::int[]) "
                "AND placement <> 'after_children'",
                after_ids,
            )
            log.info("%s; не размечено после записи: %s", res, check)
            if check != 0:
                raise RuntimeError("проверка не сошлась")
        except Exception:
            await tr.rollback()
            raise
        if apply:
            await tr.commit()
            log.info("COMMIT")
        else:
            await tr.rollback()
            log.info("dry-run: ROLLBACK")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
