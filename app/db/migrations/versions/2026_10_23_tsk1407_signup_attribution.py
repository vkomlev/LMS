"""tsk-1407: источник регистрации пользователя — `users.signup_attribution`.

Метки первого касания (utm_*, page, ветка квиза) жили только в
`guest_session.attribution` и до пользователя не доходили: связи
«сайт → регистрация → оплата» не было. При регистрации из гостевой сессии
метки копируются сюда. Только добавление nullable-колонки, данных не меняет.

Rollback: `alembic downgrade tsk1254_course_purchase` — колонка удаляется
(собранные метки регистраций теряются; в `guest_session` они остаются).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk1407_signup_attribution"
down_revision: Union[str, None] = "tsk1254_course_purchase"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить колонку signup_attribution."""
    op.add_column(
        "users",
        sa.Column(
            "signup_attribution",
            postgresql.JSONB(),
            nullable=True,
            comment="Метки первого касания при регистрации (tsk-1407): utm_*, page, ветка квиза",
        ),
    )


def downgrade() -> None:
    """Удалить колонку signup_attribution."""
    op.drop_column("users", "signup_attribution")
