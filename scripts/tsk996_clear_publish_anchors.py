# -*- coding: utf-8 -*-
"""tsk-996 (фаза C): снять якоря заданий, поставленные публикатором. По умолчанию — только план.

Откат шага 3 ADR-0008 («Публикатор CB ставит якоря»). Флаг публикатора
ContentBackbone `--no-anchors` лишь перестаёт присылать якоря — уже
поставленные остаются. Снять их — этим скриптом: у заданий выбранных курсов
`anchor_source = 'publish'` → `anchor_material_id = NULL, anchor_source = NULL`,
и задания возвращаются в конец своего узла (как до фазы C).

Курсы выбираются поимённо, без поддерева: публикатор ставит якоря и в
подкурсах урока — их id перечислить в `--courses` тоже (или `--all`).

Ручные якоря методиста (`anchor_source = 'manual'`) скрипт не трогает никогда —
ни поставленные, ни «в конце раздела» (`manual` при пустом якоре).

Отметка правки задания (`tasks.updated_at`, tsk-760) от снятия якоря не
двигается (триггер `trg_task_set_updated_at` смотрит только на содержимое) —
следующее переиздание из CB не примет задание за правленное руками.

Протокол: без `--apply` — план (сколько и какие задания) в транзакции только
для чтения; с `--apply` — одна транзакция, после UPDATE пересчёт: у выбранных
курсов не осталось якорей `publish`, число ручных якорей не изменилось; при
расхождении — откат.

Запуск (DSN — `DATABASE_URL` из окружения или `.env`):

    python scripts/tsk996_clear_publish_anchors.py --courses 871,1165
    python scripts/tsk996_clear_publish_anchors.py --courses 871,1165 --apply
    python scripts/tsk996_clear_publish_anchors.py --all [--apply]

Код возврата: 0 — успех (в том числе «снимать нечего»), 1 — проверка после
записи не сошлась (откат), 2 — ошибка запуска.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("tsk996_clear_publish_anchors")

# Сколько заданий показать в плане поимённо (остальное — счётчиком).
SAMPLE_LIMIT = 50


def _parse_ids(raw: str) -> List[int]:
    """`871,1165 1200` → [871, 1165, 1200]."""
    return [int(x) for x in raw.replace(",", " ").split() if x.strip()]


def _scope_sql(course_ids: Optional[List[int]]) -> str:
    """Условие на курсы: список или все (`None`)."""
    return "TRUE" if course_ids is None else "t.course_id = ANY(:courses)"


async def clear_publish_anchors(
    conn: Any, course_ids: Optional[List[int]], *, apply: bool
) -> Dict[str, Any]:
    """План или снятие якорей публикатора у заданий курсов на открытом соединении.

    Транзакцией управляет вызывающий: при `apply=False` записи нет вовсе, при
    `apply=True` UPDATE выполняется и проверяется здесь же — несошедшаяся
    проверка поднимает RuntimeError, и вызывающий откатывает транзакцию.

    :param conn: AsyncConnection/AsyncSession SQLAlchemy.
    :param course_ids: id курсов; `None` — все курсы.
    :param apply: выполнить запись.
    :return: отчёт: сколько якорей `publish` найдено/снято, образец заданий,
        число ручных якорей до/после.
    :raises RuntimeError: проверка после записи не сошлась.
    """
    from sqlalchemy import text

    if course_ids is not None and not course_ids:
        raise ValueError("пустой список курсов: передай --courses или --all")
    scope = _scope_sql(course_ids)
    params: Dict[str, Any] = {} if course_ids is None else {"courses": course_ids}

    rows = (
        await conn.execute(
            text(
                "SELECT t.id, t.course_id, t.external_uid, t.anchor_material_id, "
                "       m.external_uid AS material_uid "
                "FROM tasks t LEFT JOIN materials m ON m.id = t.anchor_material_id "
                f"WHERE t.anchor_source = 'publish' AND {scope} "
                "ORDER BY t.course_id, t.id"
            ),
            params,
        )
    ).fetchall()
    manual_before = int(
        (
            await conn.execute(
                text(f"SELECT count(*) FROM tasks t WHERE t.anchor_source = 'manual' AND {scope}"),
                params,
            )
        ).scalar_one()
    )
    report: Dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "courses": course_ids if course_ids is not None else "all",
        "publish_anchors": len(rows),
        "by_course": {},
        "sample": [
            {
                "task_id": int(r[0]), "course_id": int(r[1]), "external_uid": r[2],
                "anchor_material_id": r[3], "material_uid": r[4],
            }
            for r in rows[:SAMPLE_LIMIT]
        ],
        "manual_anchors_untouched": manual_before,
        "cleared": 0,
    }
    for r in rows:
        key = str(int(r[1]))
        report["by_course"][key] = report["by_course"].get(key, 0) + 1
    if not apply or not rows:
        return report

    ids = [int(r[0]) for r in rows]
    result = await conn.execute(
        text(
            "UPDATE tasks SET anchor_material_id = NULL, anchor_source = NULL "
            "WHERE id = ANY(:ids) AND anchor_source = 'publish'"
        ),
        {"ids": ids},
    )
    cleared = int(result.rowcount or 0)
    left = int(
        (
            await conn.execute(
                text(f"SELECT count(*) FROM tasks t WHERE t.anchor_source = 'publish' AND {scope}"),
                params,
            )
        ).scalar_one()
    )
    manual_after = int(
        (
            await conn.execute(
                text(f"SELECT count(*) FROM tasks t WHERE t.anchor_source = 'manual' AND {scope}"),
                params,
            )
        ).scalar_one()
    )
    report["cleared"] = cleared
    if cleared != len(ids) or left != 0 or manual_after != manual_before:
        raise RuntimeError(
            f"проверка не сошлась: снято {cleared} из {len(ids)}, осталось publish {left}, "
            f"ручных якорей было {manual_before}, стало {manual_after}"
        )
    return report


async def run(dsn: str, course_ids: Optional[List[int]], apply: bool) -> Dict[str, Any]:
    """Подключиться, выполнить план/снятие в одной транзакции, вернуть отчёт."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                # Один снимок на план, UPDATE и проверку: параллельная публикация,
                # поставившая якорь после UPDATE, не даст ложного «не сошлось».
                await conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                if not apply:
                    await conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                report = await clear_publish_anchors(conn, course_ids, apply=apply)
            except Exception:
                await trans.rollback()
                raise
            if apply:
                await trans.commit()
            else:
                await trans.rollback()
    finally:
        await engine.dispose()
    report["db_host"] = urlsplit(dsn.replace("+asyncpg", "")).hostname
    return report


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: `--courses` или `--all`; без `--apply` — только план."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--courses", help="id курсов через запятую или пробел")
    target.add_argument("--all", action="store_true", help="все курсы")
    parser.add_argument(
        "--apply", action="store_true", help="выполнить запись (по умолчанию — только план)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="только план (так и по умолчанию; флаг для явности, сильнее --apply)",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL не задан (окружение или .env)", file=sys.stderr)
        return 2
    try:
        course_ids = None if args.all else _parse_ids(args.courses)
    except ValueError:
        print(f"--courses: ожидались числа, получено {args.courses!r}", file=sys.stderr)
        return 2
    if course_ids is not None and not course_ids:
        print("--courses: пустой список", file=sys.stderr)
        return 2
    apply = bool(args.apply) and not args.dry_run
    try:
        report = asyncio.run(run(dsn, course_ids, apply))
    except RuntimeError as e:
        logger.error("откат: %s", e)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
