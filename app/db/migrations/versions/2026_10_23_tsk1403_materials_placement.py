"""tsk-1403: признак материала «после подразделов узла» (вариант В, ОГЭ).

Колонка `materials.placement`: 'head' (по умолчанию, как всегда) или
'after_children' — материал встаёт в начало хвоста узла, после подкурсов
(`app/services/node_sequence.py`). Действует только при рубильнике
`NODE_AFTER_CHILDREN_ENABLED`. Данных не меняет: все строки получают 'head'.

Rollback: `alembic downgrade tsk996_tariff_single_active` — колонка удаляется
(разметка 'after_children' теряется).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1403_materials_placement"
down_revision: Union[str, None] = "tsk996_tariff_single_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить колонку placement с ограничением значений."""
    op.add_column(
        "materials",
        sa.Column(
            "placement",
            sa.String(16),
            server_default=sa.text("'head'"),
            nullable=False,
            comment="Место в узле (tsk-1403): head — до подразделов, after_children — после",
        ),
    )
    op.create_check_constraint(
        "ck_materials_placement", "materials", "placement IN ('head', 'after_children')"
    )


def downgrade() -> None:
    """Удалить колонку placement."""
    op.drop_constraint("ck_materials_placement", "materials", type_="check")
    op.drop_column("materials", "placement")
