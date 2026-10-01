"""tsk-1194: исход «принят уход без оплаты» у начисления ушедшего ученика.

Политика школы (оператор 01.10): ушёл и не заплатил — не заплатит; такой долг
искажает картину и в итоги, долги, напоминания и рассылки не входит. Строка и
сумма остаются в истории — это отметка, а не удаление. Пришедшая позже оплата
закрывает остаток и тем самым сама переводит строку в оплаченные.

Без заполнения: уже ушедших переводит отдельный проход сервиса
(`charge_writeoff_service.write_off_alumni_debts`) с контрольным слепком.

Rollback: `alembic downgrade tsk1192_task_external_uid` — колонки снимаются, отметки
теряются, долги ушедших снова видны в итогах и рассылках.

Revision ID: tsk1194_charge_written_off
Revises: tsk1192_task_external_uid
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1194_charge_written_off"
down_revision: Union[str, None] = "tsk1192_task_external_uid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "student_monthly_charge",
        sa.Column("written_off_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "student_monthly_charge",
        sa.Column(
            "written_off_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("student_monthly_charge", "written_off_by")
    op.drop_column("student_monthly_charge", "written_off_at")
