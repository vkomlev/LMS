"""tsk-999 (П10): рефлексия ученика после ответа на заявку помощи.

Решение оператора Р2: после ЛЮБОГО ответа на заявку (текстовый ответ
преподавателя или ссылка на индивидуальный разбор) ученик может коротко
написать, что было главным и что осталось непонятным, и отметить «понятно?».
Ответ необязателен; принимается один раз.

Колонки `help_requests`:

- `reflection_main` — «что было главным», текст ученика (≤ 500 символов,
  ограничение — в схеме запроса);
- `reflection_unclear` — «что осталось непонятным» (≤ 500);
- `reflection_understood` — «понятно?» да/нет/не ответил. Отдельно от
  `review_understood`: та колонка — развилка маршрута индивидуального разбора
  (false уводит заявку методисту), и записать в неё ответ по обычной заявке
  значило бы закрыть ученику оценку разбора, если заявка потом до него дойдёт;
- `reflection_at` — когда ученик ответил или нажал «Пропустить»; признак
  «рефлексия уже была» (повтор → 409 `already_reflected`).

`review_understood` не трогается. Все колонки необязательные, без значения
по умолчанию — старые строки и старые клиенты не затронуты.

Rollback: `alembic downgrade tsk995_help_due_backfill` — колонки снимаются,
тексты рефлексии теряются (сами заявки остаются).

Revision ID: tsk999_help_reflection
Revises: tsk995_help_due_backfill
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk999_help_reflection"
down_revision: Union[str, None] = "tsk995_help_due_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить колонки рефлексии в `help_requests`."""
    op.add_column(
        "help_requests",
        sa.Column(
            "reflection_main",
            sa.Text(),
            nullable=True,
            comment="Рефлексия ученика: что было главным в ответе (tsk-999).",
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "reflection_unclear",
            sa.Text(),
            nullable=True,
            comment="Рефлексия ученика: что осталось непонятным (tsk-999).",
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "reflection_understood",
            sa.Boolean(),
            nullable=True,
            comment="Рефлексия ученика: «понятно?»; NULL — не ответил (tsk-999).",
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "reflection_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда ученик оставил рефлексию или пропустил её (tsk-999).",
        ),
    )


def downgrade() -> None:
    """Снять колонки рефлексии."""
    op.drop_column("help_requests", "reflection_at")
    op.drop_column("help_requests", "reflection_understood")
    op.drop_column("help_requests", "reflection_unclear")
    op.drop_column("help_requests", "reflection_main")
