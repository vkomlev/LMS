"""tsk-994 (П5): итоговый срез после курса — рубильник и прогоны.

Решение оператора Р4: срез — только диагностика. Статус курса, зависимости и
выпуск он не меняет; ответы пишутся в `task_results` с
`source_system='final_check'` и из расчётов учебного пути исключены.

- `courses.final_check_config JSONB NULL` — рубильник на корневом курсе по
  образцу `retrieval_config`: `{"enabled": false, "size": 10}`. NULL и
  `enabled=false` значат одно и то же — среза нет, поведение прежнее.
- `final_check_runs` — одна строка на (ученик, корневой курс): состав
  заданий (фиксируется при первом открытии), ответы, итог.

Обе части только добавляются: старые строки и старые клиенты не затронуты.

Rollback: `alembic downgrade tsk992_task_review_schedule` — таблица и колонка
снимаются, прогоны срезов теряются (сами ответы в `task_results` с
`source_system='final_check'` остаются и по-прежнему не входят в учебный путь).

Revision ID: tsk994_final_check
Revises: tsk992_task_review_schedule
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk994_final_check"
down_revision: Union[str, None] = "tsk992_task_review_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать `final_check_runs` и колонку `courses.final_check_config`."""
    op.add_column(
        "courses",
        sa.Column(
            "final_check_config",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "Итоговый срез после курса (tsk-994): {enabled, size}. "
                "NULL/enabled=false — выключено."
            ),
        ),
    )
    op.create_table(
        "final_check_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            sa.Integer(),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
            comment="Корневой курс, по которому проходит срез",
        ),
        sa.Column(
            "task_ids",
            postgresql.JSONB(),
            nullable=False,
            comment="Состав среза — id заданий в порядке показа",
        ),
        sa.Column(
            "answers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Ответы среза: {task_id: {result_id, is_correct}}",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда отвечено последнее задание среза",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=True,
            comment="Итог: доля верных, главы с ошибками, что поставлено на повтор",
        ),
        sa.UniqueConstraint(
            "student_id", "course_id", name="uq_final_check_runs_student_course"
        ),
        comment="Итоговый срез после курса: состав заданий и итог (tsk-994)",
    )


def downgrade() -> None:
    """Снять таблицу прогонов среза и рубильник курса."""
    op.drop_table("final_check_runs")
    op.drop_column("courses", "final_check_config")
