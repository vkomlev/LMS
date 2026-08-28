"""Настройки школы: показать, сохранить, вернуть как было (tsk-721).

Сервис стоит между кабинетом администратора и двумя местами хранения:
реестром описаний (`app/core/settings_registry.py`) и таблицей выбранных
значений (`system_setting`). Он же держит три обязательства задачи:

* **границы проверяются на сервере.** Форма кабинета их тоже знает, но
  проверка там — удобство, а не защита: порог, выставленный в ноль запросом
  мимо формы, ломает работу школы так же тихо;
* **остаётся след.** Каждое сохранение и каждый сброс пишутся в `audit_event`
  со старым и новым значением — иначе через месяц не понять, почему школа
  ведёт себя иначе, чем в прошлом;
* **правка действует сразу.** После записи значение кладётся в память
  процесса, и следующий же запрос читает новое — без перезапуска.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.core.settings_registry import (
    SettingDef,
    coerce,
    get_definition,
    grouped,
    serialize,
)
from app.models.system_setting import SystemSetting
from app.services import audit_service

logger = logging.getLogger("system_settings")

# Типы событий аудита. Grep-friendly константы вместо сырых строк — как в
# остальном audit_service.
SETTING_CHANGED = "admin.setting.changed"
SETTING_RESET = "admin.setting.reset"


@dataclass
class SettingView:
    """Одна настройка так, как её видит администратор."""

    key: str
    title: str
    description: str
    kind: str
    unit: str
    value: Any
    default: Any
    source: str
    """`cabinet` — выбрано в кабинете, `env` — из файла настроек, `default` — из кода."""

    min_value: float | None
    max_value: float | None
    max_length: int | None
    warning: str | None
    updated_at: Any
    updated_by: Optional[int]
    updated_by_name: Optional[str]


async def list_settings(db: AsyncSession) -> List[Tuple[str, List[SettingView]]]:
    """Все настройки по группам: что стоит сейчас, откуда взято, кто менял."""
    # Перечитываем базу перед показом: кабинет должен показывать то, что
    # действует на самом деле, даже если правку сделал соседний процесс.
    try:
        await settings_store.refresh(db)
    except Exception:
        logger.warning("tsk-721: не удалось перечитать настройки перед показом", exc_info=True)

    rows = (
        await db.execute(
            select(SystemSetting.key, SystemSetting.updated_at, SystemSetting.updated_by)
        )
    ).all()
    meta = {key: (updated_at, updated_by) for key, updated_at, updated_by in rows}

    names = await _resolve_names(db, [uid for _, uid in meta.values() if uid])

    result: List[Tuple[str, List[SettingView]]] = []
    for group, definitions in grouped():
        views = []
        for definition in definitions:
            updated_at, updated_by = meta.get(definition.key, (None, None))
            views.append(
                SettingView(
                    key=definition.key,
                    title=definition.title,
                    description=definition.description,
                    kind=definition.kind,
                    unit=definition.unit,
                    value=settings_store.get(definition.key),
                    default=definition.default,
                    source=settings_store.source(definition.key),
                    min_value=definition.min_value,
                    max_value=definition.max_value,
                    max_length=definition.max_length,
                    warning=definition.warning,
                    updated_at=updated_at,
                    updated_by=updated_by,
                    updated_by_name=names.get(updated_by) if updated_by else None,
                )
            )
        result.append((group, views))
    return result


async def _resolve_names(db: AsyncSession, user_ids: List[int]) -> dict[int, str]:
    """Имена тех, кто менял настройки — чтобы в кабинете стояло имя, а не номер."""
    if not user_ids:
        return {}
    from app.models.users import Users

    rows = (
        await db.execute(
            select(Users.id, Users.full_name).where(Users.id.in_(set(user_ids)))
        )
    ).all()
    return {uid: (name or f"Пользователь {uid}") for uid, name in rows}


async def update_setting(
    db: AsyncSession,
    *,
    key: str,
    raw_value: Any,
    user_id: int | None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SettingView:
    """Сохранить значение настройки. Границы проверяются здесь, а не в форме."""
    definition = get_definition(key)
    value = coerce(definition, raw_value)  # ValueError с русским текстом наружу

    previous = settings_store.get(key)
    previous_source = settings_store.source(key)

    stmt = (
        pg_insert(SystemSetting)
        .values(key=key, value=serialize(definition, value), updated_by=user_id)
        .on_conflict_do_update(
            index_elements=[SystemSetting.key],
            set_={
                "value": serialize(definition, value),
                "updated_by": user_id,
                "updated_at": _now(),
            },
        )
    )
    await db.execute(stmt)

    await audit_service.log_event(
        db,
        event_type=SETTING_CHANGED,
        user_id=user_id,
        ip=ip,
        user_agent=user_agent,
        details={
            "key": key,
            "title": definition.title,
            "old_value": _plain(previous),
            "new_value": _plain(value),
            "old_source": previous_source,
        },
    )
    await db.commit()

    # Значение — в память сразу: следующий запрос уже считает по нему,
    # перезапуск не нужен. Соседние процессы (если появятся) подхватят
    # правку фоновым обновлением в течение минуты.
    settings_store.apply_local(key, value)
    logger.info(
        "tsk-721: настройка %s изменена: %s → %s (пользователь %s)",
        key, _plain(previous), _plain(value), user_id,
    )
    return await _single_view(db, definition)


async def reset_setting(
    db: AsyncSession,
    *,
    key: str,
    user_id: int | None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SettingView:
    """Вернуть как было: убрать выбор администратора.

    Строка удаляется, а не переписывается прежним числом: после удаления
    настройка снова следует за файлом настроек и умолчанием в коде — ровно
    то состояние, в котором она была до первой правки.
    """
    definition = get_definition(key)
    previous = settings_store.get(key)
    had_row = settings_store.source(key) == "cabinet"

    await db.execute(delete(SystemSetting).where(SystemSetting.key == key))
    # Что окажется в силе после сброса — считаем ДО правки памяти: упади
    # запись, память осталась бы рассинхронизирована с базой.
    restored = settings_store.fallback(key)

    if had_row:
        await audit_service.log_event(
            db,
            event_type=SETTING_RESET,
            user_id=user_id,
            ip=ip,
            user_agent=user_agent,
            details={
                "key": key,
                "title": definition.title,
                "old_value": _plain(previous),
                "new_value": _plain(restored),
                "new_source": settings_store.source(key),
            },
        )
    await db.commit()
    settings_store.forget_local(key)

    logger.info(
        "tsk-721: настройка %s возвращена к %s (пользователь %s)",
        key, _plain(restored), user_id,
    )
    return await _single_view(db, definition)


async def _single_view(db: AsyncSession, definition: SettingDef) -> SettingView:
    """Свежее состояние одной настройки — то, что кабинет покажет после правки."""
    row = (
        await db.execute(
            select(SystemSetting.updated_at, SystemSetting.updated_by).where(
                SystemSetting.key == definition.key
            )
        )
    ).first()
    updated_at, updated_by = (row[0], row[1]) if row else (None, None)
    names = await _resolve_names(db, [updated_by] if updated_by else [])

    return SettingView(
        key=definition.key,
        title=definition.title,
        description=definition.description,
        kind=definition.kind,
        unit=definition.unit,
        value=settings_store.get(definition.key),
        default=definition.default,
        source=settings_store.source(definition.key),
        min_value=definition.min_value,
        max_value=definition.max_value,
        max_length=definition.max_length,
        warning=definition.warning,
        updated_at=updated_at,
        updated_by=updated_by,
        updated_by_name=names.get(updated_by) if updated_by else None,
    )


def _now():
    from sqlalchemy import func

    return func.now()


def _plain(value: Any) -> Any:
    """Значение в вид, пригодный для JSONB аудита."""
    if isinstance(value, (int, float, bool, str)) or value is None:
        return value
    return str(value)
