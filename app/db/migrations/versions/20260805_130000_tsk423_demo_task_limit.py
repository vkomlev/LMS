"""tsk-423: лимит гостевых заданий на демо-курс (per-подкурс).

Живой прогон гостевого доступа (Y-5, tsk-423) начинался без лимита — курс
`is_public_demo=true` открывался гостю целиком. Для пилотного «Пробного
занятия» (курс 651) это осознанное решение оператора: намеренная бесплатная
приманка. Для остальных (платных) курсов оператор решил вернуть лимит по
ЗАДАНИЯМ (не материалам — см. tsk-423 «История движения» 2026-08-05: задания
и материалы в БД не связаны, гость технически взаимодействует только с
заданиями).

NULL (default) — прежнее поведение: демо-доступ без лимита (курс 651 остаётся
NULL, ничего для него не меняется). Целое число N — гость может ПРОВЕРИТЬ
ответ максимум на N РАЗНЫХ заданий этого курса; повторные попытки на уже
использованном задании в лимит не считаются (см.
`learning_guest_service.submit_guest_attempt`).

Revision ID: tsk423_demo_task_limit
Revises: tsk114_task_audit
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk423_demo_task_limit"
down_revision: Union[str, None] = "tsk114_task_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет nullable-колонку лимита гостевых заданий на подкурс."""
    op.add_column(
        "courses",
        sa.Column(
            "demo_task_limit",
            sa.Integer(),
            nullable=True,
            comment=(
                "tsk-423: макс. число РАЗНЫХ заданий, которые гость может "
                "проверить в этом is_public_demo-курсе. NULL = без лимита "
                "(прежнее поведение)."
            ),
        ),
    )


def downgrade() -> None:
    """Убирает колонку лимита гостевых заданий."""
    op.drop_column("courses", "demo_task_limit")
