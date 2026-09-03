# -*- coding: utf-8 -*-
"""Регулярный чек: сколько запросов ждали дольше порога (tsk-644).

Зачем. 18 августа в окне 12:11–12:25 приём ответа занял 123,5 с: ребёнок нажал
«Ответить» и две минуты смотрел в экран. Ошибок не было, жалоб не было, пул не
исчерпывался — день прошёл как обычный, и узнали мы о нём через четыре дня и
случайно. Порог медленного запроса (3 с) с tsk-621 пишется в `logs/app.log`, но
в лог никто не смотрит, а с машины оператора его и не видно: еженедельные чеки
достают до прода только подключением к БД.

Поэтому tsk-644 завёл таблицу `slow_request` — приложение пишет туда строку,
когда запрос превысил порог, — а этот чек раз в неделю превращает её в сводку.

Что показывает:
  * сколько медленных запросов за окно и в скольких разных минутах они шли
    (49 запросов в одном 14-минутном окне и 49 запросов, размазанных по неделе, —
    это две разные истории: авария и обычный фон);
  * худшие обработчики: сколько раз и какая была худшая задержка;
  * худшие отдельные запросы — с ними идти в `logs/app.log` по `request_id`.

Красный свет (код 1) — не «есть медленные запросы» вовсе: единичный тяжёлый
отчёт преподавателя за 4 с нормален и будить оператора не должен. Красным
считается то, чего ученик простить не может: хотя бы один запрос дольше
`SLOW_ALERT_SECONDS` (по умолчанию 30 с) ЛИБО пачка от `SLOW_ALERT_BURST`
(20) запросов в пределах одного часа — признак того самого общего затора.

**Красным — только по свежим суткам** (`SLOW_ALERT_FRESH_DAYS`, по умолчанию 2),
а не по всему недельному окну (tsk-779). Иначе один вылеченный затор поднимает
тревогу ещё неделю, пока не выпадет из окна: затор 29.08 починили в тот же день,
а сводка звала на разбор до 03.09 — и он ушёл в работу вторично, как живая
проблема. Старые находки не прячутся: они остаются в отчёте разбивкой по дням
с пометкой «затор был, но прошёл», просто не выглядят пожаром.

Read-only: ни одного UPDATE.

Куда смотрит. В базу из `DATABASE_URL`; по умолчанию это dev (прод от скриптов
закрыт, tsk-246). Прод — явным override:
    DATABASE_URL=<прод-dsn> python scripts/check_slow_requests.py

Под планировщиком чек идёт через общий вход ``scripts/weekly_checks.py
slow-requests`` — он подставляет боевой DSN и пишет журнал
``logs/slow_requests_check.log``.

Коды выхода: 0 — тихо; 1 — есть находки; 2 — ошибка выполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# tsk-641: под планировщиком консоли нет — см. пояснение в check_ungradable_tasks.py.
if sys.platform == "win32" and not os.environ.get("LMS_CHECK_NO_CONSOLE"):
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

WINDOW_DAYS = int(os.getenv("SLOW_CHECK_WINDOW_DAYS", "7"))
ALERT_SECONDS = float(os.getenv("SLOW_ALERT_SECONDS", "30"))
ALERT_BURST = int(os.getenv("SLOW_ALERT_BURST", "20"))
# tsk-779: сколько последних суток считать «сейчас». Тревога поднимается только по
# ним — см. пояснение у SQL_BY_DAY.
FRESH_DAYS = int(os.getenv("SLOW_ALERT_FRESH_DAYS", "2"))

SQL_TOTALS = """
SELECT count(*)                                   AS n,
       count(DISTINCT date_trunc('minute', ts))   AS minutes,
       max(duration_ms)                           AS worst_ms,
       min(ts)                                    AS first_ts,
       max(ts)                                    AS last_ts
