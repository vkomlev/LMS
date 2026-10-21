"""tsk-1253 (этап Б): привязка ВКонтакте и MAX как каналов уведомлений.

Таблица `messenger_link` — куда слать ученику напоминания и сообщения
преподавателя вне кабинета. Отдельно от `identity_link`: та отвечает за ВХОД
(identity `vk` — VK ID, не даёт права писать от сообщества), а эта — за
согласие получать сообщения. Ученик сам нажал «Подключить» и сам написал
сообществу / запустил бота; отписка (`disabled_at`) исполняется сразу, строка
остаётся — видно, когда и откуда отписался.

* `channel` — `vk` (сообщения сообщества, `user_id` ВК) | `max` (бот MAX).
* один внешний аккаунт — один ученик (`uq_messenger_link_external`);
  у ученика — одна привязка на канал (`uq_messenger_link_user_channel`).

Данных не меняет. Rollback: `alembic downgrade tsk996_bank_anchor` — таблица
удаляется вместе с привязками (каналы выключены по умолчанию).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1253_messenger_link"
down_revision: Union[str, None] = "tsk996_bank_anchor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу привязок мессенджеров."""
    op.create_table(
        "messenger_link",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.Text, nullable=True),
        sa.CheckConstraint("channel IN ('vk', 'max')", name="ck_messenger_link_channel"),
        sa.UniqueConstraint("channel", "external_id", name="uq_messenger_link_external"),
        sa.UniqueConstraint("user_id", "channel", name="uq_messenger_link_user_channel"),
    )


def downgrade() -> None:
    """Удалить таблицу привязок."""
    op.drop_table("messenger_link")
