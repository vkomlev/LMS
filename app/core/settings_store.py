# app/core/settings_store.py
"""Чтение настроек школы в момент применения (tsk-721).

Зачем отдельный слой
--------------------
Настройка, прочитанная при импорте модуля, требует перезапуска — а смысл
кабинета ровно в том, чтобы правка действовала сразу. Поэтому места
применения зовут `get_int("lesson_idle_threshold_minutes")` в тот момент,
когда порог им понадобился, а не складывают его в константу модуля. Образец
взят у `dropout_window_days()` из tsk-647, здесь он обобщён на весь реестр.

Откуда берётся значение (решение оператора 2026-08-28)
------------------------------------------------------
1. **Кабинет** — строка в таблице `system_setting`. Побеждает всегда.
2. **Переменная окружения** — стартовое значение: действует, пока в кабинете
   ничего не сохранено. Правка `.env` не отменяет решение администратора молча.
3. **Умолчание в коде** из реестра — последний рубеж.

Почему значения лежат в памяти, а не читаются из базы на каждый вызов
---------------------------------------------------------------------
Пороги спрашивают в горячих путях (приём ответа, обход занятий) — ходить в
базу за каждым было бы дороже самой работы. Поэтому вся таблица целиком
(два десятка строк) держится в памяти процесса:

* при старте приложения — разовое чтение;
* при сохранении из кабинета — немедленное обновление, поэтому правка видна
  со следующего же запроса;
* фоновым тиком раз в минуту — страховка на случай, если процессов станет
  больше одного: тогда правка в одном доедет до остальных за минуту, а не
  будет ждать перезапуска.

Слой намеренно не падает: не прочиталась база — работаем на окружении и
умолчаниях, о чём остаётся запись в логе. Отсутствие настроек не должно
останавливать школу.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_registry import SettingDef, SETTINGS, coerce, get_definition

logger = logging.getLogger("settings_store")

# Значения, сохранённые администратором. Пусто — ничего не сохраняли.
_cabinet: Dict[str, Any] = {}
_loaded: bool = False

_REFRESH_INTERVAL_SEC = 60
_refresh_task: Optional[asyncio.Task] = None


_SELECT_SQL = text("SELECT key, value FROM system_setting")


async def refresh(db: AsyncSession) -> int:
    """Перечитать значения из базы в память. Возвращает число прочитанных."""
    global _cabinet, _loaded
    rows = (await db.execute(_SELECT_SQL)).all()

    fresh: Dict[str, Any] = {}
    for key, raw in rows:
        try:
            definition = get_definition(key)
        except KeyError:
            # Ключ из базы, которого больше нет в реестре: настройку убрали из
            # кода, строка осталась. Не ошибка — просто игнорируем.
            logger.info("настройка %s есть в базе, но не в реестре — пропускаю", key)
            continue
        try:
            fresh[key] = coerce(definition, raw)
        except ValueError as exc:
            # Значение за границами могло попасть только в обход кабинета
            # (прямой SQL). Берём умолчание, но громко — иначе школа тихо
            # работает по значению, которого никто не выбирал.
            logger.error("настройка %s в базе недопустима (%s) — беру умолчание", key, exc)

    _cabinet = fresh
    _loaded = True
    return len(fresh)


def _env_value(definition: SettingDef) -> Any | None:
    """Стартовое значение из переменной окружения, если оно там есть и годно."""
    if not definition.env_var:
        return None
    raw = os.getenv(definition.env_var)
    if raw is None:
        return None
    if definition.kind == "str" and raw == "":
        # Пустая строка — законное значение (реквизиты не заданы), берём как есть.
        return ""
    try:
        return coerce(definition, raw)
    except ValueError as exc:
        logger.warning(
            "переменная %s задана недопустимо (%s) — беру умолчание",
            definition.env_var, exc,
        )
        return None


def get(key: str) -> Any:
    """Действующее значение настройки: кабинет → окружение → умолчание."""
    definition = get_definition(key)

    if key in _cabinet:
        return _cabinet[key]

    from_env = _env_value(definition)
    if from_env is not None:
        return from_env

    return definition.default


def fallback(key: str) -> Any:
    """Значение БЕЗ учёта кабинета: окружение или умолчание в коде.

    То, к чему настройка вернётся по кнопке «вернуть как было». Считается,
    ничего не меняя в памяти, — чтобы кабинет мог показать это ещё до сброса.
    """
    definition = get_definition(key)
    from_env = _env_value(definition)
    return definition.default if from_env is None else from_env


def source(key: str) -> str:
    """Откуда сейчас берётся значение — для показа в кабинете.

    `cabinet` | `env` | `default`.
    """
    definition = get_definition(key)
    if key in _cabinet:
        return "cabinet"
    if _env_value(definition) is not None:
        return "env"
    return "default"


def get_int(key: str) -> int:
    return int(get(key))


def get_float(key: str) -> float:
    return float(get(key))


def get_bool(key: str) -> bool:
    return bool(get(key))


def get_str(key: str) -> str:
    value = get(key)
    return "" if value is None else str(value)


def apply_local(key: str, value: Any) -> None:
    """Положить сохранённое значение в память немедленно (без похода в базу)."""
    _cabinet[key] = value


def forget_local(key: str) -> None:
    """Убрать значение из памяти после сброса «вернуть как было»."""
    _cabinet.pop(key, None)


def is_loaded() -> bool:
    """Читали ли мы базу хоть раз. `False` — работаем на окружении и умолчаниях."""
    return _loaded


def snapshot() -> Dict[str, Any]:
    """Копия действующих значений — для логов и тестов."""
    return {s.key: get(s.key) for s in SETTINGS}


async def load_once() -> None:
    """Разовое чтение при старте приложения. Не падает: школа важнее настроек."""
    from app.db.session import async_session_factory

    try:
        async with async_session_factory() as db:
            count = await refresh(db)
        logger.info("tsk-721: настройки школы прочитаны, сохранённых значений: %s", count)
    except Exception:
        logger.exception("tsk-721: настройки школы не прочитались — работаю на .env и умолчаниях")


async def _refresh_loop() -> None:
    """Фоновая страховка: перечитывать значения раз в минуту."""
    while True:
        try:
            await asyncio.sleep(_REFRESH_INTERVAL_SEC)
            from app.db.session import async_session_factory

            async with async_session_factory() as db:
                await refresh(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("tsk-721: не удалось обновить настройки школы", exc_info=True)


def start_refresh_loop() -> None:
    """Запустить фоновое обновление (идемпотентно)."""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh_loop())


def stop_refresh_loop() -> None:
    global _refresh_task
    if _refresh_task is not None:
        _refresh_task.cancel()
    _refresh_task = None


def reset_for_tests() -> None:
    """Забыть прочитанное — тестам нужно чистое состояние между случаями."""
    global _cabinet, _loaded
    _cabinet = {}
    _loaded = False
