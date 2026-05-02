"""M11: добавить courses.is_public_demo для guest-mode (Phase Y-5).

Revision ID: m11_courses_is_public_demo
Revises: m10_role_backfill
Create Date: 2026-05-02

Phase Y-5 (Guest mode + WP embed) — открыть подмножество курсов
анонимным посетителям SPW для решения 1+ задач без регистрации.

Действия:
- ADD COLUMN courses.is_public_demo BOOLEAN NOT NULL DEFAULT FALSE.
  Существующие 22+ курсов получают FALSE — backward-compatible.
- CREATE partial INDEX idx_courses_is_public_demo WHERE is_public_demo=TRUE
  — минимизирует размер индекса (1-2 demo-курса в обозримом будущем),
  ускоряет SELECT ... WHERE c.is_public_demo=TRUE в guest endpoints.

Параметризация demo-курса — через ручной UPDATE оператором (см. tech-spec
Y-5 §6.6 operator seed). UI-toggle в админке — post-MVP.

См. tech-spec: D:/Work/ContentBackbone/docs/tech-specs/tech-spec-Y5-guest-embed-v1.md §6.1.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "m11_courses_is_public_demo"
down_revision: Union[str, None] = "m10_role_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column(
            "is_public_demo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Доступен ли курс гостям без регистрации (Phase Y-5).",
        ),
    )
    op.create_index(
        "idx_courses_is_public_demo",
        "courses",
        ["is_public_demo"],
        postgresql_where=sa.text("is_public_demo = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_courses_is_public_demo", table_name="courses")
    op.drop_column("courses", "is_public_demo")
