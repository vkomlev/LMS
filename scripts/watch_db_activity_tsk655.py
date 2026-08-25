"""Улавливатель заторов на боевой базе LMS (tsk-655). Только чтение.

Что делает: раз в несколько секунд снимает `pg_stat_activity` и новые строки
`slow_request`, складывает снимки в файл и в конце показывает разбор — на чём
именно стояли соединения в момент затора.

Зачем именно так. Заторы 18.08 и 24.08 задевали одновременно экраны
преподавателя и экраны учеников, то есть упирались во что-то общее. Снимок
активности отвечает на главный вопрос ровно одной колонкой ``wait_event_type``:

* ``NULL`` у активных соединений — запросы реально работают, узкое место в
  мощности базы (процессор/диск), очереди на замках нет;
* ``Lock`` — соединения стоят в очереди за замком: искать, кто держит;
* ``LWLock`` / ``IO`` — упор во внутренние защёлки или диск;
* ``Client`` при ``state='idle in transaction'`` — приложение держит открытую
  транзакцию и не отпускает.

Запускать в известное окно затора: заторы воспроизводятся на ГРАНИЦЕ занятия,
когда ученики разом идут к следующему заданию. Расписание — таблица
`lesson_occurrence`; ключ ``--auto`` сам находит ближайшие занятия и караулит
их границы, ничего считать руками не нужно.

Примеры::

    # покараулить границы всех сегодняшних занятий (за 10 минут до конца
    # и 5 минут после), снимок каждые 2 секунды
    python scripts/watch_db_activity_tsk655.py --prod --auto

    # конкретное окно вручную, 15 минут
    python scripts/watch_db_activity_tsk655.py --prod --minutes 15

    # разобрать уже снятый файл, ничего не снимая
    python scripts/watch_db_activity_tsk655.py --report out/watch-2026-08-25.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tsk655-watch")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: За сколько минут ДО конца занятия начинать караулить и сколько минут ПОСЛЕ
#: продолжать. Затор случается ровно на границе, когда группа разом идёт
#: дальше, — окно взято с запасом в обе стороны.
_BEFORE_END_MINUTES = 10
_AFTER_END_MINUTES = 5

#: Снимок активности: всё, что нужно, чтобы отличить «работает» от «стоит в
#: очереди». `backend_type` отсекает служебные процессы самой базы.
_ACTIVITY_SQL = """
SELECT pid,
       state,
       wait_event_type,
       wait_event,
       backend_type,
       application_name,
       client_addr::text AS client_addr,
       EXTRACT(EPOCH FROM (now() - query_start))   AS query_age_s,
       EXTRACT(EPOCH FROM (now() - xact_start))    AS xact_age_s,
       EXTRACT(EPOCH FROM (now() - state_change))  AS state_age_s,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 240) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY query_start NULLS LAST
"""

_SLOW_SQL = """
SELECT id, ts, method, path, duration_ms, status_code, request_id
FROM slow_request
WHERE id > $1
ORDER BY id
"""

_SETTINGS_SQL = """
SELECT name, setting, unit FROM pg_settings
WHERE name IN ('max_connections', 'shared_buffers', 'work_mem',
               'effective_cache_size', 'max_parallel_workers')
