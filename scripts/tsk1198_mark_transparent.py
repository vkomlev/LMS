"""tsk-1198: подборки практикумов прозрачны в курсах банка «Задание N».

Решение оператора (03.10): в курсе банка задания подборки идут обычным списком
вместе с остальными, после теории и на ПРЕЖНИХ местах; разделом подборка видна
только в практикуме 7–11 класса. Практикумы и сдачи не трогаем.

Что делает (одна транзакция, по умолчанию dry-run с откатом):
1. `course_parents.is_transparent = true` у связи «подборка → курс банка» —
   32 подборки (`lms:tsk1161:bank:*`, `lms:tsk1163:bank:*`, выборки
   `tsk1161_selection.json` / `tsk1163_selection.json`); связи с практикумами
   не меняются.
2. `tasks.host_order_position` у заданий подборки — место в списке курса банка:
   текущая позиция первого задания банка, стоявшего ПОСЛЕ этого задания до
   переноса (если такого нет — в конец). Позиции до переноса —
   `tsk1198_positions_0929.json`, выжимка из копии прода от 29.09 03:15 UTC
   (перенос tsk-1161 — 29.09 14:46, tsk-1163 — позже). `order_position`
   заданий не меняется: это порядок внутри подборки (практикум).

Проверки до записи и после: число связей и заданий, неизменность
`order_position`, сдач и связей с практикумами.

Запуск (из корня LMS):
  python scripts/tsk1198_mark_transparent.py            # dry-run: план + откат
  DBCHECK_OK=1 python scripts/tsk1198_mark_transparent.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk1132_move_foreign_tasks import prod_dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1198")

HERE = Path(__file__).resolve().parent
SNAPSHOT: Dict[str, List[List[Any]]] = json.loads(
    (HERE / "tsk1198_positions_0929.json").read_text(encoding="utf-8")
)["banks"]


def selection() -> List[Tuple[str, int]]:
    """(course_uid подборки, курс банка) по обеим выборкам."""
    pairs: List[Tuple[str, int]] = []
    for name in ("tsk1161", "tsk1163"):
        for g in json.loads((HERE / f"{name}_selection.json").read_text(encoding="utf-8")):
            # Подборки tsk-1163 переиспользуют узлы tsk-1161, если ключ уже был.
            pairs.append((g["key"], int(g["bank"])))
    return pairs


def resolve_nodes(cur: Any, pairs: List[Tuple[str, int]]) -> List[Tuple[int, int, str]]:
    """(подборка, банк, uid): ищем узел по ключу среди обоих префиксов."""
    out: List[Tuple[int, int, str]] = []
    for key, bank in pairs:
        cur.execute(
            "SELECT id, course_uid FROM courses WHERE course_uid = ANY(%s)",
            ([f"lms:tsk1161:bank:{key}", f"lms:tsk1163:bank:{key}"],),
        )
        rows = cur.fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"Подборка {key}: найдено узлов {len(rows)}")
        out.append((int(rows[0]["id"]), bank, rows[0]["course_uid"]))
    return out


def plan_slots(cur: Any, node: int, bank: int) -> Dict[int, int]:
    """Место каждого задания подборки в списке банка (см. docstring модуля)."""
    old = {int(t): p for t, p in SNAPSHOT.get(str(bank), []) if p is not None}
    cur.execute("SELECT id, order_position FROM tasks WHERE course_id = %s", (bank,))
    current = {int(r["id"]): r["order_position"] for r in cur.fetchall()}
    cur.execute("SELECT id FROM tasks WHERE course_id = %s ORDER BY order_position, id", (node,))
    sub_tasks = [int(r["id"]) for r in cur.fetchall()]
    tail = max((p for p in current.values() if p is not None), default=0) + 1
    slots: Dict[int, int] = {}
    for tid in sub_tasks:
        if tid not in old:
            raise RuntimeError(f"Задание {tid} подборки {node}: нет в копии банка {bank}")
        after = [
            (old[y], current[y]) for y in current
            if y in old and old[y] > old[tid] and current[y] is not None
        ]
        slots[tid] = min(after)[1] if after else tail
    return slots


def fingerprint(cur: Any, nodes: List[int], banks: List[int]) -> Dict[str, Any]:
    """Что меняться не должно: позиции, сдачи, связи с практикумами."""
    ids = nodes + banks
    cur.execute(
        "SELECT md5(string_agg(id || ':' || course_id || ':' || COALESCE(order_position, -1), ',' "
        "ORDER BY id)) AS h FROM tasks WHERE course_id = ANY(%s)",
        (ids,),
    )
    positions = cur.fetchone()["h"]
    cur.execute(
        "SELECT count(*) AS n FROM task_results tr JOIN tasks t ON t.id = tr.task_id "
        "WHERE t.course_id = ANY(%s)",
        (ids,),
    )
    results = cur.fetchone()["n"]
    cur.execute(
        "SELECT md5(string_agg(course_id || '>' || parent_course_id || ':' || "
        "COALESCE(order_number, -1) || ':' || is_transparent, ',' ORDER BY course_id, parent_course_id)) AS h "
        "FROM course_parents WHERE course_id = ANY(%s) AND NOT (parent_course_id = ANY(%s))",
        (nodes, banks),
    )
    return {"positions": positions, "results": results, "praktikum_links": cur.fetchone()["h"]}


def main() -> int:
    """Разметить 32 подборки в одной транзакции с проверкой до и после."""
    parser = argparse.ArgumentParser(description="tsk-1198")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(**prod_dsn())
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        nodes = resolve_nodes(cur, selection())
        if len(nodes) != 32 or len({n for n, _, _ in nodes}) != 32:
            logger.error("Ожидалось 32 разные подборки, получено %s — останов", len(nodes))
            conn.rollback()
            return 1
        node_ids = [n for n, _, _ in nodes]
        bank_ids = sorted({b for _, b, _ in nodes})
        before = fingerprint(cur, node_ids, bank_ids)

        plan: Dict[int, Dict[int, int]] = {}
        for node, bank, uid in nodes:
            cur.execute(
                "SELECT 1 FROM course_parents WHERE course_id = %s AND parent_course_id = %s",
                (node, bank),
            )
            if cur.fetchone() is None:
                logger.error("Нет связи %s → банк %s — останов", node, bank)
                conn.rollback()
                return 1
            plan[node] = plan_slots(cur, node, bank)
            logger.info("%-26s %s → %s: %s", uid, node, bank,
                        ", ".join(f"{t}@{s}" for t, s in plan[node].items()))

        for node, bank, _ in nodes:
            cur.execute(
                "UPDATE course_parents SET is_transparent = true "
                "WHERE course_id = %s AND parent_course_id = %s",
                (node, bank),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Связь {node} → {bank}: обновлено {cur.rowcount}")
            for tid, slot in plan[node].items():
                cur.execute(
                    "UPDATE tasks SET host_order_position = %s WHERE id = %s AND course_id = %s",
                    (slot, tid, node),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"Задание {tid}: обновлено {cur.rowcount}")

        after = fingerprint(cur, node_ids, bank_ids)
        cur.execute("SELECT count(*) AS n FROM course_parents WHERE is_transparent")
        links = cur.fetchone()["n"]
        cur.execute(
            "SELECT count(*) AS n FROM tasks WHERE course_id = ANY(%s) AND host_order_position IS NULL",
            (node_ids,),
        )
        unslotted = cur.fetchone()["n"]
        ok = before == after and links == 32 and unslotted == 0
        logger.info("Проверка: неизменное %s, прозрачных связей %s, заданий без места %s",
                    before == after, links, unslotted)
        if not ok:
            logger.error("Проверка не прошла — откат (до %s, после %s)", before, after)
            conn.rollback()
            return 1
        if args.apply:
            conn.commit()
            logger.info("ЗАПИСАНО: 32 связи, %s заданий", sum(len(p) for p in plan.values()))
        else:
            conn.rollback()
            logger.info("dry-run: откат (для записи --apply)")
        return 0
    except Exception:
        conn.rollback()
        logger.exception("Ошибка — откат")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
