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
from app.services.code_review_service import pick_code_for_review, review_student_code

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
           tr.answer_json->'response'->>'value'   AS value,
           tr.answer_json->'response'->>'comment' AS comment,
           t.task_content->>'stem'                AS stem,
           COALESCE((tr.code_review->>'attempts')::int, 0) AS attempts
    FROM task_results tr
    JOIN tasks t ON t.id = tr.task_id
    WHERE tr.code_review->>'status' = 'pending'
    ORDER BY tr.submitted_at ASC
    LIMIT :limit
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
            text(_PENDING_SQL), {"limit": settings.code_review_batch_size},
        )).fetchall()
        summary["picked"] = len(rows)
        if not rows:
            return summary

        for row in rows:
            result_id, student_id, value, comment, stem, attempts = row
            code = pick_code_for_review(value, comment)
            if not code:
                # Программы в ответе нет (одно вложение, ответ-однострочник) —
                # оценивать нечего. Снимаем пометку, чтобы работа не крутилась
                # в очереди вечно.
                await _write(db, result_id, {"status": "skipped", "reason": "no_code"})
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
                await _write(db, result_id, payload)
                summary["reviewed"] += 1
                continue

            attempts_done = int(attempts) + 1
            can_retry = bool(verdict.get("retryable")) and attempts_done < settings.code_review_max_attempts
            if can_retry:
                # Остаёмся в очереди: следующий тик попробует снова.
                await _write(db, result_id, {
                    "status": "pending",
                    "attempts": attempts_done,
                    "last_error": error,
                })
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
                await _write(db, result_id, payload)
                # Считаем раздельно: в БД у деградированной работы `done`, и
                # называть её в логе провалом — врать самому себе при разборе.
                summary["degraded" if static_ok else "failed"] += 1

        await db.commit()

    logger.info(
        "tsk-302 code_review_cron_tick done picked=%s reviewed=%s degraded=%s "
        "retried=%s failed=%s skipped=%s",
        summary["picked"], summary["reviewed"], summary["degraded"],
        summary["retried"], summary["failed"], summary["skipped"],
    )
    return summary


async def _write(db: AsyncSession, result_id: int, payload: Dict[str, Any]) -> None:
    """Записывает отчёт целиком: он самодостаточен, сливать со старым нечего."""
    await db.execute(
        text("UPDATE task_results SET code_review = CAST(:payload AS jsonb) WHERE id = :id"),
        {"payload": _json(payload), "id": result_id},
    )


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
