"""tsk-1398: отписка от уведомлений в Telegram.

Привязка Telegram живёт в `users.tg_id` / identity `tg` (вход и бот), а
отписку от уведомлений хранить негде. Разрешаем в `messenger_link` канал `tg`:
строка с `disabled_at` — «не слать в Telegram» (слово «стоп», остановка бота,
кнопка в кабинете). Новая привязка по ссылке из кабинета строку снимает.

Данных не меняет. Rollback: `alembic downgrade tsk1398_absence_poll` —
строки `tg` удаляются, ограничение возвращается к ('vk', 'max').
"""
from typing import Sequence, Union

from alembic import op

revision: str = "tsk1398_messenger_link_tg"
down_revision: Union[str, None] = "tsk1398_absence_poll"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Разрешить канал tg."""
    op.drop_constraint("ck_messenger_link_channel", "messenger_link", type_="check")
    op.create_check_constraint("ck_messenger_link_channel", "messenger_link", "channel IN ('vk', 'max', 'tg')")


def downgrade() -> None:
    """Вернуть ограничение без tg."""
    op.execute("DELETE FROM messenger_link WHERE channel = 'tg'")
    op.drop_constraint("ck_messenger_link_channel", "messenger_link", type_="check")
    op.create_check_constraint("ck_messenger_link_channel", "messenger_link", "channel IN ('vk', 'max')")
