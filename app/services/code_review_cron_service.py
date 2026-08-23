# app/services/code_review_cron_service.py
"""tsk-302 этап 3: фоновая оценка кода ученика (чистота + признак ИИ-авторства).

**Почему фоном, а не при сдаче.** Оценка — это вызов внешней модели: секунды
в пользовательском пути приёма ответа. Ученику она не показывается вовсе, а
преподаватель открывает работу заметно позже сдачи — ждать нечего и некому.
Решение оператора 2026-08-06: «фоном после сдачи».

**Очередь без отдельной таблицы.** Приём ответа помечает работу
`code_review = {"status": "pending"}`, этот тик разбирает пометки. Отдельная
таблица очереди не заводится намеренно: состояние живёт ровно там, где будет
жить результат, поэтому невозможно рассогласование «в очереди есть, в отчёте
нет». Побочная польза — статус видно в кабинете преподавателя, и понятно, что
оценка ещё готовится, а не отсутствует.

**Повторы.** Временные сбои (сеть, таймаут, остывание провайдера после 429)
оставляют работу в `pending` со счётчиком попыток — следующий тик попробует
снова. Постоянные (неверный ключ, битый ответ модели) сразу уводят в `failed`:
долбить провайдера на заведомо нерабочей конфигурации нельзя, это кормит его
брейкер. Признак `retryable` даёт сам LLM-клиент (§5 контракта).

Паттерн тика и advisory-lock — тот же, что у соседних сервисов
(`escalation_service`, `course_dependency_state_cron_service`): один worker за
тик делает работу, остальные отступают мгновенно.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services.code_quality_service import analyze_student_code_quality
from app.services.code_review_service import (
    pick_code_attachment,
    pick_code_for_review,
    review_student_code,
)

logger = logging.getLogger("app.code_review_cron")

# ascii "CDRV" (CoDe ReView) — не пересекается с соседними ключами: Y-6
# (0x59365453), генератор occurrence (0x4C534E43), attendance (0x4C534E41),
# link_audit (0x4C494E4B), состояние зависимостей (0x43445354).
_CODE_REVIEW_LOCK_KEY = 0x43445256

_scheduler: Optional[AsyncIOScheduler] = None

# Берём работы, помеченные к оценке. `attempts` нужен ради `time_expired`:
# просроченную попытку оценивать незачем — балл всё равно обнулён.
_PENDING_SQL = """
    SELECT tr.id,
           tr.user_id,
           tr.attempt_id,
           tr.task_id,
           tr.answer_json->'response'->>'value'   AS value,
           tr.answer_json->'response'->>'comment' AS comment,
           t.task_content->>'stem'                AS stem,
           tr.answer_json->'response'->'meta'->'attachments' AS attachments,
           COALESCE((tr.code_review->>'attempts')::int, 0) AS attempts,
           COALESCE((tr.code_review->>'backfill')::bool, false) AS backfill,
           tr.code_review->>'code'                AS code_snapshot
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.code_review->>'status' = 'pending'
      AND (
            tr.code_review->>'claimed_at' IS NULL
            OR (tr.code_review->>'claimed_at')::timestamptz
                 < now() - make_interval(mins => :claim_ttl_min)
          )
    ORDER BY tr.submitted_at ASC
    LIMIT :limit
"""

# tsk-644: пометка «работа взята этим тиком». Нужна с тех пор, как замок больше
# не держится на всё время прохода: без неё второй worker (или следующий тик
# после перезапуска процесса) забрал бы ту же работу и заплатил бы провайдеру
# второй раз за тот же ответ.
#
# Пометка живёт ровно до записи отчёта: `_write` пишет payload целиком, поэтому
# любая запись — и готовый вердикт, и повтор — её стирает. Если процесс умер
# посередине, работу освободит срок `claim_ttl`.
_CLAIM_SQL = """
    UPDATE task_results
       SET code_review = code_review || jsonb_build_object('claimed_at', to_jsonb(now()))
     WHERE id = ANY(:ids)
