"""Индивидуальный курс ученику с замком на его основные курсы (tsk-1245).

Правило: индивидуальный курс (повторение, доработка) блокирует прохождение
основных курсов ученика, пока не пройден целиком. Замок — точечная
зависимость `course_dependencies(основной -> индивидуальный, auto_assign=false)`
(tsk-231, фаза 6): блокирует только тех, кому индивидуальный курс доступен,
остальных учеников основного курса не трогает. Снимается сам, когда курс
COMPLETED (все задания с зачётом последней сдачи + все материалы).
Аварийно — отчислить ученика с индивидуального курса (`user_courses.is_active`).

Замок закрывает «следующий шаг» движка и оглавление основного курса; прямые
ссылки на задания (домашка со страницы занятий) остаются рабочими.

Режимы:
  Новый курс:   --user 4569 --title "..." --uid ind:... --subcourses 863,867
  Есть курс:    --user 4569 --course-id 2106
  --block 88,112  — какие корни закрыть (по умолчанию все активные корни ученика)
  --apply         — записать; без флага прогон с откатом.
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("individual_course")

project_root = Path(__file__).resolve().parent.parent


def _dsn() -> str:
    """Прод-DSN learn из `.mcp.json` (секрет не печатаем)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("прод-DSN learn_prod_db не найден в .mcp.json")


def _ids(raw: str | None) -> list[int]:
    """Разобрать список id через запятую."""
    return [int(x) for x in raw.split(",") if x.strip()] if raw else []


async def _create_course(conn: asyncpg.Connection, a: argparse.Namespace) -> int:
    """Создать корень, подцепить подкурсы, записать ученика. Вернуть id курса."""
    if not (a.title and a.uid and a.subcourses):
        raise ValueError("для нового курса нужны --title, --uid, --subcourses")
    if await conn.fetchval("SELECT 1 FROM courses WHERE course_uid=$1", a.uid):
        raise RuntimeError(f"курс с uid {a.uid} уже есть")
    cid = await conn.fetchval(
        "INSERT INTO courses (title, access_level, description, course_uid) "
        "VALUES ($1, 'self_guided', $2, $3) RETURNING id",
        a.title, a.description, a.uid,
    )
    for n, sub in enumerate(_ids(a.subcourses), start=1):
        await conn.execute(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number, is_transparent) "
            "VALUES ($1, $2, $3, false)", sub, cid, n,
        )
    await conn.execute(
        "INSERT INTO user_courses (user_id, course_id, is_active) VALUES ($1, $2, true)",
        a.user, cid,
    )
    return cid


async def _lock_roots(conn: asyncpg.Connection, user: int, cid: int, block: list[int]) -> list[int]:
    """Поставить точечный замок на корни ученика. Вернуть закрытые корни."""
    if not block:
        rows = await conn.fetch(
            "SELECT uc.course_id FROM user_courses uc WHERE uc.user_id=$1 AND uc.is_active "
            "AND uc.course_id<>$2 ORDER BY uc.order_number", user, cid)
        block = [r["course_id"] for r in rows]
    for root in block:
        enrolled = await conn.fetchval(
            "SELECT 1 FROM user_courses WHERE user_id=$1 AND course_id=$2 AND is_active", user, root)
        if not enrolled:
            raise RuntimeError(f"ученик {user} не записан на корень {root}")
        await conn.execute(
            "INSERT INTO course_dependencies (course_id, required_course_id, auto_assign) "
            "VALUES ($1, $2, false) ON CONFLICT (course_id, required_course_id) DO NOTHING",
            root, cid,
        )
    return block


async def main(a: argparse.Namespace) -> None:
    """Выполнить выдачу и замок в одной транзакции, проверить результат."""
    conn = await asyncpg.connect(_dsn())
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            cid = a.course_id or await _create_course(conn, a)
            if not await conn.fetchval(
                    "SELECT 1 FROM user_courses WHERE user_id=$1 AND course_id=$2 AND is_active", a.user, cid):
                raise RuntimeError(f"ученик {a.user} не записан на курс {cid}")
            roots = await _lock_roots(conn, a.user, cid, _ids(a.block))
            deps = await conn.fetch(
                "SELECT course_id, auto_assign FROM course_dependencies WHERE required_course_id=$1 "
                "ORDER BY course_id", cid)
            holders = await conn.fetchval(
                "SELECT count(*) FROM user_courses WHERE course_id=$1 AND is_active", cid)
            log.info("курс %s; закрыты корни %s; замки %s; учеников с курсом: %s",
                     cid, roots, [(d["course_id"], d["auto_assign"]) for d in deps], holders)
            if any(d["auto_assign"] for d in deps) or {d["course_id"] for d in deps} < set(roots):
                raise RuntimeError("проверка замков не сошлась")
        except Exception:
            await tr.rollback()
            raise
        if a.apply:
            await tr.commit()
            log.info("COMMIT")
        else:
            await tr.rollback()
            log.info("dry-run: ROLLBACK")
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", type=int, required=True)
    p.add_argument("--course-id", type=int)
    p.add_argument("--title")
    p.add_argument("--uid")
    p.add_argument("--description")
    p.add_argument("--subcourses")
    p.add_argument("--block")
    p.add_argument("--apply", action="store_true")
    asyncio.run(main(p.parse_args()))
