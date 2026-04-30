from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from sqlalchemy import (
    Integer,
    Text,
    String,
    Sequence,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import Users


class Notifications(Base):
    """
    Уведомления: legacy-семантика «версии шаблонов» (id, content, modified_by,
    modified_at) расширена в Y-4 inbox-полями для in-app уведомлений ученикам
    (user_id, kind, title, payload, read_at). См. M8 миграцию.

    Legacy PK constraint name = template_versions_pkey (исторический); новые
    ограничения именованы fk_notifications_user_id и idx_notifications_user_*.
    """
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["modified_by"], ["users.id"],
            name="template_versions_modified_by_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_notifications_user_id",
            ondelete="CASCADE",
        ),
        PrimaryKeyConstraint("id", name="template_versions_pkey"),
        {"comment": "Inbox-уведомления (Y-4) поверх legacy template_versions"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        Sequence("template_versions_id_seq"),
        primary_key=True,
        comment="ID записи",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Готовый текст уведомления"
    )
    modified_by: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Кто создал/изменил запись (legacy + creator inbox)"
    )
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Время создания / последнего изменения",
    )

    # Inbox-поля (Y-4, M8) — все nullable для совместимости с legacy-записями
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Получатель inbox-уведомления (NULL = legacy)"
    )
    kind: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="Тип уведомления (sa_com_graded, …)"
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Краткий заголовок"
    )
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True, comment="Структурированные данные"
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Когда ученик прочитал; NULL = непрочитано",
    )

    # Legacy backref: «шаблоны/уведомления, которые этот user создал/изменил»
    users: Mapped[Optional["Users"]] = relationship(
        "Users",
        back_populates="notifications",
        foreign_keys="[Notifications.modified_by]",
    )

    # Inbox backref: «уведомления, адресованные этому user»
    recipient: Mapped[Optional["Users"]] = relationship(
        "Users",
        back_populates="inbox_messages",
        foreign_keys="[Notifications.user_id]",
    )
