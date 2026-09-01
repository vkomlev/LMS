"""tsk-741 фаза 3: домашняя работа — выдача, состав, срок, отметка выполнения.

**Сущности ДЗ в базе не было вовсе.** Смежное `assignment_rule`/`assignment_event`
(tsk-031) — автоназначение КУРСОВ по условию, не домашняя работа. То, что сводка
преподавателя называла «ДЗ за окно» (tsk-022/tsk-473), — это свободная работа
ученика между занятиями: сколько сдал, сколько с первого раза. Плана в ней нет,
и потому нельзя ответить на вопрос «сделал ли он то, что задали».

Две таблицы:

- `homework_assignment` — сама выдача: кому, когда, к какому сроку, кем
  (`source='auto'` — расчёт по темпу и классу, `'teacher'` — рука преподавателя),
  после какого занятия. `planned_volume` хранит НОРМУ, посчитанную формулой на
  момент выдачи: без неё потом не отличить «дали мало» от «сделал мало», а
  формула к тому времени уже посчитает другое число.
- `homework_item` — состав: задание или материал. Материалы здесь наравне с
  заданиями — прямое требование оператора: теорию учат дома, чтобы занятие
  сместилось к практике.

**Отметки «выполнено» в составе НЕТ, и это решение, а не упущение.** Выполнение
выводится из того же источника, где живёт настоящая работа ученика:
`task_results` (верная сдача, не ручной зачёт) и `student_material_progress`.
Своя колонка была бы вторым источником правды: ученик решает задание обычным
путём из кабинета, а не «внутри ДЗ», и хранимая отметка разъехалась бы с
фактом в первый же день — ровно так уже ошибались с производными величинами
(`derived_value_needs_recalc_at_source`).

Rollback: `alembic downgrade tsk741_school_grade_prompt`. Обе таблицы новые и
ничем не связаны с существующими данными, кроме FK на людей, задания и
материалы; откат теряет только выданные ДЗ.

Revision ID: tsk741_homework
Revises: tsk741_school_grade_prompt
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk741_homework"
down_revision: Union[str, None] = "tsk741_school_grade_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создаёт homework_assignment и homework_item."""
    op.create_table(
        "homework_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="Кому выдано",
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Когда выдано",
        ),
        sa.Column(
            "due_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Срок: обычно начало следующего занятия ученика",
        ),
        sa.Column(
            "source",
            sa.String(16),
            nullable=False,
            comment="auto — расчёт по темпу и классу; teacher — выдал преподаватель",
        ),
        sa.Column(
            "issued_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Кто выдал; NULL у автоматической выдачи",
        ),
        sa.Column(
            "occurrence_id",
            sa.Integer(),
            sa.ForeignKey("lesson_occurrence.id", ondelete="SET NULL"),
            nullable=True,
            comment="Занятие, после которого выдано; NULL — выдано вне занятия",
        ),
        sa.Column(
            "planned_volume",
            sa.Integer(),
            nullable=False,
            comment=(
                "Норма, посчитанная формулой на момент выдачи (элементов). "
                "Хранится снимком: иначе «дали мало» не отличить от «сделал мало»"
            ),
        ),
        sa.Column(
            "volume_details",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
            comment=(
                "Из чего сложилась норма: надо/факт/качество/класс/недель до "
                "экзамена. Нужен, чтобы объяснить человеку конкретное число"
            ),
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Выдача отменена преподавателем; NULL — действует",
        ),
        sa.Column("note", sa.Text(), nullable=True, comment="Комментарий преподавателя"),
        comment="tsk-741: выдача домашней работы ученику",
    )
    # Главный запрос — «текущее ДЗ этого ученика»: свежая недоотменённая выдача.
    op.create_index(
        "ix_homework_assignment_student_issued",
        "homework_assignment",
        ["student_id", "issued_at"],
    )
    op.create_index(
        "ix_homework_assignment_occurrence",
        "homework_assignment",
        ["occurrence_id"],
    )
    op.create_check_constraint(
        "ck_homework_assignment_source",
        "homework_assignment",
        "source IN ('auto', 'teacher')",
    )

    op.create_table(
        "homework_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "homework_id",
            sa.Integer(),
            sa.ForeignKey("homework_assignment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind", sa.String(16), nullable=False, comment="task | material"
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "material_id",
            sa.Integer(),
            sa.ForeignKey("materials.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            comment="Порядок в выдаче — учебный, тот же, что в дереве курса",
        ),
        comment="tsk-741: состав домашней работы — задания и материалы",
    )
    op.create_index("ix_homework_item_homework", "homework_item", ["homework_id"])
    # Ровно одна ссылка на элемент и ровно та, что соответствует kind: строка
    # «задание без task_id» или «задание, у которого заодно материал» — это
    # молча испорченная выдача, и ловить её надо базой, а не глазами.
    op.create_check_constraint(
        "ck_homework_item_kind_target",
        "homework_item",
        "(kind = 'task' AND task_id IS NOT NULL AND material_id IS NULL) "
        "OR (kind = 'material' AND material_id IS NOT NULL AND task_id IS NULL)",
    )
    # Дубль элемента внутри одной выдачи ломает счёт «сделано из N».
    op.create_index(
        "uq_homework_item_task",
        "homework_item",
        ["homework_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "uq_homework_item_material",
        "homework_item",
        ["homework_id", "material_id"],
        unique=True,
        postgresql_where=sa.text("material_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Убирает обе таблицы. Существующие данные не затрагиваются."""
    op.drop_table("homework_item")
    op.drop_table("homework_assignment")
