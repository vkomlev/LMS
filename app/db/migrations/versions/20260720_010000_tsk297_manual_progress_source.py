"""Провенанс отметки материала: `student_material_progress.source` (tsk-297).

Штатная правка прогресса преподавателем должна быть ОБРАТИМОЙ и не затирать
реальное прохождение ученика. Для заданий провенанс уже есть
(`attempts.source_system` / `task_results.source_system`), а у материалов колонок
провенанса нет вовсе — только `student_id, material_id, status, completed_at,
skipped_at`. Без источника снятие отметки не отличило бы «поставил преподаватель»
от «прошёл сам» и удаляло бы чужой прогресс.

Добавляем `source VARCHAR(32) NOT NULL DEFAULT 'system'` + CHECK на закрытый набор
значений. Дефолт покрывает уже существующие строки без отдельного бэкфилла:
всё, что записано до этой миграции, — прохождение самого ученика ('system').

Revision ID: tsk297_manual_progress_source
Revises: tsk264_attempts_root_course
Create Date: 2026-07-20 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "tsk297_manual_progress_source"
down_revision: Union[str, None] = "tsk264_attempts_root_course"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_CHECK_NAME = "ck_student_material_progress_source"


def upgrade() -> None:
    op.add_column(
        "student_material_progress",
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'system'"),
            comment=(
                "Провенанс отметки (tsk-297): 'system' — прохождение самого ученика, "
                "'manual_teacher' — зачёт поставлен преподавателем/методистом вручную. "
                "Снятие зачёта удаляет только строки 'manual_teacher'."
            ),
        ),
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "student_material_progress",
        "source IN ('system', 'manual_teacher')",
    )


def downgrade() -> None:
    # Rollback-note: снос колонки теряет провенанс отметок. Строки, поставленные
    # вручную, останутся как обычные 'completed' — прогресс ученика не пострадает,
    # но отличить ручной зачёт от реального прохождения станет нечем.
    # Ни один существующий столбец миграция не трогает.
    op.drop_constraint(
        _CHECK_NAME, "student_material_progress", type_="check"
    )
    op.drop_column("student_material_progress", "source")
