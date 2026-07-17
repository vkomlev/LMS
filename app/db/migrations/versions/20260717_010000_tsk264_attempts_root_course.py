"""Попытки по паре «курс + задание»: контекст навигации в попытке (tsk-264).

Узел графа курсов переиспользуется несколькими корнями (из 645 узлов — 24, до 5
родителей). ``attempts.course_id`` хранит курс САМОГО задания, а не путь, которым
ученик пришёл, поэтому для переиспользуемого узла он одинаков при любом пути и
разделить попытки по нему нельзя. Добавляем ``attempts.root_course_id`` —
корневой курс дерева, в котором ученик находился, когда открыл попытку.

Бэкфилл (детерминированный, только однозначные случаи):
1. Узел входит ровно в одно дерево графа → этот корень.
2. Иначе узел входит ровно в одно АКТИВНОЕ дерево ученика → этот корень.
3. Иначе (узел под несколькими активными корнями ученика) → NULL: путь не
   записывался и восстановить его нечем. NULL означает «путь неизвестен» и не
   расходует лимит попыток ни в одном корне (см. ``compute_task_state``).

Revision ID: tsk264_attempts_root_course
Revises: tsk122_trigger_quiz_scale
Create Date: 2026-07-17 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "tsk264_attempts_root_course"
down_revision: Union[str, None] = "tsk122_trigger_quiz_scale"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


# Дерево «корень → все потомки» строится по course_parents. Корень — курс без
# родителей. Тот же обход, что у LearningEngineService (course_parents —
# единственный источник вложенности).
_BACKFILL_SQL = """
WITH RECURSIVE ct AS (
    SELECT c.id AS root_course_id, c.id AS member_course_id
    FROM courses c
    WHERE NOT EXISTS (
        SELECT 1 FROM course_parents cp WHERE cp.course_id = c.id
    )
    UNION ALL
    SELECT ct.root_course_id, cp.course_id
    FROM ct
    JOIN course_parents cp ON cp.parent_course_id = ct.member_course_id
),
tree AS (
    SELECT DISTINCT root_course_id, member_course_id FROM ct
),
-- (1) узел входит ровно в одно дерево графа — корень однозначен без учёта записей
single_root AS (
    SELECT member_course_id, MIN(root_course_id) AS root_course_id
    FROM tree
    GROUP BY member_course_id
    HAVING COUNT(*) = 1
),
-- (2) узел под несколькими корнями, но активное дерево ученика ровно одно
single_active_root AS (
    SELECT a.id AS attempt_id, MIN(t.root_course_id) AS root_course_id
    FROM attempts a
    JOIN tree t ON t.member_course_id = a.course_id
    JOIN user_courses uc
      ON uc.course_id = t.root_course_id
     AND uc.user_id = a.user_id
     AND uc.is_active = true
    GROUP BY a.id
    HAVING COUNT(DISTINCT t.root_course_id) = 1
)
UPDATE attempts a
SET root_course_id = COALESCE(sr.root_course_id, sar.root_course_id)
FROM attempts src
LEFT JOIN single_root sr ON sr.member_course_id = src.course_id
LEFT JOIN single_active_root sar ON sar.attempt_id = src.id
WHERE a.id = src.id
  AND a.course_id IS NOT NULL
  AND a.root_course_id IS NULL
  AND COALESCE(sr.root_course_id, sar.root_course_id) IS NOT NULL
"""


def upgrade() -> None:
    op.add_column(
        "attempts",
        sa.Column(
            "root_course_id",
            sa.Integer(),
            nullable=True,
            comment=(
                "Корневой курс дерева, которым ученик пришёл к заданию (tsk-264). "
                "NULL — путь неизвестен: попытка не расходует лимит ни в одном корне."
            ),
        ),
    )
    op.create_foreign_key(
        "attempts_root_course_id_fkey",
        "attempts",
        "courses",
        ["root_course_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_attempts_user_course_root",
        "attempts",
        ["user_id", "course_id", "root_course_id"],
    )
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    # Rollback-note: снос колонки теряет записанный контекст навигации —
    # счёт попыток возвращается к прежнему («по заданию, независимо от пути»).
    # Данных, кроме контекста, миграция не меняет: бэкфилл пишет только в
    # добавленную колонку, ни один существующий столбец не трогается.
    op.drop_index("idx_attempts_user_course_root", table_name="attempts")
    op.drop_constraint("attempts_root_course_id_fkey", "attempts", type_="foreignkey")
    op.drop_column("attempts", "root_course_id")
