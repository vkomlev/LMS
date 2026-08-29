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

Караул по расписанию НА САМОМ сервере (tsk-735). Машина оператора для этого не
годится: занятия идут утром в будни, а ноутбук в это время может спать, и
пропущенное окно не переснять. Поэтому таймер systemd на боевой машине, а
строка подключения — из окружения (`WATCH_DB_DSN`), не ключом: пароль в
командной строке виден любому пользователю машины через `ps`. Юнит и таймер —
`deploy/vps/lms-watch-tsk655.service` и `.timer`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter, deque
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
#:
#: tsk-662 (25.08): хвост в 5 минут ОКАЗАЛСЯ КОРОТОК и стоил всего прогона.
#: Занятие 6832 кончилось в 08:00 UTC, окно закрылось в 08:05 — и сняло
#: тишину: пик в одно соединение, один медленный вызов. А настоящий затор
#: случился в 08:06:39-08:07:12, то есть ЧЕРЕЗ ДВЕ МИНУТЫ после закрытия
#: окна: 21 медленный запрос, худший 44,2 с. Ученики расходятся с занятия не
#: по звонку — часть открывает кабинет через несколько минут после конца.
#: Поэтому хвост 20 минут, а не 5: снять тишину рядом с событием и написать
#: «затора не было» — худший исход замера, он закрывает вопрос неверно.
_BEFORE_END_MINUTES = 10
_AFTER_END_MINUTES = 20

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


