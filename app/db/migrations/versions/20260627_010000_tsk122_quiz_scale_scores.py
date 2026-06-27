"""Квиз-вопросы со шкалами: хранение scale_scores (tsk-122, Stage 1).

Добавляет поле ``task_results.scale_scores`` (JSONB, nullable) для баллов по
шкалам квиз-вопросов SC_Qw/MC_Qw. Типы задач хранятся в ``tasks.task_content``
(JSONB) — отдельного DB-enum нет, поэтому новые типы валидируются только на
уровне Pydantic-схем (TaskContent.type). Никаких изменений существующих данных:
колонка nullable, для всех прежних результатов остаётся NULL.

Revision ID: tsk122_quiz_scale_scores
Revises: tsk031_assignment_rules
Create Date: 2026-06-27 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "tsk122_quiz_scale_scores"
down_revision: Union[str, None] = "tsk031_assignment_rules"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "task_results",
        sa.Column(
            "scale_scores",
            JSONB(),
            nullable=True,
            comment="Баллы по шкалам для квиз-вопросов SC_Qw/MC_Qw (tsk-122)",
        ),
    )


def downgrade() -> None:
    op.drop_column("task_results", "scale_scores")
