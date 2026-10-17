"""tsk-995 (П6): срок ответа у уже открытых заявок помощи.

До tsk-995 `help_requests.due_at` код не писал ни в одном пути создания — счётчик
«Просрочено» был всегда 0. С tsk-995 новые открытые заявки получают
`due_at = created_at + HELP_SLA_HOURS` в коде; эта миграция разово ставит срок
заявкам, которые открыты на момент выката.

Срок — ОТ МОМЕНТА МИГРАЦИИ, а не от `created_at`:
`due_at = greatest(created_at, now()) + HELP_SLA_HOURS`. Иначе все старые
открытые заявки (их на проде может быть много — заявка остаётся открытой, пока
её не закроет ученик) разом стали бы «просроченными» в день выката, и счётчик
испугал бы преподавателей числом, за которым нет новой работы (риск из
постановки `docs/briefs/2026-10-16-p6-p9-p10-review-sla-checkpoint-reflection.md`).
Через `HELP_SLA_HOURS` после выката то, что осталось без ответа, станет
просроченным честно.

Часы — из той же настройки `HELP_SLA_HOURS` (`app/core/config.py`, по умолчанию
24), что и у кода. Закрытые заявки и заявки с уже заполненным `due_at` не трогаются.

Откат. Чтобы `downgrade` вернул `NULL` ТОЛЬКО строкам, заполненным здесь,
миграция записывает их в служебную таблицу `help_request_due_backfill`
(`request_id`, поставленный `due_at`). Откат обнуляет `due_at` у этих строк,
если значение с тех пор не менялось (возврат заявки или перевод в разбор
ставят новый срок — его откат не трогает), и снимает таблицу. Сроки, которые
поставил код после выката, откат миграции не трогает.

Таблица `help_request_due_backfill` служебная: модели у неё нет намеренно, код
её не читает. `alembic revision --autogenerate` предложит её удалить — это
предложение отклонять (удалить её можно только вместе с откатом этой миграции).

Revision ID: tsk995_help_due_backfill
Revises: tsk1361_requirement_sync
Create Date: 2026-10-17
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import Settings

revision: str = "tsk995_help_due_backfill"
down_revision: Union[str, None] = "tsk1361_requirement_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    """Поставить срок открытым заявкам без срока и запомнить, каким строкам."""
    op.create_table(
        "help_request_due_backfill",
        sa.Column(
            "request_id", sa.BigInteger(),
            sa.ForeignKey("help_requests.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        comment=(
            "tsk-995: заявки, которым срок ответа поставила миграция "
            "tsk995_help_due_backfill (для точного отката)"
        ),
    )
    hours = int(Settings().help_sla_hours)
    bind = op.get_bind()
    filled = bind.execute(
        sa.text(
            """
            WITH upd AS (
                UPDATE help_requests
                   SET due_at = GREATEST(created_at, now()) + make_interval(hours => :hours)
                 WHERE due_at IS NULL AND status = 'open'
             RETURNING id, due_at
            )
            INSERT INTO help_request_due_backfill (request_id, due_at)
            SELECT id, due_at FROM upd
            RETURNING request_id
            """
        ),
        {"hours": hours},
    ).fetchall()
    logger.info(
        "tsk995_help_due_backfill: срок %s ч поставлен открытым заявкам: %d",
        hours, len(filled),
    )


def downgrade() -> None:
    """Вернуть `due_at = NULL` строкам, заполненным миграцией, и снять таблицу."""
    op.execute(
        """
        UPDATE help_requests hr
           SET due_at = NULL
          FROM help_request_due_backfill b
         WHERE b.request_id = hr.id
           AND hr.due_at = b.due_at
        """
    )
    op.drop_table("help_request_due_backfill")