FROM slow_request
WHERE ts >= now() - make_interval(days => :days)
"""

SQL_BY_PATH = """
SELECT method, path, count(*) AS n, max(duration_ms) AS worst_ms
FROM slow_request
WHERE ts >= now() - make_interval(days => :days)
GROUP BY method, path
ORDER BY max(duration_ms) DESC, count(*) DESC
LIMIT 10
"""

SQL_WORST = """
SELECT ts, method, path, duration_ms, status_code, request_id
FROM slow_request
WHERE ts >= now() - make_interval(days => :days)
ORDER BY duration_ms DESC
LIMIT 10
"""

# Самый плотный час окна: затор виден именно кучностью, а не суммой за неделю.
SQL_BURST = """
SELECT date_trunc('hour', ts) AS hour, count(*) AS n, max(duration_ms) AS worst_ms
FROM slow_request
WHERE ts >= now() - make_interval(days => :days)
GROUP BY 1
ORDER BY count(*) DESC
LIMIT 1
"""

# tsk-779: разбивка по дням и отдельный счёт по свежим суткам.
#
# Зачем. Окно чека — неделя, и до этой правки вердикт считался по всему окну
# сразу. Значит один разобранный и ВЫЛЕЧЕННЫЙ затор кричал «ЭТО ЗАТОР» ещё семь
# дней подряд, пока не выпадет из окна. Ровно это и случилось: затор 29.08
# (движок ходил в файловое хранилище на каждом узле дерева) вылечили в тот же
# день, а сводка повторяла его до 03.09 — и он ушёл на разбор во второй раз,
# как живая проблема. Разбор занял часы и кончился выводом «уже починено».
#
# Поэтому теперь: тревога (код 1) — только если признаки затора есть в СВЕЖИХ
# сутках; всё, что старше, показывается историей и код выхода не поднимает.
# Сигнал при этом не теряется — старая находка остаётся на виду, просто перестаёт
# выглядеть пожаром.
SQL_BY_DAY = """
SELECT (ts AT TIME ZONE 'UTC')::date AS day,
       count(*) AS n,
       max(duration_ms) AS worst_ms
FROM slow_request
WHERE ts >= now() - make_interval(days => :days)
GROUP BY 1
ORDER BY 1
"""

SQL_FRESH = """
SELECT count(*) AS n,
       coalesce(max(duration_ms), 0) AS worst_ms,
       (SELECT coalesce(max(cnt), 0) FROM (
            SELECT count(*) AS cnt FROM slow_request
            WHERE ts >= now() - make_interval(days => :fresh_days)
            GROUP BY date_trunc('hour', ts)
        ) h) AS worst_hour_n
