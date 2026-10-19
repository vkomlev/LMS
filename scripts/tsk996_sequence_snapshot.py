# -*- coding: utf-8 -*-
"""tsk-996 (фаза A): снимок порядка узлов по старому и новому пути. ТОЛЬКО ЧТЕНИЕ.

Перед выкатом фазы A оператор сверяет на копии/проде, что общая
`node_sequence` даёт ровно прежний порядок (ADR-0008, шаг 1 плана: «снимок
выдачи по 30 реальным узлам до/после совпадает»). Для каждого `course_id` и
ученика скрипт строит порядок элементов дважды — с NODE_SEQUENCE_ENABLED=false
(прежний код) и true (новый) — и печатает JSON с разницей.

Что сравнивается (всё — чистое чтение):

* ``walk`` — учебный обход движка: шаги дерева и элементы каждого шага, которые
  ученик реально видит (выборка tsk-314, правило tsk-692/1261, программа
  tsk-798). Ровно по этому списку next-item ищет «первый незавершённый после
  позиции»;
* ``syllabus`` — порядок `items[]` программы курса (`syllabus-states`);
* ``preview`` — порядок предпросмотра «глазами ученика» (tsk-1334);
* ``progress`` — порядок строк карточки прогресса у преподавателя;
* ``grace`` — что правила tsk-692/1261 прощают ученику (зависит от порядка).

Почему не вызывается сам next-item. `resolve_next_item` при зависимостях
курсов ПИШЕТ: `compute_course_state(update_state_table=True)` обновляет
`student_course_state`, а на завершённом курсе может запустить эскалацию
методисту (уведомление). Поэтому скрипт зовёт только читающие функции,
из которых next-item собран (обход + списки шагов), — порядок у них тот же.

Страховка от записи: соединение открывается с
``default_transaction_read_only=on``, работа идёт в транзакции, открытой
как ``SET TRANSACTION READ ONLY`` (PostgreSQL отклонит любую запись ошибкой),
и в конце — ``ROLLBACK``.

Запуск (DSN — `DATABASE_URL` из окружения или `.env`):

    python scripts/tsk996_sequence_snapshot.py --student 4507 --courses 871,1165 \\
        [--out snapshot.json]

Код возврата: 0 — все снимки совпали, 1 — есть разница (подробности в JSON),
2 — ошибка запуска.

Фаза B (якоря `tasks.anchor_material_id`): прежний путь якоря не читает, новый
ставит задание с якорем сразу после его материала. Поэтому на узле без якорей
снимки обязаны совпасть (код 0), а на узле с якорями разница ожидаема — это и
есть перестановка, и JSON показывает её как есть («было/стало» перед пилотом).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("tsk996_sequence_snapshot")

FLAG = "NODE_SEQUENCE_ENABLED"


def _parse_ids(raw: str) -> List[int]:
    """`871,1165 1200` → [871, 1165, 1200]."""
    return [int(x) for x in raw.replace(",", " ").split() if x.strip()]


async def _with_flag(value: bool, call: Callable[[], Awaitable[Any]]) -> Any:
    """Выполнить `call` при заданном положении рубильника (он читается на каждый вызов)."""
    os.environ[FLAG] = "true" if value else "false"
    return await call()


def _reset_caches(db: Any) -> None:
    """Каждый путь считает всё сам — без кеша, заполненного другим путём."""
    from app.repos.courses_repo import invalidate_course_tree_cache
    from app.services.content_grace_service import invalidate_grace_cache

    invalidate_course_tree_cache(db)
    invalidate_grace_cache(db)


async def _walk_old(db: Any, engine: Any, root: int, student: int) -> List[list]:
    """Обход движка по прежнему коду: шаги (материалы|задания) и их элементы."""
    out: List[list] = []
    links = await _transparent_here(db, engine, root)
    for cid, step in await engine._collect_steps_in_order(db, root):  # noqa: SLF001
        if cid in links:
            continue
        if step == "materials":
            rows = await engine._effective_material_rows(  # noqa: SLF001
                db, cid, student, root_course_id=root
            )
            out.extend(["material", int(i), cid] for i, _ in rows)
        else:
            rows = await engine._effective_task_rows(  # noqa: SLF001
                db, cid, student, root_course_id=root
            )
            out.extend(["task", int(i), cid] for i, _ in rows)
    return out


async def _walk_new(db: Any, engine: Any, root: int, student: int) -> List[list]:
    """Обход движка по `node_sequence`: шаги (голова|хвост) и их элементы."""
    out: List[list] = []
    links = await _transparent_here(db, engine, root)
    for cid, segment in await engine._collect_node_steps(db, root):  # noqa: SLF001
        if cid in links:
            continue
        for entry in await engine._effective_segment(  # noqa: SLF001
            db, cid, segment, student, root_course_id=root
        ):
            out.append([entry.kind, entry.item_id, cid])
    return out


async def _transparent_here(db: Any, engine: Any, root: int) -> set:
    """Прозрачные подкурсы, чей хозяин в дереве: отдельным шагом не обходятся."""
    from app.services.transparent_subcourse import absorbed_in_tree

    tree = await engine._collect_courses_in_order(db, root)  # noqa: SLF001
    return set(await absorbed_in_tree(db, tree))


async def _snapshot_course(db: Any, root: int, student: int) -> Dict[str, Any]:
    """Все порядки одного узла по обоим путям."""
    from app.services import manual_progress_service, me_service
    from app.services.content_grace_service import compute_grace_reasons
    from app.services.learning_engine_service import LearningEngineService
    from app.services.preview_navigation import _ordered_items

    engine = LearningEngineService()

    async def walk(new: bool) -> List[list]:
        _reset_caches(db)
        return await (_walk_new if new else _walk_old)(db, engine, root, student)

    async def syllabus(_new: bool) -> List[list]:
        _reset_caches(db)
        res = await me_service.get_syllabus_states(db, user_id=student, root_course_id=root)
        return [
            [i["kind"], i.get("material_id") or i.get("task_id"), i["course_id"],
             i["requirement_level"]]
            for i in res["items"]
        ]

    async def preview(_new: bool) -> List[list]:
        _reset_caches(db)
        return [[k, i, bool(e)] async for k, i, e in _ordered_items(db, engine, root)]

    async def progress(_new: bool) -> List[list]:
        _reset_caches(db)
        res = await manual_progress_service.get_student_progress(
            db, student_id=student, course_id=root
        )
        return [[i["item_type"], i["item_id"], i["course_id"]] for i in res["items"]]

    async def grace(_new: bool) -> List[list]:
        _reset_caches(db)
        reasons = await compute_grace_reasons(db, student, root)
        return sorted([k, i, r] for (k, i), r in reasons.items())

    result: Dict[str, Any] = {}
    for name, fn in (
        ("walk", walk), ("syllabus", syllabus), ("preview", preview),
        ("progress", progress), ("grace", grace),
    ):
        old = await _with_flag(False, lambda fn=fn: fn(False))
        new = await _with_flag(True, lambda fn=fn: fn(True))
        first_diff: Optional[int] = None
        if old != new:
            first_diff = next(
                (n for n, (a, b) in enumerate(zip(old, new)) if a != b),
                min(len(old), len(new)),
            )
        result[name] = {
            "equal": old == new,
            "count_old": len(old),
            "count_new": len(new),
            "first_diff_index": first_diff,
            "old": old,
            "new": new if old != new else "= old",
        }
    return result


async def run(dsn: str, student: int, courses: List[int]) -> Dict[str, Any]:
    """Снимок по всем узлам в одной транзакции READ ONLY с откатом в конце."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    # Третья страховка: само соединение открывается только для чтения, так что
    # и транзакция после случайного commit не смогла бы писать.
    engine = create_async_engine(
        dsn, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
    )
    report: Dict[str, Any] = {
        "task": "tsk-996",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_host": urlsplit(dsn.replace("+asyncpg", "")).hostname,
        "student_id": student,
        "courses": {},
    }
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            # Первая команда транзакции: дальше PostgreSQL отклоняет любую запись.
            await db.execute(text("SET TRANSACTION READ ONLY"))
            try:
                for cid in courses:
                    try:
                        report["courses"][str(cid)] = await _snapshot_course(db, cid, student)
                    except Exception as exc:  # noqa: BLE001 — узел в отчёт, не падаем целиком
                        logger.exception("tsk996: узел %s не снят", cid)
                        report["courses"][str(cid)] = {"error": repr(exc)}
                        # Ошибка в транзакции делает её непригодной — начинаем новую,
                        # снова только для чтения.
                        await db.rollback()
                        await db.execute(text("SET TRANSACTION READ ONLY"))
            finally:
                await db.rollback()
    finally:
        await engine.dispose()

    mismatches = [
        f"{cid}:{name}"
        for cid, snap in report["courses"].items()
        for name, part in snap.items()
        if isinstance(part, dict) and not part.get("equal", True)
    ]
    errors = [cid for cid, snap in report["courses"].items() if "error" in snap]
    report["summary"] = {
        "courses": len(courses),
        "all_equal": not mismatches and not errors,
        "mismatches": mismatches,
        "errors": errors,
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--student", type=int, required=True, help="ID ученика")
    parser.add_argument(
        "--courses", required=True, help="ID узлов через запятую или пробел"
    )
    parser.add_argument("--out", type=Path, help="Куда записать JSON (по умолчанию — stdout)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL не задан (окружение или .env)", file=sys.stderr)
        return 2
    courses = _parse_ids(args.courses)
    if not courses:
        print("Пустой список --courses", file=sys.stderr)
        return 2

    report = asyncio.run(run(dsn, args.student, courses))
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
        print(
            f"Снимок записан: {args.out} — всё совпало: {report['summary']['all_equal']}",
            file=sys.stderr,
        )
    else:
        print(payload)
    return 0 if report["summary"]["all_equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
