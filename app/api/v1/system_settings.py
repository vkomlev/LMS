# app/api/v1/system_settings.py
"""Раздел «Настройки школы» кабинета администратора (tsk-721).

Только администратор: пороги отсюда меняют работу школы для всех, и это
распорядительное решение, а не учебная работа. Методист и преподаватель
раздел не видят вовсе.

Секретов здесь нет и быть не может: наружу отдаются только настройки из
реестра, а он содержит правила работы школы. Ключ доступа не попадёт в ответ,
даже если кто-то впишет его в таблицу руками — слой чтения игнорирует ключи
вне реестра.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.system_settings import (
    SystemSettingGroup,
    SystemSettingRead,
    SystemSettingUpdate,
    SystemSettingsResponse,
)
from app.services import system_settings_service

router = APIRouter(prefix="/system-settings", tags=["system_settings"])

_ADMIN_GATE = require_role("admin")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _actor_id(current_user: CurrentUser) -> int | None:
    """Кого записать автором правки.

    Сервисный ключ приходит как `CurrentUser(id=0)` — несуществующая учётка.
    Записать её в «кто менял» нельзя (внешний ключ на `users`), да и неверно
    по смыслу: за ключом не стоит человек. В таком случае автор — NULL,
    а сам факт правки всё равно остаётся в журнале.
    """
    return None if current_user.is_service else current_user.id


@router.get("", response_model=SystemSettingsResponse)
async def list_system_settings(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> SystemSettingsResponse:
    """Все настройки школы по группам: что стоит, откуда взято, кто менял."""
    groups = await system_settings_service.list_settings(db)
    return SystemSettingsResponse(
        groups=[
            SystemSettingGroup(
                group=group,
                items=[SystemSettingRead(**vars(view)) for view in views],
            )
            for group, views in groups
        ]
    )


@router.put("/{key}", response_model=SystemSettingRead)
async def update_system_setting(
    key: str,
    payload: SystemSettingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> SystemSettingRead:
    """Сохранить значение. Границы проверяет сервер, форма их лишь подсказывает."""
    try:
        view = await system_settings_service.update_setting(
            db,
            key=key,
            raw_value=payload.value,
            user_id=_actor_id(current_user),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Настройка {key!r} не найдена"
        ) from None
    except ValueError as exc:
        # Текст сообщения написан для человека и уходит прямо в форму.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return SystemSettingRead(**vars(view))


@router.delete("/{key}", response_model=SystemSettingRead)
async def reset_system_setting(
    key: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> SystemSettingRead:
    """Вернуть как было: снять выбор администратора, вернуться к прежнему значению."""
    try:
        view = await system_settings_service.reset_setting(
            db,
            key=key,
            user_id=_actor_id(current_user),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Настройка {key!r} не найдена"
        ) from None
    return SystemSettingRead(**vars(view))
