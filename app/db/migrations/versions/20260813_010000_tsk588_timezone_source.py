"""tsk-588: откуда взялся часовой пояс пользователя — из браузера или от человека.

Пояс (`users.timezone`, tsk-427) заполнен у 3 из 52 активных пользователей,
потому что его надо было вписать руками. Клиент теперь присылает системный пояс
браузера сам, и появляется вопрос, который до этого не стоял: можно ли
перезаписывать значение, которое человек выбрал осознанно.

Решение оператора (tsk-588, 2026-08-08): **ручная правка имеет приоритет** —
автозахват не перетирает выбор человека молча. Чтобы это различать, нужен
источник значения:

- `manual` — вписано человеком (профиль ученика, карточка в кабинете методиста);
  автозахват такое значение НЕ трогает;
- `auto` — снято с браузера; автозахват обновляет его при переезде/смене пояса;
- `NULL` — пояс не заполнен вовсе (`timezone IS NULL`).

Backfill: все уже заполненные пояса (их 3 на проде) проставляются `manual` —
они вписаны руками через кабинет, и переписывать их автозахватом нельзя.

Revision ID: tsk588_timezone_source
Revises: tsk593_missing_seen
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk588_timezone_source"
down_revision: Union[str, None] = "tsk593_missing_seen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет `users.timezone_source` и помечает уже заполненные пояса как ручные."""
    op.add_column(
        "users",
        sa.Column(
            "timezone_source",
            sa.String(16),
            nullable=True,
            comment=(
                "tsk-588: откуда взят users.timezone — manual (вписал человек, "
                "автозахват не трогает) или auto (снят с браузера). "
                "NULL = пояс не заполнен."
            ),
        ),
    )
    op.create_check_constraint(
        "ck_users_timezone_source",
        "users",
        "timezone_source IS NULL OR timezone_source IN ('auto', 'manual')",
    )
    # Пояс без источника — это значение, вписанное руками до появления
    # автозахвата. Пометить его 'auto' означало бы разрешить клиенту молча
    # его переписать, чего решение оператора прямо запрещает.
    op.execute(
        "UPDATE users SET timezone_source = 'manual' "
        "WHERE timezone IS NOT NULL AND timezone_source IS NULL"
    )


def downgrade() -> None:
    """Убирает constraint и колонку `timezone_source`.

    Rollback-note: сам пояс (`users.timezone`) не трогается — теряется только
    знание о том, откуда он взялся. После отката автозахват (если код тоже
    откатан) снова не отличает ручной выбор от снятого с браузера, поэтому
    откатывать код и БД надо вместе.
    """
    op.drop_constraint("ck_users_timezone_source", "users", type_="check")
    op.drop_column("users", "timezone_source")
