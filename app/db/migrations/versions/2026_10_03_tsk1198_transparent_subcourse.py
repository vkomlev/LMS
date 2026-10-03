"""tsk-1198: «прозрачный» подкурс — признак у связи с родителем + позиция в хозяине.

Подборки практикумов (tsk-1161/1163) — подкурсы внутри курсов банка «Задание N»,
подвешенные вторым родителем к «Практикум ЕГЭ/ОГЭ». В практикуме подборка —
отдельный раздел, а в курсе банка (решение оператора 03.10) её задания должны
идти обычным списком вместе с остальными, после теории, на прежних местах.

Признак живёт у СВЯЗИ (`course_parents.is_transparent`), а не у курса: один и тот
же узел прозрачен для курса банка и остаётся разделом в практикуме.

`tasks.host_order_position` — место задания прозрачного подкурса в списке
заданий курса-хозяина, `место*100 + ранг`: задание встаёт ПЕРЕД заданием
хозяина с `order_position = место`, задания на одном месте — по рангу. Собственный `order_position` задания остаётся порядком внутри
подборки (практикум не меняется). Триггер позиций колонку не трогает.

Без заполнения: разметку делает `scripts/tsk1198_mark_transparent.py`.

Rollback: `alembic downgrade tsk1194_charge_written_off` — колонки снимаются,
подборки снова идут в курсе банка отдельным разделом.

Revision ID: tsk1198_transparent_subcourse
Revises: tsk1194_charge_written_off
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1198_transparent_subcourse"
down_revision: Union[str, None] = "tsk1194_charge_written_off"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "course_parents",
        sa.Column(
            "is_transparent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "tsk-1198: подкурс прозрачен для ЭТОГО родителя — его задания "
                "показываются в списке родителя, отдельного раздела нет"
            ),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "host_order_position",
            sa.Integer(),
            nullable=True,
            comment=(
                "tsk-1198: место в списке заданий курса, для которого подкурс "
                "задания прозрачен (перед заданием хозяина с этим order_position)"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "host_order_position")
    op.drop_column("course_parents", "is_transparent")
