"""Материализованный вердикт «успевает к экзамену» — tsk-1536.

Revision ID: tsk1536_methodist_readiness
Revises: tsk1500_strict_progression
Create Date: 2026-11-17

`methodist_readiness_cache` — готовые вердикты «успевает/не успевает к
экзамену» (расчёт tsk-815, `homework_volume_service.compute`) для учеников
программ подготовки ЕГЭ/ОГЭ. Пишет фоновый тик
`methodist_readiness_cron_service` (apscheduler, вне HTTP-запроса); дашборд
методиста только читает таблицу. Два инцидента 504 (17.11) случились именно
потому, что этот расчёт (~8–12 с на ученика на прод-данных) вызывался из
запроса дашборда — теперь в запросе его нет в принципе.

Миграция только добавляет таблицу — существующие данные не трогает.
Откат: DROP TABLE.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "tsk1536_methodist_readiness"
down_revision: Union[str, None] = "tsk1500_strict_progression"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "methodist_readiness_cache",
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "kind",
            sa.String(length=8),
            nullable=False,
            comment="Программа подготовки: ege | oge",
        ),
        sa.Column(
            "deadline",
            sa.Date(),
            nullable=False,
            comment="К какому дню нужно закончить программу (tsk-815)",
        ),
        sa.Column(
            "on_track",
            sa.Boolean(),
            nullable=True,
            comment=(
                "Успевает ли при нынешнем темпе. NULL — темп измерить не "
                "по чему (нет ни минутной, ни штучной пары)"
            ),
        ),
        sa.Column(
            "details",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
            comment=(
                "Детали расчёта для разбора методистом: target/fact per week, "
                "минутная пара, remaining"
            ),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Когда строка посчитана фоновым тиком",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("methodist_readiness_cache")
