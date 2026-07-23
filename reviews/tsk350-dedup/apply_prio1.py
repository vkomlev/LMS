# -*- coding: utf-8 -*-
"""tsk-350: деактивация дублей заданий ЕГЭ (tasks.is_active = false).

Без --apply — сухой прогон: показывает выборку и ничего не пишет.
Физически ничего не удаляется: попытки, результаты и прогресс сохраняются.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2

from dump_tasks import prod_dsn

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent


def connect(readonly: bool):
    p = urlparse(prod_dsn())
    conn = psycopg2.connect(
        host=p.hostname, port=p.port or 5432, user=unquote(p.username or ""),
        password=unquote(p.password or ""), dbname=(p.path or "/").lstrip("/"),
        connect_timeout=15,
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="выполнить запись")
    args = ap.parse_args()

    plan = json.loads((HERE / "prio1_plan.json").read_text(encoding="utf-8"))
    hide = sorted({i for r in plan for i in r["hide"]})
    keep = sorted({r["keep"] for r in plan})
    assert not (set(hide) & set(keep)), "канон попал в список скрываемых"
    print(f"групп: {len(plan)}, скрыть заданий: {len(hide)}, канонов: {len(keep)}")

    conn = connect(readonly=not args.apply)
    cur = conn.cursor()

    # --- бэкап состояния до правки (весь охват задачи, не только скрываемые)
    cur.execute("""
        SELECT t.id, t.course_id, t.external_uid, t.is_active
        FROM tasks t JOIN courses c ON c.id = t.course_id
        WHERE c.course_uid LIKE 'wp:zadanie-%%'
           OR c.course_uid LIKE 'lms:tsk347:hard:%%'
           OR c.id IN (138, 139)
        ORDER BY t.id
    """)
    snapshot = [
        {"id": r[0], "course_id": r[1], "external_uid": r[2], "is_active": r[3]}
        for r in cur.fetchall()
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = HERE / f"backup_prio1_{stamp}.json"
    backup.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    print(f"бэкап состояния: {backup} ({len(snapshot)} строк)")

    # --- предусловия
    cur.execute("SELECT id, is_active FROM tasks WHERE id = ANY(%s)", (hide,))
    state = dict(cur.fetchall())
    missing = [i for i in hide if i not in state]
    already = [i for i, a in state.items() if not a]
    if missing or already:
        print(f"СТОП: отсутствуют {missing[:5]}, уже скрыты {already[:5]}")
        conn.rollback()
        return 1

    cur.execute("SELECT count(*) FROM tasks WHERE id = ANY(%s) AND is_active", (keep,))
    keep_active = cur.fetchone()[0]
    if keep_active != len(keep):
        print(f"СТОП: активных канонов {keep_active} из {len(keep)}")
        conn.rollback()
        return 1
    print("предусловия: все скрываемые активны, все каноны активны — ок")

    # --- выборка примеров
    cur.execute("""
        SELECT t.id, t.course_id, t.external_uid,
               left(regexp_replace(t.task_content->>'stem', '<[^>]+>', ' ', 'g'), 90)
        FROM tasks t WHERE t.id = ANY(%s) ORDER BY t.id LIMIT 10
    """, (hide[:10],))
    print("\nпримеры скрываемых:")
    for row in cur.fetchall():
        print(f"  {row[0]:<6} курс={row[1]:<5} {(row[2] or '')[:44]:<44} {row[3]}")

    if not args.apply:
        print("\nсухой прогон: запись НЕ выполнялась (нужен --apply)")
        conn.rollback()
        return 0

    cur.execute("UPDATE tasks SET is_active = false WHERE id = ANY(%s) AND is_active", (hide,))
    changed = cur.rowcount
    print(f"\nобновлено строк: {changed}")
    if changed != len(hide):
        print("СТОП: число обновлённых строк не совпало с планом — откат")
        conn.rollback()
        return 1

    cur.execute("SELECT count(*) FROM tasks WHERE id = ANY(%s) AND is_active", (hide,))
    left_active = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM tasks WHERE id = ANY(%s) AND is_active", (keep,))
    keep_still = cur.fetchone()[0]
    if left_active != 0 or keep_still != len(keep):
        print(f"СТОП: в транзакции осталось активных дублей {left_active}, "
              f"канонов активно {keep_still} — откат")
        conn.rollback()
        return 1

    conn.commit()
    print("транзакция зафиксирована")
    return 0


if __name__ == "__main__":
    sys.exit(main())
