"""Схемы раздела «Настройки школы» в кабинете администратора (tsk-721)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class SystemSettingRead(BaseModel):
    """Одна настройка так, как её рисует кабинет."""

    key: str = Field(description="Ключ настройки")
    title: str = Field(description="Название по-русски — то, что читает человек")
    description: str = Field(description="На что влияет, одной строкой")
    kind: str = Field(description="Тип значения: int | float | bool | str")
    unit: str = Field(default="", description="Единица измерения по-русски")
    value: Any = Field(description="Действующее значение")
    default: Any = Field(description="Умолчание в коде")
    source: str = Field(
        description=(
            "Откуда взято действующее значение: cabinet — выбрано здесь, "
            "env — из файла настроек, default — из кода"
        )
    )
    min_value: Optional[float] = Field(default=None, description="Нижняя граница")
    max_value: Optional[float] = Field(default=None, description="Верхняя граница")
    max_length: Optional[int] = Field(default=None, description="Предел длины текста")
    warning: Optional[str] = Field(
        default=None, description="Что перестанет работать при выключении"
    )
    updated_at: Optional[datetime] = Field(default=None, description="Когда меняли")
    updated_by: Optional[int] = Field(default=None, description="Кто менял, номер учётки")
    updated_by_name: Optional[str] = Field(default=None, description="Кто менял, имя")


class SystemSettingGroup(BaseModel):
    """Группа настроек — так они разложены на экране."""

    group: str = Field(description="Название группы по-русски")
    items: List[SystemSettingRead]


class SystemSettingsResponse(BaseModel):
    groups: List[SystemSettingGroup]


class SystemSettingUpdate(BaseModel):
    """Новое значение. Тип свободный: приводит и проверяет его сервер по реестру."""

    value: Any = Field(description="Новое значение настройки")
