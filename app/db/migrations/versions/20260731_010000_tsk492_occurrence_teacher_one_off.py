"""tsk-492: разовые исключения по преподавателю на ОДНО занятие.

Постоянный состав ведущих живёт в `lesson_slot_teacher`, и генератор занятий
каждый тик досыпает его в будущие `lesson_occurrence` через
`ON CONFLICT DO NOTHING`. Из этого следует главное ограничение: генератор умеет
только добавлять и никогда не удаляет, поэтому «снять преподавателя с одного
занятия» удалением строки НЕ работает — на следующем тике она вернётся.

Отсюда два новых поля:

* `is_active` — «на этом занятии не ведёт» (подмена: заболел, отпуск). Строка
  остаётся на месте и гасится: существующую строку генератор не трогает.
* `is_one_off` — «поставлен на это занятие вручную, а не из состава слота».
  Такие строки переживают снятие преподавателя со СЛОТА: разовое назначение —
  отдельное решение методиста, оптовая чистка по слоту его не касается.

Обратная миграция снимает оба поля; разовые исключения при этом теряются —
других носителей у них нет.

Revision ID: tsk492_occ_teacher_one_off
Revises: tsk433_task_provenance
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk492_occ_teacher_one_off"
down_revision: Union[str, None] = "tsk433_task_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить признаки разового исключения."""
    op.add_column(
        "lesson_occurrence_teacher",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Ведёт ли это занятие: false = разовая подмена, снят только здесь",
        ),
    )
    op.add_column(
        "lesson_occurrence_teacher",
        sa.Column(
            "is_one_off",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Поставлен разово вручную, а не из постоянного состава слота",
        ),
    )
    # Частичный индекс под главный запрос — «кто ведёт это занятие»: разовых
    # исключений мало, гасить полный индекс ради них незачем.
    op.create_index(
        "ix_lesson_occurrence_teacher_active",
        "lesson_occurrence_teacher",
        ["occurrence_id"],
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    """Снять признаки; разовые исключения теряются."""
    op.drop_index(
        "ix_lesson_occurrence_teacher_active", table_name="lesson_occurrence_teacher"
    )
    op.drop_column("lesson_occurrence_teacher", "is_one_off")
    op.drop_column("lesson_occurrence_teacher", "is_active")
