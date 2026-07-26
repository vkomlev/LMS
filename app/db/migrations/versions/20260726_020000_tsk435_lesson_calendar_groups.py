"""tsk-435 (Календарь LMS): rework на групповые слоты/occurrence.

Revision ID: tsk435_lesson_calendar_groups
Revises: tsk428_lesson_calendar_stage1
Create Date: 2026-07-26

Реальные данные (импорт Яндекс.Календаря оператора) показали, что живое
расписание в основном ГРУППОВОЕ (2-11 учеников на одно время с одним
учителем) — вразрез с правилом «индивидуальное» из Фазы 1. Оператор выбрал
полноценный rework вместо обхода (см. tsk-435).

Breaking-миграция допустима: на момент написания все 4 таблицы Фазы 1-3
были пусты и на dev, и на prod (проверено независимо через MCP) — ни одной
реальной строки не теряется.

Изменения:
- `lesson_slot`: убрать `student_id` (+ FK + CHECK) — слот больше не привязан
  к одному ученику, участники — в `lesson_slot_student`.
- `lesson_occurrence`: убрать `student_id`, `status`, `rescheduled_to_id`
  (+ их FK/CHECK) — статус явки и перенос теперь на уровне участника
  (`lesson_occurrence_participant`), не всего occurrence.
- Новая `lesson_slot_student` — M2M слот↔ученик, `is_active` (мягкое
  удаление, как у `lesson_slot.is_active`).
- Новая `lesson_occurrence_participant` — статус явки и цепочка переноса
  ПО КАЖДОМУ участнику независимо (несколько участников одного occurrence
  могут быть в разных статусах одновременно).
- `attendance_event` НЕ меняется: уже ключуется по (`occurrence_id`,
  `actor_user_id`) — по конструкции подходит для группового участника без
  правок (несколько participant одного occurrence просто дают несколько
  разных `actor_user_id` на тот же `occurrence_id`).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk435_lesson_calendar_groups"
down_revision: Union[str, None] = "tsk428_lesson_calendar_stage1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PARTICIPANT_STATUS_CHECK = (
    "status IN ('scheduled', 'confirmed', 'declined', 'rescheduled', "
    "'no_show', 'completed')"
)


def upgrade() -> None:
    # --- lesson_slot: убрать student_id (участники — в lesson_slot_student) ---
    op.drop_index("idx_lesson_slot_student_active", table_name="lesson_slot")
    op.drop_constraint(
        "lesson_slot_student_teacher_distinct_check", "lesson_slot", type_="check"
    )
    op.drop_constraint(
        "lesson_slot_student_id_fkey", "lesson_slot", type_="foreignkey"
    )
    op.drop_column("lesson_slot", "student_id")

    # --- lesson_occurrence: убрать student_id, status, rescheduled_to_id ---
    op.drop_index("idx_lesson_occurrence_status_time", table_name="lesson_occurrence")
    op.drop_index("idx_lesson_occurrence_student_time", table_name="lesson_occurrence")
    op.drop_constraint(
        "lesson_occurrence_status_check", "lesson_occurrence", type_="check"
    )
    op.drop_constraint(
        "lesson_occurrence_rescheduled_to_id_fkey", "lesson_occurrence", type_="foreignkey"
    )
    op.drop_constraint(
        "lesson_occurrence_student_id_fkey", "lesson_occurrence", type_="foreignkey"
    )
    op.drop_column("lesson_occurrence", "rescheduled_to_id")
    op.drop_column("lesson_occurrence", "status")
    op.drop_column("lesson_occurrence", "student_id")

    # --- lesson_slot_student: участники закреплённого слота (M2M) ---
    op.create_table(
        "lesson_slot_student",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.true(),
            comment="Мягкое удаление участника из слота — сохраняет историю occurrence",
        ),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="CASCADE",
            name="lesson_slot_student_slot_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_student_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_student_added_by_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="lesson_slot_student_pkey"),
        sa.UniqueConstraint("slot_id", "student_id", name="uq_lesson_slot_student_slot_student"),
        comment="Участники закреплённого группового слота (tsk-435)",
    )
    op.create_index(
        "idx_lesson_slot_student_slot_active",
        "lesson_slot_student",
        ["slot_id", "is_active"],
    )
    op.create_index(
        "idx_lesson_slot_student_student_active",
        "lesson_slot_student",
        ["student_id", "is_active"],
    )

    # --- lesson_occurrence_participant: явка ПО КАЖДОМУ участнику ---
    op.create_table(
        "lesson_occurrence_participant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurrence_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False,
            server_default=sa.text("'scheduled'"),
        ),
        sa.Column(
            "rescheduled_to_occurrence_id", sa.Integer(), nullable=True,
            comment="Новое occurrence, на которое этот участник перенёс явку",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_occurrence_participant_occurrence_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_participant_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["rescheduled_to_occurrence_id"], ["lesson_occurrence.id"], ondelete="SET NULL",
            name="lesson_occurrence_participant_rescheduled_to_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="lesson_occurrence_participant_pkey"),
        sa.UniqueConstraint(
            "occurrence_id", "student_id", name="uq_lesson_occurrence_participant_occ_student"
        ),
        sa.CheckConstraint(PARTICIPANT_STATUS_CHECK, name="lesson_occurrence_participant_status_check"),
        comment="Явка по каждому участнику occurrence независимо (tsk-435)",
    )
    op.create_index(
        "idx_locc_participant_occurrence",
        "lesson_occurrence_participant",
        ["occurrence_id"],
    )
    op.create_index(
        "idx_locc_participant_student_status",
        "lesson_occurrence_participant",
        ["student_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_locc_participant_student_status", table_name="lesson_occurrence_participant")
    op.drop_index("idx_locc_participant_occurrence", table_name="lesson_occurrence_participant")
    op.drop_table("lesson_occurrence_participant")

    op.drop_index("idx_lesson_slot_student_student_active", table_name="lesson_slot_student")
    op.drop_index("idx_lesson_slot_student_slot_active", table_name="lesson_slot_student")
    op.drop_table("lesson_slot_student")

    op.add_column("lesson_occurrence", sa.Column("student_id", sa.Integer(), nullable=True))
    op.add_column(
        "lesson_occurrence",
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'scheduled'")),
    )
    op.add_column("lesson_occurrence", sa.Column("rescheduled_to_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "lesson_occurrence_student_id_fkey", "lesson_occurrence", "users",
        ["student_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "lesson_occurrence_rescheduled_to_id_fkey", "lesson_occurrence", "lesson_occurrence",
        ["rescheduled_to_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "lesson_occurrence_status_check", "lesson_occurrence",
        "status IN ('scheduled', 'confirmed', 'declined', 'rescheduled', 'no_show', 'completed')",
    )
    op.create_index(
        "idx_lesson_occurrence_student_time", "lesson_occurrence", ["student_id", "scheduled_at"]
    )
    op.create_index(
        "idx_lesson_occurrence_status_time", "lesson_occurrence", ["status", "scheduled_at"]
    )

    op.add_column("lesson_slot", sa.Column("student_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "lesson_slot_student_id_fkey", "lesson_slot", "users",
        ["student_id"], ["id"], ondelete="CASCADE",
    )
    op.create_check_constraint(
        "lesson_slot_student_teacher_distinct_check", "lesson_slot",
        "student_id <> teacher_id",
    )
    op.create_index(
        "idx_lesson_slot_student_active", "lesson_slot", ["student_id", "weekday", "start_time"],
        postgresql_where=sa.text("is_active"),
    )
