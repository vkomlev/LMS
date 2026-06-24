"""Course assignment trigger rules (tsk-031).

Создаёт фундамент автоматического/ручного назначения курсов ученику:
- assignment_rule  — определения правил (событие → назначить курс по course_uid);
- assignment_event — журнал назначений (provenance + идемпотентность).

Поведение по умолчанию: таблица правил пуста → движок оценки выполняет no-op,
поведение существующих эндпоинтов не меняется.

Revision ID: tsk031_assignment_rules
Revises: tsk111_content_req_skip
Create Date: 2026-06-24 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "tsk031_assignment_rules"
down_revision: Union[str, None] = "tsk111_content_req_skip"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


TRIGGER_EVENT_CHECK = "trigger_event IN ('answer_value', 'task_failed', 'course_failed')"
ACTION_TYPE_CHECK = "action_type IN ('assign_course')"
REFIRE_POLICY_CHECK = "refire_policy IN ('once_per_student', 'every_time')"
SOURCE_CHECK = "source IN ('auto_rule', 'manual_teacher')"


def upgrade() -> None:
    # --- assignment_rule: определения правил ---
    op.create_table(
        "assignment_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False, comment="Устойчивый код правила"),
        sa.Column("title", sa.Text(), nullable=True, comment="Описание для UI/админки"),
        sa.Column(
            "trigger_event",
            sa.Text(),
            nullable=False,
            comment="answer_value | task_failed | course_failed",
        ),
        sa.Column("task_id", sa.Integer(), nullable=True, comment="Отслеживаемая задача"),
        sa.Column("course_id", sa.Integer(), nullable=True, comment="Отслеживаемая тема=курс"),
        sa.Column(
            "condition",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Параметры условия (option_id/value/min_correct_ratio/...)",
        ),
        sa.Column(
            "action_type",
            sa.Text(),
            nullable=False,
            server_default="assign_course",
            comment="Тип действия (пока только assign_course)",
        ),
        sa.Column(
            "target_course_uid",
            sa.Text(),
            nullable=False,
            comment="Курс к назначению по course_uid (wp:<slug>)",
        ),
        sa.Column(
            "refire_policy",
            sa.Text(),
            nullable=False,
            server_default="once_per_student",
            comment="once_per_student | every_time",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE",
            name="assignment_rule_task_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE",
            name="assignment_rule_course_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="assignment_rule_pkey"),
        sa.UniqueConstraint("code", name="assignment_rule_code_key"),
        sa.CheckConstraint(TRIGGER_EVENT_CHECK, name="assignment_rule_trigger_event_check"),
        sa.CheckConstraint(ACTION_TYPE_CHECK, name="assignment_rule_action_type_check"),
        sa.CheckConstraint(REFIRE_POLICY_CHECK, name="assignment_rule_refire_policy_check"),
        comment="Правила автоматического назначения курсов (tsk-031)",
    )
    op.create_index(
        "idx_assignment_rule_task_active",
        "assignment_rule",
        ["task_id", "is_active"],
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "idx_assignment_rule_course_active",
        "assignment_rule",
        ["course_id", "is_active"],
        postgresql_where=sa.text("course_id IS NOT NULL"),
    )
    op.create_index(
        "idx_assignment_rule_event_active",
        "assignment_rule",
        ["trigger_event", "is_active"],
    )

    # --- assignment_event: журнал назначений (provenance + идемпотентность) ---
    op.create_table(
        "assignment_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("assigned_course_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True, comment="NULL = ручное назначение"),
        sa.Column("source", sa.Text(), nullable=False, comment="auto_rule | manual_teacher"),
        sa.Column("assigned_by", sa.Integer(), nullable=True, comment="Учитель (для ручного)"),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("task_result_id", sa.Integer(), nullable=True),
        sa.Column(
            "already_enrolled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Ученик уже был на курсе на момент события",
        ),
        sa.Column("detail", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="assignment_event_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_course_id"], ["courses.id"], ondelete="CASCADE",
            name="assignment_event_assigned_course_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["assignment_rule.id"], ondelete="SET NULL",
            name="assignment_event_rule_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], ondelete="SET NULL",
            name="assignment_event_assigned_by_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], ondelete="SET NULL",
            name="assignment_event_attempt_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="assignment_event_pkey"),
        sa.CheckConstraint(SOURCE_CHECK, name="assignment_event_source_check"),
        comment="Журнал назначений курсов (provenance + идемпотентность, tsk-031)",
    )
    op.create_index(
        "idx_assignment_event_rule_student",
        "assignment_event",
        ["rule_id", "student_id"],
    )
    op.create_index(
        "idx_assignment_event_student",
        "assignment_event",
        ["student_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_assignment_event_student", table_name="assignment_event")
    op.drop_index("idx_assignment_event_rule_student", table_name="assignment_event")
    op.drop_table("assignment_event")

    op.drop_index("idx_assignment_rule_event_active", table_name="assignment_rule")
    op.drop_index("idx_assignment_rule_course_active", table_name="assignment_rule")
    op.drop_index("idx_assignment_rule_task_active", table_name="assignment_rule")
    op.drop_table("assignment_rule")
