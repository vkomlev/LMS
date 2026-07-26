"""poligon: promo/enrollment tables (tsk-182, ветка poligon — НЕ применять на main)

Revision ID: poligon_20260725_010000
Revises: <ПРОСТАВИТЬ актуальный head ветки poligon перед коммитом>
Create Date: 2026-07-25

Три новые таблицы, специфичные только для учебного полигона — не существуют
и не должны появляться в прод-схеме `learn`. Обе целостности (промокод
уникален; редемпшн НЕ уникален по (user_id, code) — это НАМЕРЕННЫЙ дефект
класса 8, см. docs/qa-poligon/defect-registry.md) закреплены явно в DDL,
а не оставлены на волю application-кода — иначе дефект перестанет быть
воспроизводимым детерминированно.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "poligon_20260725_010000"
down_revision = "REPLACE_ME_WITH_POLIGON_BRANCH_HEAD"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "poligon_promo_codes",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("max_uses_per_account", sa.Integer(), nullable=False, server_default="1"),
        comment="Полигон tsk-182: промокоды (SUMMER2026/STUDENT20 из уроков 6.4/6.7)",
    )

    op.create_table(
        "poligon_promo_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("promo_code", sa.String(length=32), sa.ForeignKey("poligon_promo_codes.code"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # НАМЕРЕННО без UniqueConstraint(user_id, promo_code) — класс 8 реестра
        # (повторное применение STUDENT20 должно проходить снова).
        comment="Полигон tsk-182: факты применения промокода. См. defect-registry класс 8.",
    )

    op.create_table(
        "poligon_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("payment_method", sa.String(length=16), nullable=False),
        sa.Column("amount_charged", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "course_id", name="uq_poligon_enrollment_user_course"),
        comment="Полигон tsk-182: записи на курс. См. defect-registry класс 3 (200 без записи).",
    )


def downgrade() -> None:
    op.drop_table("poligon_enrollments")
    op.drop_table("poligon_promo_redemptions")
    op.drop_table("poligon_promo_codes")