FROM slow_request
WHERE ts >= now() - make_interval(days => :fresh_days)
"""


def verdict(
    worst_sec: float,
    burst_n: int,
    fresh_worst_sec: float,
    fresh_hour_n: int,
) -> str:
    """Что за находка: пожар, отголосок вылеченного или обычный фон (tsk-779).

    Вынесено из ``main`` отдельной функцией не ради красоты: именно это решение
    определяет, поднимет ли чек тревогу в понедельничной сводке, и до tsk-779
    оно было неверным целую неделю подряд. Проверять его тестом дешевле, чем
    ждать следующего заторного утра.

    :param worst_sec: худшая задержка за всё окно чека, секунды.
    :param burst_n: размер самой плотной пачки за всё окно (запросов в час).
    :param fresh_worst_sec: худшая задержка за свежие сутки, секунды.
    :param fresh_hour_n: самая плотная пачка за свежие сутки.
    :returns: ``"active"`` — затор идёт сейчас (красный свет);
        ``"resolved"`` — затор в окне был, но свежие сутки чистые;
        ``"background"`` — порог не превышен нигде.
    """
    if fresh_worst_sec >= ALERT_SECONDS or fresh_hour_n >= ALERT_BURST:
        return "active"
    if worst_sec >= ALERT_SECONDS or burst_n >= ALERT_BURST:
        return "resolved"
    return "background"


async def main(quiet: bool = False) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL (ни в окружении, ни в .env)", file=sys.stderr)
        return 2
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            where = (await conn.execute(text(
                "SELECT current_database() AS db, inet_server_addr()::text AS host"
            ))).mappings().first()
            if not quiet:
                print(f"База: {where['db']} на {where['host'] or 'localhost'}")

            # Таблицы может не быть, если миграция ещё не доехала. Молчаливое
            # «0 находок» тут было бы ложью того же класса, что и чек по пустой
            # dev-базе, — поэтому это ошибка выполнения, а не «чисто».
            exists = (await conn.execute(text(
                "SELECT to_regclass('public.slow_request') IS NOT NULL AS ok"
            ))).scalar()
            if not exists:
                print(
                    "ОШИБКА: нет таблицы slow_request — миграция tsk644_slow_request "
                    "не применена на этой базе",
                    file=sys.stderr,
                )
                return 2

            params = {"days": WINDOW_DAYS}
            totals = (await conn.execute(text(SQL_TOTALS), params)).mappings().first()
            by_path = (await conn.execute(text(SQL_BY_PATH), params)).mappings().all()
            worst = (await conn.execute(text(SQL_WORST), params)).mappings().all()
            burst = (await conn.execute(text(SQL_BURST), params)).mappings().first()
            by_day = (await conn.execute(text(SQL_BY_DAY), params)).mappings().all()
            fresh = (await conn.execute(
                text(SQL_FRESH), {"fresh_days": FRESH_DAYS}
            )).mappings().first()
    finally:
        await engine.dispose()

    n = int(totals["n"] or 0)
    if not n:
        if not quiet:
            print(f"\nOK: за {WINDOW_DAYS} дн. запросов дольше порога не было.")
        return 0

    worst_sec = (totals["worst_ms"] or 0) / 1000
    burst_n = int(burst["n"] or 0) if burst else 0

    # tsk-779: вердикт — по свежим суткам, а не по всему окну (см. SQL_BY_DAY).
    fresh_n = int(fresh["n"] or 0) if fresh else 0
    fresh_worst_sec = (fresh["worst_ms"] or 0) / 1000 if fresh else 0.0
    fresh_hour_n = int(fresh["worst_hour_n"] or 0) if fresh else 0
    kind = verdict(worst_sec, burst_n, fresh_worst_sec, fresh_hour_n)
    alarming = kind == "active"
    # «Было и прошло» отличается от «медленных вообще не было» — формулировка
    # ниже у них разная.
    was_alarming = kind == "resolved"

    print(
        f"\nМЕДЛЕННЫЕ ЗАПРОСЫ за {WINDOW_DAYS} дн.: {n} шт. "
        f"в {totals['minutes']} разных минутах, худший {worst_sec:.1f} с"
    )
    if burst and burst_n:
        print(
            f"  Самый плотный час: {burst['hour']:%d.%m %H:%M} — "
            f"{burst_n} шт., худший {(burst['worst_ms'] or 0) / 1000:.1f} с"
        )

    # По дням: одна строка отвечает на вопрос «это сейчас или уже прошло?»,
    # ради которого иначе лезут в базу руками.
    if by_day:
        print("\n  По дням (всего / худший):")
        for r in by_day:
            print(f"    {r['day']:%d.%m}  {r['n']:>4} шт.  {(r['worst_ms'] or 0) / 1000:6.1f} с")
    print(
        f"  Последние {FRESH_DAYS} сут.: {fresh_n} шт., худший {fresh_worst_sec:.1f} с"
    )

    print("\n  Обработчики (худшая задержка сверху):")
    for r in by_path:
        print(
            f"    {(r['worst_ms'] or 0) / 1000:6.1f} с  ×{r['n']:<4} "
            f"{r['method']} {r['path']}"
        )

    print("\n  Худшие запросы:")
    for r in worst:
        print(
            f"    {r['ts']:%d.%m %H:%M:%S}  {(r['duration_ms'] or 0) / 1000:6.1f} с  "
            f"{r['method']} {r['path']}  код={r['status_code']}  "
            f"request_id={r['request_id']}"
        )

    if not alarming:
        if was_alarming:
            # Затор в окне был, но свежие сутки чистые. Почти наверняка его уже
            # разобрали и починили — код выхода не поднимаем, иначе сводка будет
            # звать на разбор вылеченного всю неделю (tsk-779).
            print(
                f"\n  ЗАТОР БЫЛ, НО ПРОШЁЛ: за последние {FRESH_DAYS} сут. худший "
                f"{fresh_worst_sec:.1f} с и ни одной пачки. Похоже, причина уже "
                f"устранена — ПЕРЕД разбором проверить закрытые задачи трекера "
                f"(`D:\\Work\\Root\\tasks`, grep по 'затор', 'next-item', "
                f"'медленн'), иначе разберёте починенное во второй раз. Находка "
                f"останется в сводке, пока не выйдет из окна в {WINDOW_DAYS} дн."
            )
        else:
            print(
                f"\n  Порог тревоги не превышен (худший < {ALERT_SECONDS:.0f} с и "
                f"плотность < {ALERT_BURST} за час) — это фон, а не затор."
            )
        return 0

    print(
        "\n  ЭТО ЗАТОР, И ОН СВЕЖИЙ. Что делать: сперва убедиться, что это не "
        "уже разобранный случай (закрытые задачи трекера по 'затор'/'next-item'); "
        "затем взять request_id худшего запроса и найти его в логе на боевой "
        "машине (`grep '\"request_id\":\"…\"' /opt/lms/logs/app.log*` — со "
        "звёздочкой, лог ротируется по 5 МБ и держит примерно неделю); рядом по "
        "времени смотреть строки `tsk-593: ошибка соединения с S3` (хранилище) и "
        "`code_review: модель недоступна` (провайдер модели). 18.08.2026 затор "
        "дало молчащее внешнее хранилище (tsk-644), 29.08 — хождение движка в то "
        "же хранилище на каждом узле дерева курса (tsk-735). Оба раза база "
        "простаивала: смотреть сеть в обработчике, а не запросы."
    )
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="печатать только находки")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(quiet=args.quiet)))
    except Exception as exc:  # noqa: BLE001 — чек под планировщиком, причина обязана попасть в лог
        print(f"ОШИБКА выполнения чека: {exc}", file=sys.stderr)
        sys.exit(2)