ORDER BY name
"""


def _dsn_from_mcp(server: str) -> str:
    """Строка подключения из `.mcp.json`. Не логируется: в ней пароль."""
    config = json.loads((_REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    for arg in config["mcpServers"][server]["args"]:
        if arg.startswith("postgres"):
            return arg
    raise RuntimeError(f"в .mcp.json у сервера {server} нет строки подключения")


async def _plan_windows(
    conn: Any, horizon_hours: int, min_participants: int = 0,
) -> list[tuple[datetime, datetime, dict[str, Any]]]:
    """Окна караула вокруг границ ближайших занятий.

    ``min_participants`` отсекает мелкие группы. Считается по ЗАПИСАННЫМ
    участникам, а не по пришедшим, и это не небрежность: сводка преподавателя
    делает полный расчёт на каждую строку списка, включая `no_show`. Длина
    списка и есть цена панели, независимо от явки — на занятии 6294 из 12
    записанных пришли 5, а расчёт шёл на все 12.
    """
    rows = await conn.fetch(
        """
        SELECT lo.id, lo.scheduled_at, lo.duration_minutes,
               count(lop.id) AS participants
        FROM lesson_occurrence lo
        LEFT JOIN lesson_occurrence_participant lop ON lop.occurrence_id = lo.id
        WHERE lo.scheduled_at >= now() - interval '2 hours'
          AND lo.scheduled_at <= now() + make_interval(hours => $1)
        GROUP BY lo.id
        ORDER BY lo.scheduled_at
        """,
        horizon_hours,
    )
    windows: list[tuple[datetime, datetime, dict[str, Any]]] = []
    for row in rows:
        ends_at = row["scheduled_at"] + timedelta(minutes=int(row["duration_minutes"]))
        start = ends_at - timedelta(minutes=_BEFORE_END_MINUTES)
        stop = ends_at + timedelta(minutes=_AFTER_END_MINUTES)
        if stop <= datetime.now(timezone.utc):
            continue
        if int(row["participants"]) < min_participants:
            logger.info(
                "    пропускаю занятие %s: учеников %s, меньше порога %s",
                row["id"], row["participants"], min_participants,
            )
            continue
        windows.append((start, stop, {
            "occurrence_id": row["id"],
            "participants": int(row["participants"]),
            "ends_at": ends_at.isoformat(),
        }))
    return windows


async def _sample(conn: Any, out, last_slow_id: int, window: dict[str, Any]) -> int:
    """Один снимок: активность базы + новые строки журнала медленных запросов."""
    now = datetime.now(timezone.utc)
    activity = [dict(r) for r in await conn.fetch(_ACTIVITY_SQL)]
    slow_rows = [dict(r) for r in await conn.fetch(_SLOW_SQL, last_slow_id)]

    record = {
        "ts": now.isoformat(),
        "window": window,
        "activity": [
            {k: (v.isoformat() if isinstance(v, datetime) else float(v) if hasattr(v, "quantize") else v)
             for k, v in row.items()}
            for row in activity
        ],
        "new_slow_requests": [
            {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
            for row in slow_rows
        ],
    }
    out.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.flush()

    active = [r for r in activity if r["state"] == "active" and r["backend_type"] == "client backend"]
    idle_in_tx = [r for r in activity if r["state"] == "idle in transaction"]
    waiting = [r for r in active if r["wait_event_type"] not in (None, "Timeout")]
    oldest = max((float(r["query_age_s"] or 0) for r in active), default=0.0)

    if active or slow_rows:
        logger.info(
            "активных %2d | в очереди %2d | idle-in-tx %2d | старейший запрос %5.1f с%s",
            len(active),
            len(waiting),
            len(idle_in_tx),
            oldest,
            f" | НОВЫХ медленных: {len(slow_rows)}" if slow_rows else "",
        )
        for row in slow_rows:
            logger.info("    медленный: %s %s — %.1f с", row["method"], row["path"], row["duration_ms"] / 1000)
        if waiting:
            for row in waiting[:5]:
                logger.info(
                    "    ждёт %s/%s (%.1f с): %s",
                    row["wait_event_type"], row["wait_event"], float(row["query_age_s"] or 0), row["query"][:110],
                )

    if slow_rows:
        last_slow_id = max(int(r["id"]) for r in slow_rows)
    return last_slow_id


def report(path: Path) -> None:
    """Разбор снятого файла: на чём стояли соединения и когда было хуже всего."""
    peak: Optional[dict[str, Any]] = None
    peak_active = -1
    waits: Counter[str] = Counter()
    query_at_peak: Counter[str] = Counter()
    samples = 0
    slow_total = 0

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            record = json.loads(line)
            samples += 1
            slow_total += len(record.get("new_slow_requests", []))
            active = [
                r for r in record["activity"]
                if r["state"] == "active" and r["backend_type"] == "client backend"
            ]
            for row in active:
                waits[f"{row['wait_event_type'] or 'работает (без ожидания)'}/{row['wait_event'] or '-'}"] += 1
            if len(active) > peak_active:
                peak_active = len(active)
                peak = record
                query_at_peak = Counter(r["query"][:110] for r in active)

    logger.info("Снимков: %s, новых медленных запросов за прогон: %s", samples, slow_total)
    logger.info("Пик одновременной активности: %s соединений", peak_active)
    if peak:
        logger.info("  момент пика: %s (окно %s)", peak["ts"], peak.get("window"))
        for query, times in query_at_peak.most_common(8):
            logger.info("    %2d × %s", times, query)
    logger.info("На чём стояли соединения (все снимки, вид ожидания × раз):")
    for wait, times in waits.most_common(12):
        logger.info("    %5d × %s", times, wait)


async def main_async(args: argparse.Namespace) -> None:
    import asyncpg  # noqa: PLC0415
    from asyncpg.exceptions import InterfaceError  # noqa: PLC0415

    dsn = _dsn_from_mcp("learn_prod_db") if args.prod else args.dsn
    if not dsn:
        raise SystemExit("нужен --prod или --dsn")

    out_path = Path(args.out or (_REPO_ROOT / "out" / f"watch-tsk655-{datetime.now(timezone.utc):%Y%m%d-%H%M}.jsonl"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await asyncpg.connect(dsn, server_settings={"default_transaction_read_only": "on"})
    try:
        settings = await conn.fetch(_SETTINGS_SQL)
        logger.info("Боевая база, режим только чтение. Настройки:")
        for row in settings:
            logger.info("    %-22s %s %s", row["name"], row["setting"], row["unit"] or "")

        last_slow_id = int(await conn.fetchval("SELECT COALESCE(max(id), 0) FROM slow_request"))
        logger.info("Журнал медленных запросов: последняя строка id=%s", last_slow_id)

        if args.auto:
            windows = await _plan_windows(conn, args.horizon_hours, args.min_participants)
            if not windows:
                logger.info(
                    "Занятий в ближайшие %s ч не нашлось (порог по ученикам: %s) — "
                    "караулить нечего. Увеличьте --horizon-hours, снизьте "
                    "--min-participants или снимите окно вручную через --minutes.",
                    args.horizon_hours,
                    args.min_participants,
                )
                return
            logger.info("Окна караула (по границам занятий, время UTC):")
            for start, stop, meta in windows:
                logger.info(
                    "    занятие %s (%s учеников): с %s до %s",
                    meta["occurrence_id"], meta["participants"], start.strftime("%H:%M"), stop.strftime("%H:%M"),
                )
        else:
            start = datetime.now(timezone.utc)
            stop = start + timedelta(minutes=args.minutes)
            windows = [(start, stop, {"occurrence_id": None, "participants": None, "ends_at": None})]
            logger.info("Окно вручную: %s минут.", args.minutes)

        with out_path.open("w", encoding="utf-8") as out:
            for start, stop, meta in windows:
                wait_seconds = (start - datetime.now(timezone.utc)).total_seconds()
                if wait_seconds > 0:
                    logger.info(
                        "Жду начала окна занятия %s — %.0f мин.", meta["occurrence_id"], wait_seconds / 60,
                    )
                    # tsk-662: на время ожидания соединение ЗАКРЫВАЕМ и открываем
                    # заново перед окном. Прогон 25.08 пропал целиком именно на
                    # этом: программа ждала окна 55 минут с открытым праздным
                    # соединением, его закрыла та сторона, и первый же снимок
                    # упал с `connection is closed` — ноль снимков за прогон,
                    # окно занятия потеряно безвозвратно.
                    if wait_seconds > 120:
                        await conn.close()
                        await asyncio.sleep(wait_seconds)
                        conn = await asyncpg.connect(
                            dsn, server_settings={"default_transaction_read_only": "on"}
                        )
                        logger.info("Соединение открыто заново перед окном.")
                    else:
                        await asyncio.sleep(wait_seconds)
                logger.info("=== Караулю занятие %s до %s UTC ===", meta["occurrence_id"], stop.strftime("%H:%M"))
                while datetime.now(timezone.utc) < stop:
                    tick = time.perf_counter()
                    try:
                        last_slow_id = await _sample(conn, out, last_slow_id, meta)
                    except (OSError, asyncpg.PostgresConnectionError, InterfaceError) as exc:
                        # Обрыв ПОСРЕДИ окна не должен стоить остатка окна:
                        # переоткрываем и продолжаем со следующего снимка.
                        logger.warning("Соединение оборвалось (%s) — открываю заново.", exc)
                        try:
                            await conn.close()
                        except Exception:  # noqa: BLE001 — уже мёртвое соединение
                            pass
                        conn = await asyncpg.connect(
                            dsn, server_settings={"default_transaction_read_only": "on"}
                        )
                    await asyncio.sleep(max(0.0, args.interval - (time.perf_counter() - tick)))
    finally:
        await conn.close()

    logger.info("Снимки сохранены: %s", out_path)
    logger.info("")
    report(out_path)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Улавливатель заторов на боевой базе (tsk-655)")
    parser.add_argument("--prod", action="store_true", help="боевая база из .mcp.json (только чтение)")
    parser.add_argument("--dsn", default=None, help="явная строка подключения")
    parser.add_argument("--auto", action="store_true", help="караулить границы ближайших занятий")
    # Сутки, а не полсуток: типичный сценарий — запустить вечером и поймать
    # утреннее занятие следующего дня. С горизонтом в 12 часов такой запуск
    # молча не находил ничего и выглядел как «занятий нет».
    parser.add_argument("--horizon-hours", type=int, default=24, help="на сколько часов вперёд искать занятия")
    parser.add_argument(
        "--min-participants", type=int, default=0,
        help="караулить только занятия, где учеников не меньше этого числа",
    )
    parser.add_argument("--minutes", type=float, default=15, help="длина окна вручную (без --auto)")
    parser.add_argument("--interval", type=float, default=2.0, help="секунд между снимками")
    parser.add_argument("--out", default=None, help="куда писать снимки (.jsonl)")
    parser.add_argument("--report", default=None, help="только разобрать уже снятый файл")
    return parser.parse_args(argv)


if __name__ == "__main__":
    parsed = parse_args()
    if parsed.report:
        report(Path(parsed.report))
    else:
        asyncio.run(main_async(parsed))