"""


async def code_review_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> Dict[str, Any]:
    """Один проход очереди. Возвращает summary для логов и тестов."""
    settings = Settings()
    factory = session_factory or async_session_factory
    summary = {
        "locked": False, "picked": 0, "reviewed": 0,
        "retried": 0, "failed": 0, "skipped": 0, "degraded": 0,
    }

    # Фаза 1 — захват пачки. Короткая транзакция: замок, выборка, пометка,
    # коммит. Всё вместе — доли секунды.
    #
    # tsk-644: раньше эта транзакция держалась до КОНЦА прохода, то есть через
    # все вызовы модели. Замер стенда 2026-08-22 (молчащий провайдер, 10 работ
    # в очереди): транзакция висела `idle in transaction` 298 c и продолжала
    # висеть — при полной пачке это ~20 минут на одном подключении из пула, и
    # столько же PG не может убрать мёртвые версии строк (горизонт xmin стоит).
    # Живым запросам это на стенде не мешало, но подключение и горизонт — плата
    # ни за что: модели транзакция не нужна вовсе.
    async with factory() as db:
        got = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _CODE_REVIEW_LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.debug("tsk-302: тик оценки кода пропущен — работает другой worker")
            return summary
        summary["locked"] = True

        rows = (await db.execute(
            text(_PENDING_SQL),
            {
                "limit": settings.code_review_batch_size,
                "claim_ttl_min": settings.code_review_claim_ttl_min,
            },
        )).fetchall()
        summary["picked"] = len(rows)
        if rows:
            await db.execute(text(_CLAIM_SQL), {"ids": [r[0] for r in rows]})
        await db.commit()

    if not rows:
        return summary

    # Фаза 2 — работа. Открытой транзакции здесь нет: вызов модели идёт вне БД,
    # а каждый отчёт `_write` пишет и коммитит сам, своей короткой транзакцией.
    for row in rows:
        (result_id, student_id, attempt_id, task_id, value, comment, stem,
         attachments, attempts, backfill, code_snapshot) = row
        # Снимок кода, снятый при приёме ответа, главнее повторного разбора:
        # файл-вложение мог быть удалён следующей загрузкой ученика в этой
        # же попытке (см. комментарий в `attempts.py`).
        # tsk-593: разбор читает вложение из объектного хранилища —
        # синхронный сетевой вызов, поэтому уносим с петли событий.
        code = code_snapshot or await asyncio.to_thread(
            pick_code_for_review,
            value, comment, attachments,
            attempt_id=attempt_id, task_id=task_id,
        )
        if not code:
            # Программы в ответе нет (одно вложение, ответ-однострочник) —
            # оценивать нечего. Снимаем пометку, чтобы работа не крутилась
            # в очереди вечно.
            #
            # Причину различаем: работа с вложением, из которой код достать
            # не вышло, — это не то же самое, что честное «программы нет».
            # Со сваленными в одну кучу такие работы уже не найти и не
            # пересчитать после починки разбора.
            reason = "extract_failed" if pick_code_attachment(attachments) else "no_code"
            await _write(factory, result_id, {"status": "skipped", "reason": reason}, backfill=backfill)
            summary["skipped"] += 1
            continue

        # Статический анализ считаем ПЕРВЫМ и НЕЗАВИСИМО от модели.
        # Находка ревью Б1: раньше он вызывался только внутри ветки успеха
        # модели — а на проде ключа ещё нет, значит модель отвечает
        # `LLMConfigError`, и преподаватель вместо работающего pylint-отчёта
        # (этап 0, уже был на проде) увидел бы «оценка не выполнена». Теперь
        # при недоступной модели фича деградирует до уровня этапа 0, а не
        # исчезает. На не-Python анализ сам вернёт syntax_error — тогда
        # секции просто не будет.
        static = await asyncio.to_thread(analyze_student_code_quality, code)
        static_ok = bool(static) and not static.get("error")

        verdict = await review_student_code(
            code, task_stem=stem, student_id=student_id,
        )

        error = verdict.get("error")
        if not error:
            payload: Dict[str, Any] = {"status": "done", **verdict}
            if static_ok:
                payload["static"] = static
            await _write(factory, result_id, payload, backfill=backfill)
            summary["reviewed"] += 1
            continue

        attempts_done = int(attempts) + 1
        can_retry = bool(verdict.get("retryable")) and attempts_done < settings.code_review_max_attempts
        if can_retry:
            # Остаёмся в очереди: следующий тик попробует снова. Переносим
            # ФАКТИЧЕСКИ использованный код, а не то, что лежало в снимке:
            # если снимка не было и код прочитан из файла, повтор иначе
            # остался бы ни с чем — файл к тому времени мог исчезнуть.
            await _write(factory, result_id, {
                "status": "pending",
                "attempts": attempts_done,
                "last_error": error,
            }, backfill=backfill, code_snapshot=code)
            summary["retried"] += 1
        else:
            # Модель недоступна окончательно — но статический анализ мог
            # сработать. Отдаём что есть: это ровно тот отчёт, который
            # преподаватель видел до этапа 3.
            payload = {
                "status": "done" if static_ok else "failed",
                "attempts": attempts_done,
                "error": error,
                "message": verdict.get("message"),
            }
            if static_ok:
                payload["static"] = static
                payload["degraded"] = True
            await _write(factory, result_id, payload, backfill=backfill)
            # Считаем раздельно: в БД у деградированной работы `done`, и
            # называть её в логе провалом — врать самому себе при разборе.
            summary["degraded" if static_ok else "failed"] += 1

    logger.info(
        "tsk-302 code_review_cron_tick done picked=%s reviewed=%s degraded=%s "
        "retried=%s failed=%s skipped=%s",
        summary["picked"], summary["reviewed"], summary["degraded"],
        summary["retried"], summary["failed"], summary["skipped"],
    )
    return summary


async def _write(
    factory: async_sessionmaker[AsyncSession],
    result_id: int,
    payload: Dict[str, Any],
    *,
    backfill: bool = False,
    code_snapshot: Optional[str] = None,
) -> None:
    """Записывает отчёт целиком: он самодостаточен, сливать со старым нечего.

    tsk-644: своя короткая транзакция на каждый отчёт. Раньше все записи прохода
    шли одной транзакцией, открытой на всё время прохода, — то есть подключение
    было занято, пока тик ждал модель. Теперь между работами открытой транзакции
    нет вовсе. Побочно это чинит и «всё или ничего»: сбой на девятой работе
    больше не отменяет восемь уже посчитанных вердиктов.

    Запись стирает и пометку захвата (`claimed_at`): payload пишется целиком.

    :param backfill: работа попала в очередь пересчётом задним числом. Метку
        переносим в новый отчёт: запись идёт целиком, и иначе она потерялась бы
        на первом же тике — а потом нечем было бы отделить оценки старых работ
        от оценок живых сдач.
    :param code_snapshot: копия кода из вложения. Переносим её только в
        промежуточные записи (`pending` при повторе): иначе повтор потерял бы
        код, файл которого уже удалён. В готовый отчёт копия не идёт — она
        временная и в результате не нужна.
    """
    if backfill:
        payload = {**payload, "backfill": True}
    if code_snapshot and payload.get("status") == "pending":
        payload = {**payload, "code": code_snapshot}
    async with factory() as db:
        await db.execute(
            text("UPDATE task_results SET code_review = CAST(:payload AS jsonb) WHERE id = :id"),
            {"payload": _json(payload), "id": result_id},
        )
        await db.commit()


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднимает периодический тик (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.code_review_cron_enabled:
        logger.info("tsk-302: фоновая оценка кода выключена (CODE_REVIEW_CRON_ENABLED)")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        code_review_cron_tick,
        trigger=IntervalTrigger(minutes=int(settings.code_review_cron_interval_min)),
        id="tsk302_code_review_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-302: фоновая оценка кода запущена, интервал %s мин",
        settings.code_review_cron_interval_min,
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
