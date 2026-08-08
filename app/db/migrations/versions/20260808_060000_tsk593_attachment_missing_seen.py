"""tsk-593: журнал уже известных утрат файлов-вложений.

**Зачем таблица.** Суточная проверка целостности («ссылка на файл есть, файла в
хранилище нет») обязана МОЛЧАТЬ на чистом прогоне. Но чистым прогон уже не
будет: 180 файлов вложений утрачено дефектом tsk-575 до его починки, и
восстановить их нечем. Без памяти о них проверка каждый день сообщала бы про
одни и те же 180 потерь — а на уведомление, которое всегда одинаковое,
перестают смотреть, и настоящая новая потеря утонет в нём.

Поэтому здесь хранится ровно один факт: «про эту утрату уже знают». Строки
кладёт сама проверка; исходный уровень (то, что потеряно до переезда) заносит
разовый скрипт `scripts/tsk593_lost_attachments_report.py`. Уведомление уходит
только про то, чего в таблице ещё нет.

**Самолечение.** Если файл вернулся (ученик перезалил) или исчезла сама ссылка
на него — проверка убирает строку. Иначе повторная потеря того же имени уже
никого бы не разбудила.

`space` — пространство ключей хранилища: `attempts` (вложения ответов),
`messages` (переписка), `receipts` (чеки). Отдельной таблицы вложений в базе
нет, поэтому внешних ключей тут тоже нет: `name` — это имя файла в хранилище,
а не ссылка на строку.

**Про откат.** `downgrade` удаляет таблицу целиком: в ней только служебная
память проверки, ничего пользовательского. После отката первая же проверка
сообщит про все известные утраты разом — неприятно, но не потеря данных.

Revision ID: tsk593_missing_seen
Revises: tsk032_retention_ach
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk593_missing_seen"
down_revision: Union[str, None] = "tsk032_retention_ach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attachment_missing_seen",
        sa.Column("space", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Где встретилась ссылка — чтобы по записи можно было выйти на ученика и
        # задание, не разбирая имя файла заново.
        sa.Column("owner_kind", sa.Text, nullable=True),
        sa.Column("owner_id", sa.BigInteger, nullable=True),
        sa.PrimaryKeyConstraint("space", "name", name="pk_attachment_missing_seen"),
        sa.CheckConstraint(
            "space IN ('attempts', 'messages', 'receipts')",
            name="ck_attachment_missing_seen_space",
        ),
    )


def downgrade() -> None:
    op.drop_table("attachment_missing_seen")
