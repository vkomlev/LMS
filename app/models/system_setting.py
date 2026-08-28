from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemSetting(Base):
    """Значение настройки школы, выбранное администратором в кабинете (tsk-721).

    В таблице лежат ТОЛЬКО те настройки, которые администратор менял сам.
    Ничего не менял — строки нет, и значение берётся из переменной окружения
    или из умолчания в коде (порядок — `app/core/settings_store.py`).
    Поэтому «вернуть как было» — это удаление строки, а не запись прежнего
    числа: после удаления настройка снова следует за окружением.

    Описание настройки (название, пояснение, границы, тип) живёт в реестре
    `app/core/settings_registry.py`, а не здесь: оно меняется вместе с тем
    кодом, который настройку применяет. В базе — только выбранное значение.

    `value` хранится текстом при любом типе: настроек два десятка, читаются
    они через реестр, который и знает, число это, «да/нет» или строка.
    Отдельные колонки под типы дали бы схему, которую пришлось бы менять
    миграцией всякий раз, когда в кабинет выносят настройку нового вида.

    Секретов здесь быть не может: реестр их не содержит, а ключ вне реестра
    слой чтения игнорирует.
    """

    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        comment="Ключ настройки из реестра app/core/settings_registry.py",
    )
    value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Значение текстом; тип и границы проверяются по реестру",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="Когда значение изменили в последний раз",
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Кто изменил. NULL — учётку удалили после правки",
    )
