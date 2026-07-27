"""tsk-442: расширенный маппинг ФИО + слияние учётных записей.

Готовит почву под слияние двух аккаунтов (например, «плавающий» ученик,
заведённый вручную по календарю, + аккаунт, который тот же человек создал
сам при самостоятельной регистрации под другим ФИО/через TG). Слияние
переносит данные в один аккаунт и деактивирует второй, а не удаляет —
история (attendance_event, task_results и т.д.) должна остаться читаемой.

`is_active` — по умолчанию true у всех существующих строк (обратная
совместимость). `merged_into_user_id` — self-referential FK, заполняется
только при слиянии (кто "поглотил" эту учётку); NULL у активных.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk442_user_merge_fields"
down_revision: Union[str, None] = "tsk439_auto_joined_action"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true(),
            comment="False — учётка деактивирована (обычно после слияния с другой)",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "merged_into_user_id", sa.Integer(), nullable=True,
            comment="Если учётка слита в другую — id учётки-получателя",
        ),
    )
    op.create_foreign_key(
        "fk_users_merged_into_user_id", "users", "users",
        ["merged_into_user_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_merged_into_user_id", "users", type_="foreignkey")
    op.drop_column("users", "merged_into_user_id")
    op.drop_column("users", "is_active")
