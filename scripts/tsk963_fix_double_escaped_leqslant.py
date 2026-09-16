"""tsk-963: починить дважды закодированный знак ≤ в условиях 2385 и 2352.

Что случилось. Две страницы sdamgia (партия 20260602, задания 47016 и 48437)
отдали знак «меньше или равно» как `&amp;leqslant;` — сущность, закодированная
дважды. Парсер CB пропустил её как есть, LMS получил тот же текст, а страница
ученика снимает один слой кодировки и показывает остаток `&leqslant;` текстом.
У соседнего задания той же партии (48464) знак нормальный: `≤`.

Что делает скрипт. В LMS (`tasks.task_content->>'stem'`) и в снимке CB
(`external_tasks.task.payload_data`) заменяет `&amp;leqslant;` на `≤` — только у
строк, где эта последовательность есть. В CB пересчитывает `payload_hash` по
формуле `runner/stages.py::_payload_hash`, чтобы снимок остался согласованным.

Протокол (db-check): без `--apply` — только план и выборка; с `--apply` — одна
транзакция на базу, проверка после, откат при любом расхождении.

Usage:
    python scripts/tsk963_fix_double_escaped_leqslant.py
    DBCHECK_OK=1 python scripts/tsk963_fix_double_escaped_leqslant.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json

BAD = "&amp;leqslant;"
GOOD = "≤"  # ≤
LMS_IDS = (2385, 2352)
CB_UIDS = ("ext:d4:sdamgia:20260602:47016", "ext:d4:sdamgia:20260602:48437")


def dsn_for(server: str) -> str:
    """Строка подключения из `.mcp.json` LMS (секрет в вывод не попадает)."""
    cfg = json.loads((Path(__file__).resolve().parent.parent / ".mcp.json").read_text(encoding="utf-8"))
    m = re.search(r"postgres(?:ql)?://[^\"'\s]+", json.dumps(cfg["mcpServers"][server]))
    if not m:
        raise SystemExit(f"в .mcp.json нет DSN для {server}")
    return m.group(0)


def payload_hash(payload: dict[str, Any]) -> str:
    """Копия `monolith/external_tasks/runner/stages.py::_payload_hash`."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fragments(text: str) -> list[str]:
    return [text[max(0, m.start() - 25): m.end() + 15] for m in re.finditer(re.escape(BAD), text)]


def fix_lms(apply: bool) -> int:
    conn = psycopg2.connect(dsn_for("learn_prod_db"))
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(
        "SELECT id, external_uid, is_active, task_content->>'stem' FROM tasks WHERE id = ANY(%s) ORDER BY id",
        (list(LMS_IDS),),
    )
    rows = cur.fetchall()
    print(f"\n[LMS] выборка ({len(rows)} строк):")
    todo = []
    for tid, uid, active, stem in rows:
        n = stem.count(BAD)
        print(f"  id={tid} {uid} active={active} вхождений={n} {fragments(stem)[:2]}")
        if n:
            todo.append(tid)
    cur.execute("SELECT count(*) FROM tasks WHERE task_content::text LIKE %s", (f"%{BAD}%",))
    print(f"  всего в tasks строк с '{BAD}': {cur.fetchone()[0]} (ожидаем ровно {len(todo)})")
    if not apply:
        conn.rollback()
        return len(todo)
    cur.execute(
        """
        UPDATE tasks
        SET task_content = jsonb_set(task_content, '{stem}',
                                     to_jsonb(replace(task_content->>'stem', %s, %s))),
            updated_at = now()
        WHERE id = ANY(%s) AND task_content->>'stem' LIKE %s
        """,
        (BAD, GOOD, todo, f"%{BAD}%"),
    )
    updated = cur.rowcount
    cur.execute("SELECT count(*) FROM tasks WHERE task_content::text LIKE %s", (f"%{BAD}%",))
    left = cur.fetchone()[0]
    cur.execute(
        "SELECT id, task_content->>'stem' LIKE %s FROM tasks WHERE id = ANY(%s) ORDER BY id",
        (f"%{GOOD}%", todo),
    )
    has_good = dict(cur.fetchall())
    ok = updated == len(todo) and left == 0 and all(has_good.values())
    print(f"  UPDATE затронул {updated}, осталось с '{BAD}': {left}, знак ≤ есть: {has_good}")
    if not ok:
        conn.rollback()
        raise SystemExit("[LMS] проверка не сошлась — откат")
    conn.commit()
    print("  [LMS] COMMIT")
    return updated


def fix_cb(apply: bool) -> int:
    conn = psycopg2.connect(dsn_for("content_backbone_prod_db"))
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(
        "SELECT id, external_uid, state, payload_hash, payload_data FROM external_tasks.task "
        "WHERE external_uid = ANY(%s) ORDER BY id",
        (list(CB_UIDS),),
    )
    rows = cur.fetchall()
    print(f"\n[CB] выборка ({len(rows)} строк):")
    plan = []
    for rid, uid, state, phash, payload in rows:
        text = json.dumps(payload, ensure_ascii=False)
        n = text.count(BAD)
        print(f"  id={rid} {uid} state={state} вхождений={n} {fragments(text)[:2]}")
        if not n:
            continue
        new_payload = json.loads(text.replace(BAD, GOOD))
        old_hash_matches = payload_hash(payload) == phash
        new_hash = payload_hash(new_payload)
        print(f"     payload_hash совпадает с формулой: {old_hash_matches}; новый: {new_hash[:12]}…")
        plan.append((rid, new_payload, new_hash if old_hash_matches else phash))
    cur.execute("SELECT count(*) FROM external_tasks.task WHERE payload_data::text LIKE %s", (f"%{BAD}%",))
    print(f"  всего в external_tasks.task строк с '{BAD}': {cur.fetchone()[0]} (ожидаем ровно {len(plan)})")
    if not apply:
        conn.rollback()
        return len(plan)
    for rid, new_payload, new_hash in plan:
        cur.execute(
            "UPDATE external_tasks.task SET payload_data = %s, payload_hash = %s, updated_at = now() WHERE id = %s",
            (Json(new_payload, dumps=lambda o: json.dumps(o, ensure_ascii=False)), new_hash, rid),
        )
    cur.execute("SELECT count(*) FROM external_tasks.task WHERE payload_data::text LIKE %s", (f"%{BAD}%",))
    left = cur.fetchone()[0]
    cur.execute(
        "SELECT id, payload_data::text LIKE %s FROM external_tasks.task WHERE id = ANY(%s) ORDER BY id",
        (f"%{GOOD}%", [p[0] for p in plan]),
    )
    has_good = dict(cur.fetchall())
    print(f"  осталось с '{BAD}': {left}, знак ≤ есть: {has_good}")
    if left or not all(has_good.values()):
        conn.rollback()
        raise SystemExit("[CB] проверка не сошлась — откат")
    conn.commit()
    print("  [CB] COMMIT")
    return len(plan)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="выполнить запись (по умолчанию только план)")
    args = ap.parse_args()
    print("РЕЖИМ:", "ЗАПИСЬ" if args.apply else "план (без записи)")
    n_lms = fix_lms(args.apply)
    n_cb = fix_cb(args.apply)
    print(f"\nИтог: LMS {n_lms}, CB {n_cb} {'исправлено' if args.apply else 'к исправлению'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
