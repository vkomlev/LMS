from __future__ import annotations
from typing import TYPE_CHECKING, List
from sqlalchemy import Integer, String, UniqueConstraint, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from Migrations.models import Users
from app.db.base import Base
if TYPE_CHECKING:
    from app.models.association_tables import t_user_roles


class Roles(Base):
    """
    Роли пользователей.
    """
    __tablename__ = "roles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="roles_pkey"),
        UniqueConstraint("name", name="roles_name_key"),
        {"comment": "Роли пользователей"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID роли")
    name: Mapped[str] = mapped_column(String, nullable=False, comment="Название роли")

    user: Mapped[List["Users"]] = relationship(
        "Users", secondary=t_user_roles, back_populates="role"
    )
