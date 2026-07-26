"""tsk-428 (Календарь LMS, Фаза 1): модель данных расписания и посещаемости.

Revision ID: tsk428_lesson_calendar_stage1
Revises: tsk235_session_replaced_by
Create Date: 2026-07-26

Фундамент Фазы 1 плана `docs/specs/2026-07-26-plan-kalendar-lms.md`:

- operating_hours   — часы работы школы (общие для всей школы, не per-teacher).
- lesson_slot       — закреплённый повторяющийся слот пары ученик-преподаватель.
- lesson_occurrence — конкретное занятие (сгенерировано из слота или ad-hoc).
- attendance_event  — append-only журнал действий по посещаемости occurrence.

Конвенция weekday (везде в этих 2 таблицах): 0=понедельник .. 6=воскресенье
(совпадает с Python `date.weekday()` / ISO, НЕ с cron/JS, где 0=воскресенье).

Поведение по умолчанию: 4 новые таблицы, ноль изменений в существующих
моделях. Пустые таблицы → ни один существующий эндпоинт не меняет поведение.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk428_lesson_calendar_stage1"
down_revision: Union[str, None] = "tsk235_session_replaced_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OCCURRENCE_STATUS_CHECK = (
    "status IN ('scheduled', 'confirmed', 'declined', 'rescheduled', "
    "'no_show', 'completed')"
)
ATTENDANCE_ACTION_CHECK = (
    "action IN ('joined', 'declined', 'manual_present', 'manual_absent', "
    "'auto_no_show')"
)
WEEKDAY_CHECK = "weekday BETWEEN 0 AND 6"


def upgrade() -> None:
    # --- operating_hours: часы работы школы (общие, не per-teacher) ---
    op.create_table(
        "operating_hours",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "weekday", sa.SmallInteger(), nullable=False,
            comment="0=понедельник .. 6=воскресенье (Python date.weekday())",
        ),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column(
            "timezone", sa.Text(), nullable=False,
            server_default=sa.text("'Europe/Moscow'"),
            comment="IANA timezone; MVP — одна зона на всю школу",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="operating_hours_pkey"),
        sa.CheckConstraint(WEEKDAY_CHECK, name="operating_hours_weekday_check"),
        sa.CheckConstraint(
            "end_time > start_time", name="operating_hours_time_order_check"
        ),
        comment="Часы работы школы, в рамках которых доступна гибкая отработка (tsk-428)",
    )

    # --- lesson_slot: закреплённый повторяющийся слот пары ученик-преподаватель ---
    op.create_table(
        "lesson_slot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column(
            "weekday", sa.SmallInteger(), nullable=False,
            comment="0=понедельник .. 6=воскресенье (Python date.weekday())",
        ),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "timezone", sa.Text(), nullable=False,
            server_default=sa.text("'Europe/Moscow'"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.true(),
            comment="Деактивация вместо удаления — сохраняет историю occurrence",
        ),
        sa.Column(
            "created_by", sa.Integer(), nullable=True,
            comment="Admin/оператор, создавший слот",
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
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_created_by_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="lesson_slot_pkey"),
        sa.CheckConstraint(
            "student_id <> teacher_id", name="lesson_slot_student_teacher_distinct_check"
        ),
        sa.CheckConstraint(WEEKDAY_CHECK, name="lesson_slot_weekday_check"),
        sa.CheckConstraint(
            "duration_minutes > 0", name="lesson_slot_duration_positive_check"
        ),
        comment="Закреплённый повторяющийся слот пары ученик-преподаватель (tsk-428)",
    )
    op.create_index(
        "idx_lesson_slot_teacher_active",
        "lesson_slot",
        ["teacher_id", "weekday", "start_time"],
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "idx_lesson_slot_student_active",
        "lesson_slot",
        ["student_id", "weekday", "start_time"],
        postgresql_where=sa.text("is_active"),
    )

    # --- lesson_occurrence: конкретное занятие (из слота или ad-hoc) ---
    op.create_table(
        "lesson_occurrence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "slot_id", sa.Integer(), nullable=True,
            comment="NULL = ad-hoc отработка вне регулярного расписания",
        ),
        sa.Column(
            "student_id", sa.Integer(), nullable=False,
            comment="Денормализовано из слота — устойчиво к будущей деактивации слота",
        ),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False,
            server_default=sa.text("'scheduled'"),
        ),
        sa.Column(
            "rescheduled_to_id", sa.Integer(), nullable=True,
            comment="Цепочка переноса: занятие, на которое перенесено это",
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
            ["slot_id"], ["lesson_slot.id"], ondelete="SET NULL",
            name="lesson_occurrence_slot_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["rescheduled_to_id"], ["lesson_occurrence.id"], ondelete="SET NULL",
            name="lesson_occurrence_rescheduled_to_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="lesson_occurrence_pkey"),
        sa.CheckConstraint(OCCURRENCE_STATUS_CHECK, name="lesson_occurrence_status_check"),
        sa.CheckConstraint(
            "duration_minutes > 0", name="lesson_occurrence_duration_positive_check"
        ),
        comment="Конкретное занятие: из слота (генератор) или ad-hoc отработка (tsk-428)",
    )
    # Идемпотентность генератора: повторный тик не плодит дубли для одного
    # слота на одно и то же время. Partial — ad-hoc (slot_id IS NULL) не участвует.
    op.create_index(
        "uq_lesson_occurrence_slot_scheduled_at",
        "lesson_occurrence",
        ["slot_id", "scheduled_at"],
        unique=True,
        postgresql_where=sa.text("slot_id IS NOT NULL"),
    )
    op.create_index(
        "idx_lesson_occurrence_teacher_time",
        "lesson_occurrence",
        ["teacher_id", "scheduled_at"],
    )
    op.create_index(
        "idx_lesson_occurrence_student_time",
        "lesson_occurrence",
        ["student_id", "scheduled_at"],
    )
    # Cron-тики (Фаза 2): сканы по статусу+времени без полного скана таблицы.
    op.create_index(
        "idx_lesson_occurrence_status_time",
        "lesson_occurrence",
        ["status", "scheduled_at"],
    )

    # --- attendance_event: append-only журнал действий по посещаемости ---
    op.create_table(
        "attendance_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurrence_id", sa.Integer(), nullable=False),
        sa.Column(
            "actor_user_id", sa.Integer(), nullable=True,
            comment="Кто совершил действие; NULL для auto_no_show (система)",
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="attendance_event_occurrence_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL",
            name="attendance_event_actor_user_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="attendance_event_pkey"),
        sa.CheckConstraint(ATTENDANCE_ACTION_CHECK, name="attendance_event_action_check"),
        comment="Append-only журнал действий по посещаемости occurrence (tsk-428)",
    )
    op.create_index(
        "idx_attendance_event_occurrence",
        "attendance_event",
        ["occurrence_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_attendance_event_occurrence", table_name="attendance_event")
    op.drop_table("attendance_event")

    op.drop_index("idx_lesson_occurrence_status_time", table_name="lesson_occurrence")
    op.drop_index("idx_lesson_occurrence_student_time", table_name="lesson_occurrence")
    op.drop_index("idx_lesson_occurrence_teacher_time", table_name="lesson_occurrence")
    op.drop_index(
        "uq_lesson_occurrence_slot_scheduled_at", table_name="lesson_occurrence"
    )
    op.drop_table("lesson_occurrence")

    op.drop_index("idx_lesson_slot_student_active", table_name="lesson_slot")
    op.drop_index("idx_lesson_slot_teacher_active", table_name="lesson_slot")
    op.drop_table("lesson_slot")

    op.drop_table("operating_hours")
