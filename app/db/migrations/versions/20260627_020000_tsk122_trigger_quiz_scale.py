"""Триггер назначения quiz_scale: расширение CHECK trigger_event (tsk-122, Stage 2).

Добавляет значение ``quiz_scale`` в CHECK-ограничение
``assignment_rule_trigger_event_check``. Новый триггер срабатывает на завершении
попытки/курса и интерпретирует накопленные по курсу баллы по шкалам
(``task_results.scale_scores``) через argmax / min_score (ADR-0003).

Revision ID: tsk122_trigger_quiz_scale
Revises: tsk122_quiz_scale_scores
Create Date: 2026-06-27 02:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "tsk122_trigger_quiz_scale"
down_revision: Union[str, None] = "tsk122_quiz_scale_scores"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


_CHECK_NAME = "assignment_rule_trigger_event_check"
_NEW_CHECK = (
    "trigger_event IN ('answer_value', 'task_failed', 'course_failed', 'quiz_scale')"
)
_OLD_CHECK = "trigger_event IN ('answer_value', 'task_failed', 'course_failed')"


def upgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "assignment_rule", type_="check")
    op.create_check_constraint(_CHECK_NAME, "assignment_rule", _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "assignment_rule", type_="check")
    op.create_check_constraint(_CHECK_NAME, "assignment_rule", _OLD_CHECK)
