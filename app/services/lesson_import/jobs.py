"""tsk-1255: фоновый разбор презентации — состояние задачи в Redis.

Почему не таблица: состояние нужно только до публикации урока (черновик и так
живёт у клиента), а миграция в этот день шла бы параллельно с двумя чужими
(tsk-1254, tsk-997). Redis с TTL сутки — ровно столько жизни, сколько нужно.

Конвертации идут по одной на весь сервер (замок в Redis): LibreOffice делит
машину с боевыми службами. Процесс, умерший посреди работы (рестарт при
выкате), оставил бы «running» навсегда — поэтому у состояния есть срок
свежести, после него задача считается упавшей.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from app.core.config import Settings
from app.services.lesson_import.slides import SOFFICE_TIMEOUT_S, render_presentation
from app.services.rate_limit_service import get_redis
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
settings = Settings()

KEY = "lesson_import:job:{}"
LOCK = "lesson_import:converter"
TTL_S = 24 * 3600
#: Дольше этого «running» без обновления — процесс умер, задача упала.
STALE_S = SOFFICE_TIMEOUT_S + 600
_tasks: set[asyncio.Task] = set()


def _redis():
    return get_redis(settings.redis_url)


async def _save(job_id: str, state: dict[str, Any]) -> None:
    await _redis().set(KEY.format(job_id), json.dumps(state, ensure_ascii=False), ex=TTL_S)


async def get_job(job_id: str, course_id: int) -> Optional[dict[str, Any]]:
    """Состояние задачи; чужой курс — как «нет такой» (id не раскрывает чужое)."""
    raw = await _redis().get(KEY.format(job_id))
    if raw is None:
        return None
    state = json.loads(raw)
    if state.get("course_id") != course_id:
        return None
    if state["status"] in ("queued", "running") and time.time() - state["updated"] > STALE_S:
        state.update(status="failed", error="Разбор прервался (сервер перезапускался) — загрузите файл ещё раз")
    return state


async def start_job(course_id: int, user_id: Optional[int], data: bytes, filename: str) -> str:
    """Поставить разбор презентации в фон и сразу вернуть id задачи."""
    job_id = uuid.uuid4().hex
    await _save(job_id, {"course_id": course_id, "user_id": user_id, "filename": filename,
                         "status": "queued", "updated": time.time()})
    task = asyncio.create_task(_run(job_id, course_id, user_id, data, filename))
    _tasks.add(task)  # иначе сборщик мусора может снять задачу на полпути
    task.add_done_callback(_tasks.discard)
    return job_id


async def _acquire_lock(job_id: str, base: dict[str, Any]) -> None:
    """Ждать своей очереди: одна конвертация на сервер; ожидание держит задачу свежей."""
    while not await _redis().set(LOCK, job_id, nx=True, ex=STALE_S):
        await _save(job_id, {**base, "status": "queued", "updated": time.time()})
        await asyncio.sleep(5)


async def _run(job_id: str, course_id: int, user_id: Optional[int], data: bytes, filename: str) -> None:
    base = {"course_id": course_id, "user_id": user_id, "filename": filename}
    started = time.time()
    try:
        await _acquire_lock(job_id, base)
        await _save(job_id, {**base, "status": "running", "updated": time.time()})
        result = await render_presentation(data, filename)
        await _save(job_id, {**base, "status": "ready", "updated": time.time(), "result": result})
        logger.info("lesson_import presentation job=%s course=%s states=%s за %.0f с",
                    job_id, course_id, result["states"], time.time() - started)
    except DomainError as exc:
        logger.warning("lesson_import presentation job=%s failed: %s", job_id, exc.detail)
        await _save(job_id, {**base, "status": "failed", "updated": time.time(), "error": exc.detail})
    except Exception:  # noqa: BLE001 — фоновая задача: исключение иначе пропадёт молча
        logger.exception("lesson_import presentation job=%s: непредвиденная ошибка", job_id)
        await _save(job_id, {**base, "status": "failed", "updated": time.time(),
                             "error": "Внутренняя ошибка разбора презентации"})
    finally:
        try:
            if await _redis().get(LOCK) == job_id:
                await _redis().delete(LOCK)
        except Exception:  # noqa: BLE001
            logger.exception("lesson_import: не снят замок конвертера job=%s", job_id)
