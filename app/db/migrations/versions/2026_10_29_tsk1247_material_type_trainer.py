"""Тип материала `trainer` — интерактивный тренажёр (tsk-1247).

Revision ID: tsk1247_material_type_trainer
Revises: tsk1442_slot_student_dates
Create Date: 2026-10-29

Только ``ALTER TYPE content_type ADD VALUE 'trainer'``: данных и других
изменений схемы нет (бриф tsk-1247, Р2 — отдельной ревизией без данных).
``ADD VALUE`` идёт в autocommit-блоке: новое значение enum нельзя использовать
в той же транзакции, где его добавили, а следующие ревизии цепочки могут.

Откат: PostgreSQL не умеет удалять значение enum, поэтому тип пересоздаётся
без ``trainer`` (переименовать → создать прежний → перевести колонку →
удалить старый). Если в ``materials`` уже есть тренажёры, откат отказывает с
понятной ошибкой: удалить их молча — потерять сценарии. Сначала выключить
рубильник ``TRAINERS_ENABLED`` и удалить материалы ``type = 'trainer'`` руками.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "tsk1247_material_type_trainer"
down_revision: Union[str, None] = "tsk1442_slot_student_dates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Значения enum до этой ревизии — в прежнем порядке (baseline + 20260129_*).
_PREVIOUS_VALUES = (
    "text", "video", "link", "pdf", "audio", "image", "office_document",
    "script", "document",
)


def upgrade() -> None:
    """Добавить значение `trainer` в enum `content_type` (идемпотентно)."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE content_type ADD VALUE IF NOT EXISTS 'trainer'")


def downgrade() -> None:
    """Пересоздать `content_type` без `trainer`; отказ, если тренажёры есть."""
    bind = op.get_bind()
    count = bind.execute(
        text("SELECT count(*) FROM materials WHERE type::text = 'trainer'")
    ).scalar()
    if count:
        raise RuntimeError(
            f"tsk-1247: в materials {count} материалов type='trainer' — откат типа "
            "удалил бы их сценарии. Удалите их явно и повторите downgrade."
        )
    values = ", ".join(f"'{v}'" for v in _PREVIOUS_VALUES)
    op.execute("ALTER TYPE content_type RENAME TO content_type_tsk1247_old")
    op.execute(f"CREATE TYPE content_type AS ENUM ({values})")
    op.execute(
        "ALTER TABLE materials ALTER COLUMN type TYPE content_type "
        "USING type::text::content_type"
    )
    op.execute("DROP TYPE content_type_tsk1247_old")
