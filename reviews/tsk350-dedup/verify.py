# -*- coding: utf-8 -*-
"""tsk-350: построчная верификация правки.

Сверяет фактическое состояние БД со снимком ДО правки — по каждой из 2505
строк охвата, а не по агрегату (урок tsk-317). Ожидание: ровно 129 строк
плана сменили is_active с true на false, остальные 2376 не изменились.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2

from dump_tasks import prod_dsn

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent


def main() -> int:
    before_file = (Path(sys.argv[1]) if len(sys.argv) > 1
                   else sorted(HERE.glob("backup_is_active_*.json"))[0])
    before = {r["id"]: r["is_active"] for r in json.loads(
        before_file.read_text(encoding="utf-8"))}
    plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
    hide = {i for r in plan for i in r["hide"]}
    keep = {r["keep"] for r in plan}

    p = urlparse(prod_dsn())
    conn = psycopg2.connect(
        host=p.hostname, port=p.port or 5432, user=unquote(p.username or ""),
        password=unquote(p.password or ""), dbname=(p.path or "/").lstrip("/"),
        connect_timeout=15)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.is_active
        FROM tasks t JOIN courses c ON c.id = t.course_id
        WHERE c.course_uid LIKE 'wp:zadanie-%%'
           OR c.course_uid LIKE 'lms:tsk347:hard:%%'
           OR c.id IN (138, 139)
    """)
    now = dict(cur.fetchall())
    conn.close()

    print(f"снимок до правки: {before_file.name} ({len(before)} строк)")
    print(f"строк сейчас: {len(now)}")

    flipped_off = {i for i in now if before.get(i) is True and now[i] is False}
    flipped_on = {i for i in now if before.get(i) is False and now[i] is True}
    unchanged = {i for i in now if before.get(i) == now[i]}
    appeared = set(now) - set(before)
    vanished = set(before) - set(now)

    ok = True
    checks = [
        ("скрыто ровно по плану, поштучно", flipped_off == hide),
        ("ничего не включилось обратно", not flipped_on),
        ("не изменились остальные строки", len(unchanged) == len(before) - len(hide)),
        ("новых строк не появилось", not appeared),
        ("строки не исчезли", not vanished),
        ("все каноны активны", all(now.get(i) is True for i in keep)),
        ("все скрытые действительно скрыты", all(now.get(i) is False for i in hide)),
    ]
    for name, good in checks:
        print(("  OK   " if good else "  СБОЙ ") + name)
        ok &= good

    if flipped_off != hide:
        print("  лишние:", sorted(flipped_off - hide)[:20])
        print("  пропущенные:", sorted(hide - flipped_off)[:20])

    print(f"\nитог: {'верификация пройдена' if ok else 'ВЕРИФИКАЦИЯ НЕ ПРОЙДЕНА'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
