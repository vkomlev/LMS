"""Замер стоимости `GET /teacher/lesson-occurrences/{id}/summary` (tsk-655).

Строго ЧТЕНИЕ. Прогоняет настоящий код сервиса
``teacher_lesson_summary_service.get_occurrence_summary`` против указанной базы
и разделяет затраченное время на две части:

* время в базе — сумма длительностей всех SQL-запросов, посчитанная
  слушателями SQLAlchemy ``before_cursor_execute``/``after_cursor_execute``;
* время в Python — остаток от полного времени вызова.

Зачем: заторы 18.08 и 24.08 задевали одновременно экраны преподавателя и
экраны учеников, то есть упирались в общий ресурс. Если сводка почти всё время
проводит в Python между запросами — она держит цикл событий и объясняет, почему
встают даже лёгкие чужие запросы. Если почти всё время в базе — объяснение надо
искать в другом месте (пул подключений, замки).

Соединение открывается с ``default_transaction_read_only=on``: запись
невозможна на уровне сервера, даже по ошибке.

Осторожно с моментом запуска: один прогон сводки на группу из 12 человек — это
около тысячи запросов к боевой базе, а замер `last-position` на ученика — до
полутора тысяч. Нагрузка небольшая, но на границе занятия, когда база и так под
залпом, добавлять к ней замер не надо. Запускать в спокойное время.

Запуск (боевая база берётся из `.mcp.json`, в вывод строка подключения не
попадает):

    python scripts/measure_summary_cost_tsk655.py --prod --occurrence 6258 6294
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk655")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Настройки приложения требуют DATABASE_URL уже на импорте модулей сервиса.
# Локальный `.env` смотрит на dev-базу и нужен только чтобы импорт прошёл:
# замер идёт через СВОЙ движок, созданный ниже, а не через движок приложения.
try:
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:  # pragma: no cover — на проде dotenv не обязателен
    pass


def _dsn_from_mcp(server: str) -> str:
    """Строка подключения из `.mcp.json` по имени MCP-сервера.

    Возвращается как есть и НИКОГДА не логируется: в ней пароль.
    """
    config = json.loads((_REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    args = config["mcpServers"][server]["args"]
    for arg in args:
        if arg.startswith("postgres"):
            return arg
    raise RuntimeError(f"в .mcp.json у сервера {server} нет строки подключения")


def _to_async_dsn(dsn: str) -> str:
    """Драйвер asyncpg вместо синхронного psycopg."""
    if dsn.startswith("postgresql+"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


class QueryMeter:
    """Счётчик SQL-запросов и суммарного времени в базе."""

    def __init__(self) -> None:
        self.count: int = 0
        self.total_seconds: float = 0.0
        self.by_statement: Counter[str] = Counter()
        self.seconds_by_statement: dict[str, float] = {}
        self._started_at: float = 0.0

    def attach(self, sync_engine: Any) -> None:
        event.listen(sync_engine, "before_cursor_execute", self._before)
        event.listen(sync_engine, "after_cursor_execute", self._after)

    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001, D401
        self._started_at = time.perf_counter()

    def _after(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001, D401
        elapsed = time.perf_counter() - self._started_at
        self.count += 1
        self.total_seconds += elapsed
        key = " ".join(statement.split())[:110]
        self.by_statement[key] += 1
        self.seconds_by_statement[key] = self.seconds_by_statement.get(key, 0.0) + elapsed

    def reset(self) -> None:
        self.count = 0
        self.total_seconds = 0.0
        self.by_statement.clear()
        self.seconds_by_statement.clear()


async def measure_one(
    session_factory: async_sessionmaker[AsyncSession],
    meter: QueryMeter,
    *,
    occurrence_id: int,
    teacher_id: int,
    top: int,
    include_progress: bool = True,
) -> dict[str, Any]:
    """Один прогон сводки: сколько запросов, сколько времени где.

    `include_progress=False` (tsk-665) — режим списка строк: прогресс по курсу
    и заблокированные задания не считаются, их запрашивают по клику на
    ученика. Нужен, чтобы мерить оба режима одним и тем же счётчиком.
    """
    # Импорт внутри функции: модули приложения тянут настройки, а скрипт
    # должен уметь стартовать и без них до разбора аргументов.
    from app.auth.current_user import CurrentUser  # noqa: PLC0415
    from app.services import teacher_lesson_summary_service  # noqa: PLC0415

    meter.reset()
    started = time.perf_counter()
    async with session_factory() as session:
        data = await teacher_lesson_summary_service.get_occurrence_summary(
            session,
            occurrence_id=occurrence_id,
            teacher_id=teacher_id,
            current_user=CurrentUser(id=teacher_id),
            no_show_threshold_minutes=15,
            include_progress=include_progress,
        )
    wall = time.perf_counter() - started

    participants = len(data["participants"])
    db_seconds = meter.total_seconds
    result = {
        "occurrence_id": occurrence_id,
        "participants": participants,
        "queries": meter.count,
        "wall_seconds": round(wall, 2),
        "db_seconds": round(db_seconds, 2),
        "python_seconds": round(wall - db_seconds, 2),
        "queries_per_participant": round(meter.count / participants, 1) if participants else 0,
    }

    logger.info(
        "занятие %s | участников %s | запросов %s (%s на участника) | "
        "всего %.2f с = база %.2f с + Python %.2f с",
        occurrence_id,
        participants,
        meter.count,
        result["queries_per_participant"],
        wall,
        db_seconds,
        wall - db_seconds,
    )
    if top:
        logger.info("  самые частые запросы:")
        for statement, times in meter.by_statement.most_common(top):
            logger.info(
                "    %4d раз | %6.2f с | %s",
                times,
                meter.seconds_by_statement[statement],
                statement,
            )
    return result


async def measure_last_position(
    session_factory: async_sessionmaker[AsyncSession],
    meter: QueryMeter,
    *,
    student_id: int,
    top: int = 0,
) -> dict[str, Any]:
    """Один прогон `GET /me/last-position` — сколько запросов он стоит.

    Соединение открыто только на чтение. Если вызов попробует записать
    (движок обновляет кеш `student_course_state`), сервер откажет — и это
    само по себе улика: путь, который выглядит чтением, пишет в базу.
    """
    from app.services import me_service  # noqa: PLC0415

    meter.reset()
    started = time.perf_counter()
    write_attempt = False
    try:
        async with session_factory() as session:
            await me_service.get_last_position(session, student_id)
    except Exception as exc:  # noqa: BLE001 — важен сам факт и текст отказа
        if "read-only" in str(exc):
            write_attempt = True
        else:
            raise
    wall = time.perf_counter() - started

    logger.info(
        "ученик %s | last-position: запросов %s | всего %.2f с = база %.2f с + Python %.2f с%s",
        student_id,
        meter.count,
        wall,
        meter.total_seconds,
        wall - meter.total_seconds,
        " | ПЫТАЛСЯ ЗАПИСАТЬ" if write_attempt else "",
    )
    if top:
        logger.info("  самые частые запросы:")
        for statement, times in meter.by_statement.most_common(top):
            logger.info("    %4d раз | %6.2f с | %s", times, meter.seconds_by_statement[statement], statement)
    return {
        "student_id": student_id,
        "queries": meter.count,
        "wall_seconds": round(wall, 2),
        "db_seconds": round(meter.total_seconds, 2),
        "write_attempt": write_attempt,
    }


async def main_async(args: argparse.Namespace) -> None:
    if args.prod:
        dsn = _dsn_from_mcp("learn_prod_db")
        target = "БОЕВАЯ база (learn_prod_db)"
    elif args.dsn:
        dsn = args.dsn
        target = "строка подключения из аргумента"
    else:
        dsn = os.environ["DATABASE_URL"]
        target = "DATABASE_URL из окружения"

    if not args.occurrence and not args.last_position:
        raise SystemExit(
            "нечего мерить: укажите --occurrence <ID занятий> и/или --last-position <ID учеников>"
        )

    logger.info("Цель замера: %s. Режим: только чтение.", target)

    engine = create_async_engine(
        _to_async_dsn(dsn),
        echo=False,
        pool_size=5,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    meter = QueryMeter()
    meter.attach(engine.sync_engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    results: list[dict[str, Any]] = []
    try:
        for student_id in args.last_position:
            results.append(
                await measure_last_position(session_factory, meter, student_id=student_id, top=args.top)
            )
        for occurrence_id in args.occurrence:
            results.append(
                await measure_one(
                    session_factory,
                    meter,
                    occurrence_id=occurrence_id,
                    teacher_id=args.teacher_id,
                    top=args.top,
                    include_progress=not args.no_progress,
                )
            )
    finally:
        await engine.dispose()

    if len(results) >= 2:
        logger.info("")
        logger.info("Как растёт стоимость с числом участников:")
        for row in sorted(results, key=lambda r: r["participants"]):
            logger.info(
                "  участников %2d -> запросов %4d, всего %5.2f с (база %5.2f, Python %5.2f)",
                row["participants"],
                row["queries"],
                row["wall_seconds"],
                row["db_seconds"],
                row["python_seconds"],
            )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("Итог сохранён: %s", args.json_out)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Замер стоимости сводки занятия (tsk-655)")
    parser.add_argument("--prod", action="store_true", help="боевая база из .mcp.json (только чтение)")
    parser.add_argument("--dsn", default=None, help="явная строка подключения")
    parser.add_argument("--occurrence", type=int, nargs="*", default=[], help="ID занятий для сводки")
    parser.add_argument(
        "--last-position", type=int, nargs="*", default=[], help="ID учеников для замера /me/last-position",
    )
    parser.add_argument("--teacher-id", type=int, default=2, help="ID преподавателя (по умолчанию 2)")
    parser.add_argument("--top", type=int, default=8, help="сколько самых частых запросов показать")
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="сводка в режиме списка строк: без прогресса по курсу и заблокированных (tsk-665)",
    )
    parser.add_argument("--json-out", default=None, help="куда сохранить итог в JSON")
    return parser.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