async def _take_sample(
    conn: Any, last_slow_id: int, window: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Снять один снимок, НЕ записывая его. Возвращает (запись, новый last_slow_id).

    Отделено от записи ради режима ловушки (tsk-655): там снимки сперва
    копятся в памяти и попадают в файл только вокруг события.
    """
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
    if slow_rows:
        last_slow_id = max(int(r["id"]) for r in slow_rows)
    return record, last_slow_id


def _write(out, record: dict[str, Any]) -> None:
    """Записать снимок в файл."""
    out.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.flush()


async def _sample(conn: Any, out, last_slow_id: int, window: dict[str, Any]) -> int:
    """Один снимок: активность базы + новые строки журнала медленных запросов."""
    record, last_slow_id = await _take_sample(conn, last_slow_id, window)
    _write(out, record)
    activity = record["activity"]
    slow_rows = record["new_slow_requests"]

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

    return last_slow_id


#: Подпись веера дерева курса в `pg_stat_activity` (tsk-655, замер 25.08).
#: Страница дерева выпускает пачку запросов `tasks/by-course/{id}?limit=500`
#: и `courses/{id}/materials?limit=500`; в базе это выборки по `course_id`.
#: Список — не догадка: снят с боевого лога 09:52:34, где один клиент за
#: полторы секунды запросил 11 курсов подряд.
_FAN_PATTERNS = ("FROM TASKS WHERE TASKS.COURSE_ID", "FROM MATERIALS WHERE MATERIALS.COURSE_ID")


def _detect_triggers(
    record: dict[str, Any],
    *,
    fan_size: int,
    busy_backends: int,
    busy_age: float,
) -> list[str]:
    """Что в этом снимке стоит внимания. Пустой список — обычная минута.

    Четыре ловушки, все сняты с боевых заторов 24-25.08, а не придуманы:

    * `веер` — залп страницы дерева курса. Это СПУСКОВОЙ КРЮЧОК: 25.08 оба
      затора начинались с него, и оба раза он сработал ВНЕ границы занятия
      (в 08:06 — через 6 минут после конца, в 09:52 — через 52). Караул по
      границам такое не ловит никаким хвостом.
    * `затор` — несколько запросов разом висят дольше порога. Это само
      событие, пока оно идёт.
    * `замок` — очередь за строкой (25.08: семеро на `UPDATE user_session`).
    * `медленный` — новая строка журнала. Самая надёжная ловушка и самая
      поздняя: журнал пишется ПОСЛЕ запроса, поэтому одной её мало —
      интересные секунды к этому моменту уже прошли, и их спасает только
      буфер «до события».
    """
    active = [
        r for r in record["activity"]
        if r["state"] == "active" and r["backend_type"] == "client backend"
    ]
    reasons: list[str] = []

    fan = sum(
        1 for r in active
        if any(p in " ".join((r["query"] or "").split()).upper() for p in _FAN_PATTERNS)
    )
    if fan >= fan_size:
        reasons.append(f"веер дерева курса ({fan} запросов разом)")

    slow_now = [r for r in active if float(r["query_age_s"] or 0) >= busy_age]
    if len(slow_now) >= busy_backends:
        oldest = max(float(r["query_age_s"] or 0) for r in slow_now)
        reasons.append(f"затор ({len(slow_now)} запросов дольше {busy_age:.0f} с, старейший {oldest:.1f} с)")

    locked = [r for r in active if r["wait_event_type"] == "Lock"]
    if len(locked) >= 2:
        reasons.append(f"очередь за замком ({len(locked)} соединений)")

    if record["new_slow_requests"]:
        paths = ", ".join(
            f"{r['path']} {r['duration_ms'] / 1000:.1f} с" for r in record["new_slow_requests"][:3]
        )
        reasons.append(f"журнал медленных: {paths}")

    return reasons


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
            if not line.strip():
                continue
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

    if samples == 0:
        # Пустой файл — законный исход режима ловушки: событий не было, писать
        # нечего. Раньше разбор печатал тут «пик активности: -1 соединений» —
        # бессмыслицу, по которой инструмент перестают читать. И это ЕЩЁ и
        # опасно: 25.08 пустой файл означал мёртвый прогон, а не тишину,
        # поэтому два состояния обязаны различаться словами.
        logger.info(
            "Снимков в файле нет. В режиме ловушки это норма — событий не "
            "случилось. В режиме окон пустой файл означает, что прогон умер: "
            "сверьтесь с журналом запуска."
        )
        return

    logger.info("Снимков: %s, новых медленных запросов за прогон: %s", samples, slow_total)
    logger.info("Пик одновременной активности: %s соединений", peak_active)
    if peak:
        logger.info("  момент пика: %s (окно %s)", peak["ts"], peak.get("window"))
        for query, times in query_at_peak.most_common(8):
            logger.info("    %2d × %s", times, query)
    logger.info("На чём стояли соединения (все снимки, вид ожидания × раз):")
    for wait, times in waits.most_common(12):
        logger.info("    %5d × %s", times, wait)


async def _watch_with_trigger(conn: Any, dsn: str, out, last_slow_id: int, args: Any) -> Any:
    """Непрерывный караул с ловушкой: пишем в файл только вокруг событий.

    tsk-655, переделка после разбора 25.08. Прежний караул сторожил ГРАНИЦЫ
    занятий — и промахнулся дважды подряд, потому что сторожил не то: оба
    затора дня начались с веера страницы дерева курса, и второй случился в
    52 минутах от ближайшей границы. Крючок — не время, а действие ученика,
    и ловить надо его.

    Устройство. Снимки идут непрерывно, но в файл попадают не все: последние
    `--buffer-seconds` держатся в памяти кольцом. Сработала ловушка — кольцо
    выгружается целиком (это и есть секунды ДО события, ради которых всё) и
    дальше пишется всё подряд, пока не пройдёт `--after-seconds` без новых
    срабатываний. Так за восьмичасовой караул на диск ложатся минуты вокруг
    событий, а не восемь часов тишины.

    Возвращает обновлённый `conn`: соединение могло смениться после обрыва.
    """
    import asyncpg  # noqa: PLC0415 — как и в `main_async`, импорт поздний
    from asyncpg.exceptions import InterfaceError  # noqa: PLC0415

    ends_at = datetime.now(timezone.utc) + timedelta(hours=args.hours)
    buffer_len = max(1, int(args.buffer_seconds / max(args.interval, 0.1)))
    ring: deque = deque(maxlen=buffer_len)
    recording_until: Optional[datetime] = None
    events = 0
    written = 0
    next_heartbeat = datetime.now(timezone.utc) + timedelta(minutes=args.heartbeat_minutes)

    logger.info(
        "Ловушка: караулю %.1f ч, снимок раз в %.1f с, буфер до события %.0f с, "
        "запись после события %.0f с.",
        args.hours, args.interval, args.buffer_seconds, args.after_seconds,
    )
    logger.info("Пишу в файл ТОЛЬКО вокруг событий; тишина на диск не идёт.")

    while datetime.now(timezone.utc) < ends_at:
        tick = time.perf_counter()
        try:
            record, last_slow_id = await _take_sample(
                conn, last_slow_id, {"mode": "trigger", "phase": "idle"}
            )
        except (OSError, asyncpg.PostgresConnectionError, InterfaceError) as exc:
            logger.warning("Соединение оборвалось (%s) — открываю заново.", exc)
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 — уже мёртвое соединение
                pass
            conn = await asyncpg.connect(dsn, server_settings={"default_transaction_read_only": "on"})
            continue

        reasons = _detect_triggers(
            record,
            fan_size=args.fan_size,
            busy_backends=args.busy_backends,
            busy_age=args.busy_age,
        )
        now = datetime.now(timezone.utc)

        if reasons:
            events += 1
            recording_until = now + timedelta(seconds=args.after_seconds)
            logger.info("=== СОБЫТИЕ %s: %s ===", events, "; ".join(reasons))
            if ring:
                # Кольцо выгружается ЦЕЛИКОМ и помечается `pre`: это секунды
                # до события, которых у прежнего караула не было вовсе.
                for buffered in ring:
                    buffered["window"] = {"mode": "trigger", "phase": "pre", "event": events}
                    _write(out, buffered)
                written += len(ring)
                logger.info("    выгружено %s снимков ДО события", len(ring))
                ring.clear()

        if recording_until is not None and now <= recording_until:
            record["window"] = {
                "mode": "trigger", "phase": "live", "event": events,
                "reasons": reasons or None,
            }
            _write(out, record)
            written += 1
            active = [
                r for r in record["activity"]
                if r["state"] == "active" and r["backend_type"] == "client backend"
            ]
            if active:
                oldest = max(float(r["query_age_s"] or 0) for r in active)
                waiting = [r for r in active if r["wait_event_type"] not in (None, "Timeout")]
                logger.info(
                    "    активных %2d | в очереди %2d | старейший %5.1f с",
                    len(active), len(waiting), oldest,
                )
        else:
            if recording_until is not None:
                logger.info("    событие %s закрыто, возвращаюсь в тишину", events)
                recording_until = None
            ring.append(record)

        if now >= next_heartbeat:
            # Своя же наука 25.08: молчащий караул неотличим от мёртвого.
            logger.info(
                "жив: событий %s, снимков на диске %s, в буфере %s, до конца %.1f ч",
                events, written, len(ring), (ends_at - now).total_seconds() / 3600,
            )
            next_heartbeat = now + timedelta(minutes=args.heartbeat_minutes)

        await asyncio.sleep(max(0.0, args.interval - (time.perf_counter() - tick)))

    logger.info("Караул окончен: событий %s, снимков на диске %s.", events, written)
    if events == 0:
        logger.info(
            "Ни одна ловушка не сработала. Это значит «спусковой крючок не "
            "случался», а НЕ «стало хорошо»: без веера дерева курса заторов "
            "не бывает и на старом коде."
        )
    return conn


#: Насколько широкую полосу вокруг окна проверять на «событие рядом, а не внутри».
_MISS_BAND_MINUTES = 30


async def _warn_if_event_missed(conn: Any, windows: list) -> None:
    """Сказать вслух, если затор случился РЯДОМ с окном, а не внутри него.

    Сторож на ту самую ошибку, которая 25.08 стоила замера: занятие кончилось
    в 08:00 UTC, окно закрылось в 08:05 и сняло тишину — а затор пришёл в
    08:06:39, через две минуты после закрытия. Разбор чуть не закончился
    выводом «затора не было»; поймал его человек, сверивший журнал руками.

    Проверка сравнивает снятое окно с журналом медленных запросов в полосе
    ±30 минут. Молчание тут значит «рядом тоже тихо», а не «мы не смотрели» —
    ровно та разница, которой не хватало.

    Ошибка самой проверки не должна ронять прогон: снимки уже на диске, они
    ценнее сторожа.
    """
    try:
        for start, stop, meta in windows:
            rows = await conn.fetch(
                """
                SELECT ts, path, duration_ms
                FROM slow_request
                WHERE ts >= $1::timestamptz - make_interval(mins => $3)
                  AND ts <= $2::timestamptz + make_interval(mins => $3)
                  AND (ts < $1::timestamptz OR ts > $2::timestamptz)
                ORDER BY ts
                """,
                start, stop, _MISS_BAND_MINUTES,
            )
            if not rows:
                continue
            logger.warning(
                "ВНИМАНИЕ: у занятия %s медленные запросы есть РЯДОМ с окном "
                "(%s-%s UTC), но ВНЕ него — %s штук за ±%s мин. Окно взято не "
                "туда: «затора не было» тут сказать нельзя.",
                meta["occurrence_id"], start.strftime("%H:%M"), stop.strftime("%H:%M"),
                len(rows), _MISS_BAND_MINUTES,
            )
            for row in rows[:10]:
                logger.warning(
                    "    %s %s — %.1f с",
                    row["ts"].strftime("%H:%M:%S"), row["path"], row["duration_ms"] / 1000,
                )
            if len(rows) > 10:
                logger.warning("    ... и ещё %s", len(rows) - 10)
    except Exception as exc:  # noqa: BLE001 — сторож не важнее снимков
        logger.warning("Проверку «событие рядом с окном» выполнить не удалось: %s", exc)


async def main_async(args: argparse.Namespace) -> None:
    import asyncpg  # noqa: PLC0415
    from asyncpg.exceptions import InterfaceError  # noqa: PLC0415

    # tsk-735: третий источник — переменная окружения. Нужен для караула по
    # расписанию НА САМОМ сервере: `.mcp.json` там нет, а передать строку
    # ключом `--dsn` нельзя — пароль осел бы в списке процессов, видимом любому
    # пользователю машины. Порядок источников: явный ключ, потом `.mcp.json`,
    # потом окружение.
    dsn = args.dsn or (_dsn_from_mcp("learn_prod_db") if args.prod else None) or os.getenv("WATCH_DB_DSN")
    if not dsn:
        raise SystemExit("нужен --prod, --dsn или переменная окружения WATCH_DB_DSN")
    # `DATABASE_URL` приложения записан в диалекте SQLAlchemy
    # (`postgresql+asyncpg://`), а asyncpg такую схему не понимает и падает.
    # Срезаем драйвер, чтобы строку из `.env` можно было подать как есть.
    if "+" in dsn.split("://", 1)[0]:
        dsn = dsn.split("+", 1)[0] + "://" + dsn.split("://", 1)[1]

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

        if args.trigger:
            # Режим ловушки: караулим не время, а действие (tsk-655, 25.08).
            with out_path.open("w", encoding="utf-8") as out:
                conn = await _watch_with_trigger(conn, dsn, out, last_slow_id, args)
            logger.info("Снимки сохранены: %s", out_path)
            logger.info("")
            report(out_path)
            return

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

        await _warn_if_event_missed(conn, windows)
    finally:
        await conn.close()

    logger.info("Снимки сохранены: %s", out_path)
    logger.info("")
    report(out_path)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Улавливатель заторов на боевой базе (tsk-655)")
    parser.add_argument("--prod", action="store_true", help="боевая база из .mcp.json (только чтение)")
    parser.add_argument(
        "--dsn", default=None,
        help="явная строка подключения; без неё и без --prod берётся WATCH_DB_DSN "
             "из окружения (так караул по расписанию не светит пароль в списке процессов)",
    )
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
    parser.add_argument(
        "--trigger",
        action="store_true",
        help=(
            "караулить не границу занятия, а СОБЫТИЕ: веер дерева курса, "
            "затор, очередь за замком, новую строку журнала (tsk-655)"
        ),
    )
    parser.add_argument("--hours", type=float, default=8.0, help="сколько часов караулить в режиме ловушки")
    parser.add_argument(
        "--buffer-seconds", type=float, default=120.0,
        help="сколько секунд ДО события держать в памяти и выгрузить при срабатывании",
    )
    parser.add_argument(
        "--after-seconds", type=float, default=180.0,
        help="сколько секунд писать после события, если ловушки молчат",
    )
    parser.add_argument(
        "--fan-size", type=int, default=4,
        help="сколько одновременных запросов по course_id считать веером дерева курса",
    )
    parser.add_argument(
        "--busy-backends", type=int, default=3,
        help="сколько одновременно висящих запросов считать затором",
    )
    parser.add_argument(
        "--busy-age", type=float, default=2.0,
        help="с какого возраста запрос считается висящим (секунды)",
    )
    parser.add_argument(
        "--heartbeat-minutes", type=float, default=5.0,
        help="как часто говорить, что караул жив (молчащий караул неотличим от мёртвого)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    parsed = parse_args()
    if parsed.report:
        report(Path(parsed.report))
    else:
        asyncio.run(main_async(parsed))
