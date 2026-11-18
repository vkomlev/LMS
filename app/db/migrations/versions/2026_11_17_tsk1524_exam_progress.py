"""Карта прогресса ЕГЭ/ОГЭ: варианты и initial flow (tsk-1524).

Revision ID: tsk1524_exam_progress
Revises: tsk1500_strict_progression
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "tsk1524_exam_progress"
down_revision: Union[str, None] = "tsk1500_strict_progression"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exam_variant",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("external_uid", sa.String(255), nullable=False, unique=True),
        sa.Column("exam_kind", sa.String(8), nullable=False),
        sa.Column("root_course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("scoring_scale", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("exam_kind IN ('ege','oge')", name="ck_exam_variant_kind"),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_exam_variant_status"),
        sa.UniqueConstraint("root_course_id", "exam_kind", "sequence", name="uq_exam_variant_sequence"),
    )
    op.create_table(
        "exam_variant_item",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("variant_id", sa.BigInteger(), sa.ForeignKey("exam_variant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("exam_number", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("primary_score", sa.Integer(), nullable=False),
        sa.UniqueConstraint("variant_id", "position", name="uq_exam_variant_item_position"),
        sa.UniqueConstraint("variant_id", "task_id", name="uq_exam_variant_item_task"),
    )
    op.create_table(
        "exam_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", sa.BigInteger(), sa.ForeignKey("exam_variant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("variant_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("scale_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("initial_finished_at", sa.DateTime(timezone=True)),
        sa.Column("review_finished_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("active_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('in_progress','paused','review','completed')", name="ck_exam_run_status"),
        sa.UniqueConstraint("student_id", "variant_id", name="uq_exam_run_student_variant"),
    )
    op.create_index("ix_exam_run_student_status", "exam_run", ["student_id", "status"])
    op.create_table(
        "exam_run_session",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("exam_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(16)),
        sa.Column("seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("end_reason IS NULL OR end_reason IN ('pause','initial_finish','stale','logout')", name="ck_exam_run_session_reason"),
    )
    op.create_index("uq_exam_run_session_open", "exam_run_session", ["run_id"], unique=True, postgresql_where=sa.text("ended_at IS NULL"))
    op.create_table(
        "exam_run_item",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("exam_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("exam_number", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("primary_score", sa.Integer(), nullable=False),
        sa.Column("public_content", postgresql.JSONB(), nullable=False),
        sa.Column("checker_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("initial_result_id", sa.Integer(), sa.ForeignKey("task_results.id", ondelete="SET NULL")),
        sa.Column("initial_outcome", sa.String(8)),
        sa.Column("initial_answer", postgresql.JSONB()),
        sa.Column("review_result_id", sa.Integer(), sa.ForeignKey("task_results.id", ondelete="SET NULL")),
        sa.Column("review_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reflection_reason", sa.String(32)),
        sa.Column("reflection_comment", sa.String(500)),
        sa.Column("reflected_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.String(8)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("initial_outcome IS NULL OR initial_outcome IN ('correct','wrong','blank')", name="ck_exam_run_item_initial"),
        sa.CheckConstraint("resolution IS NULL OR resolution IN ('initial','rno','late')", name="ck_exam_run_item_resolution"),
        sa.UniqueConstraint("run_id", "task_id", name="uq_exam_run_item_task"),
    )
    op.create_index("ix_exam_run_item_run", "exam_run_item", ["run_id", "position"])
    op.create_table(
        "exam_answer_request",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("exam_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "client_request_id", name="uq_exam_answer_request"),
    )


def downgrade() -> None:
    op.drop_table("exam_answer_request")
    op.drop_index("ix_exam_run_item_run", table_name="exam_run_item")
    op.drop_table("exam_run_item")
    op.drop_index("uq_exam_run_session_open", table_name="exam_run_session")
    op.drop_table("exam_run_session")
    op.drop_index("ix_exam_run_student_status", table_name="exam_run")
    op.drop_table("exam_run")
    op.drop_table("exam_variant_item")
    op.drop_table("exam_variant")
