"""Групповые и частичные initial баллы демо-2027 (tsk-1538).

Revision ID: tsk1538_exam_group_score
Revises: tsk1524_exam_progress
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "tsk1538_exam_group_score"
down_revision: Union[str, None] = "tsk1524_exam_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("exam_run_item", sa.Column("initial_score", sa.Integer(), nullable=True))
    op.execute("UPDATE exam_run_item SET initial_score=CASE WHEN initial_outcome='blank' THEN 0 WHEN initial_result_id IS NOT NULL THEN (SELECT tr.score FROM task_results tr WHERE tr.id=initial_result_id) WHEN initial_outcome='correct' THEN primary_score WHEN initial_outcome='wrong' THEN 0 ELSE NULL END")
    op.create_check_constraint("ck_exam_run_item_initial_score", "exam_run_item", "initial_score IS NULL OR initial_score BETWEEN 0 AND primary_score")


def downgrade() -> None:
    op.drop_constraint("ck_exam_run_item_initial_score", "exam_run_item", type_="check")
    op.drop_column("exam_run_item", "initial_score")
