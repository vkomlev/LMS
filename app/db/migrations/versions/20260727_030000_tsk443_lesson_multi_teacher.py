"""tsk-443: несколько преподавателей на одном занятии (совместное ведение).

Оператор: ученики должны быть видны сразу всем преподавателям, ведущим одно
и то же занятие (напр. Комлев + Серебрякова на Пн 11:00), а явка — общая
на всех (один пропуск засчитывается, только если ученик не отметился НИ У
КОГО). Выбранная архитектура (подтверждена оператором): ОДНО занятие
(``lesson_occurrence``) на несколько преподавателей, а не отдельное
occurrence на каждого — тогда общая явка получается "бесплатно" (один
список участников, синхронизировать нечего).

Новые M2M-таблицы:
- ``lesson_slot_teacher`` — преподаватели закреплённого слота (шаблон).
- ``lesson_occurrence_teacher`` — преподаватели конкретного занятия
  (заполняется генератором из ``lesson_slot_teacher`` на каждый тик, как
  участники).

``lesson_slot.teacher_id`` / ``lesson_occurrence.teacher_id`` НЕ убираются —
остаются как "создатель/основной преподаватель" (audit, обратная
совместимость путей создания). Реальным источником истины "кто ведёт" после
этой миграции становятся M2M-таблицы — backfill добавляет туда запись для
каждого существующего teacher_id, поэтому поведение всех уже существующих
(одиночных) слотов/занятий не меняется.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk443_lesson_multi_teacher"
down_revision: Union[str, None] = "tsk442_user_merge_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lesson_slot_teacher",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true(),
            comment="Мягкое удаление — сохраняет историю уже сгенерированных occurrence",
        ),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_slot_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_teacher_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_teacher_added_by_fkey",
        ),
        sa.UniqueConstraint("slot_id", "teacher_id", name="uq_lesson_slot_teacher_slot_teacher"),
        comment="Преподаватели закреплённого слота — совместное ведение (tsk-443)",
    )
    op.create_index(
        "ix_lesson_slot_teacher_teacher_id", "lesson_slot_teacher", ["teacher_id"],
    )

    op.create_table(
        "lesson_occurrence_teacher",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurrence_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_occurrence_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_teacher_id_fkey",
        ),
        sa.UniqueConstraint(
            "occurrence_id", "teacher_id", name="uq_lesson_occurrence_teacher_occurrence_teacher",
        ),
        comment="Преподаватели конкретного занятия — совместное ведение (tsk-443)",
    )
    op.create_index(
        "ix_lesson_occurrence_teacher_teacher_id", "lesson_occurrence_teacher", ["teacher_id"],
    )

    # Backfill: у каждого существующего слота/occurrence уже есть ровно один
    # преподаватель (в колонке teacher_id) — переносим его в M2M, чтобы
    # текущее поведение (списки/фильтры теперь идут через M2M) не изменилось.
    op.execute(
        "INSERT INTO lesson_slot_teacher (slot_id, teacher_id) "
        "SELECT id, teacher_id FROM lesson_slot"
    )
    op.execute(
        "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id) "
        "SELECT id, teacher_id FROM lesson_occurrence"
    )


def downgrade() -> None:
    op.drop_index("ix_lesson_occurrence_teacher_teacher_id", table_name="lesson_occurrence_teacher")
    op.drop_table("lesson_occurrence_teacher")
    op.drop_index("ix_lesson_slot_teacher_teacher_id", table_name="lesson_slot_teacher")
    op.drop_table("lesson_slot_teacher")
