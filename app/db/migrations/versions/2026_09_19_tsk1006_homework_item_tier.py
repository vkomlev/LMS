"""tsk-1006: ярус пункта домашней работы — обязательное / желательное.

Оператор 19.09: «у ребят с отставанием от программы нужно показывать две
градации ДЗ: обязательное и желательное, чтобы нагнать норму. Я вижу, что
ученик работает в полном объёме по ДЗ и при этом отстаёт — таким я
рекомендую ускориться. Должна быть штатная процедура без ручных указаний».

Обязательный объём («задаём») ограничен шагом роста от собственного темпа
ученика (tsk-896/tsk-909), а «нужно к сроку» у отстающего выше. Разница и
есть желательный ярус: выдаётся тем же набором, следом за обязательным, с
пометкой `tier = 'extra'`. Выполнение считается раздельно: «X из N» —
обязательное, «сверх нормы M из K» — желательное; просрочка — только по
обязательному.

Одна колонка с `server_default 'required'`: все существующие пункты — и в
действующих, и в отменённых выдачах — обязательные, что и было правдой.
`server_default` заполняет старые строки на месте
([[feedback_add_column_default_fills_existing_rows]]), переписывать таблицу
не нужно. Значения ограничены CHECK — на проде два потребителя состава
(SPW и бот), и третьего значения никто из них не ждёт.

Rollback: `alembic downgrade tsk943_help_request_attachments` — колонка
удаляется, желательные пункты становятся неотличимы от обязательных (в
истории — как выданные). Данные не теряются.

Revision ID: tsk1006_homework_item_tier
Revises: tsk943_help_request_attachments
Create Date: 2026-09-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1006_homework_item_tier"
down_revision: Union[str, None] = "tsk943_help_request_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_item",
        sa.Column(
            "tier",
            sa.String(16),
            nullable=False,
            server_default="required",
            comment="tsk-1006: required — обязательное, extra — желательное, чтобы нагнать норму",
        ),
    )
    op.create_check_constraint(
        "ck_homework_item_tier",
        "homework_item",
        "tier IN ('required', 'extra')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_homework_item_tier", "homework_item", type_="check")
    op.drop_column("homework_item", "tier")
