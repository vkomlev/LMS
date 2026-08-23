"""tsk-644: журнал медленных запросов, чтобы такой день был виден.

Контекст. 18 августа в окне 12:11–12:25 приём ответа ученика занял 123,5 с —
ребёнок нажал «Ответить» и две минуты смотрел в экран. Узнали об этом через
четыре дня и случайно: порог медленного запроса (3 с, tsk-621) пишется в
`logs/app.log`, а в лог никто не смотрит. Ошибок не было, жалоб не было, день
прошёл как обычный.

Почему таблица, а не разбор лога. Еженедельные чеки (`scripts/weekly_checks.py`,
tsk-636/tsk-641) ходят по расписанию с машины оператора и достают до прода
ровно одним способом — подключением к БД из `.mcp.json`. Файл `logs/app.log`
лежит на боевой машине, и чек его не видит в принципе. Значит сигнал должен
лежать там, куда чек и так ходит.

Цена на записи. Строка пишется только когда запрос уже превысил порог: 18
августа таких было 49 за день, в обычный день — единицы. Запись идёт мимо
ответа пользователю (фоновая задача, свои короткие транзакция и таймаут) и
никогда его не задерживает: журнал наблюдения не имеет права стать причиной
следующего наблюдения.

Что НЕ пишем: тело запроса, параметры и заголовки. Здесь нужен ответ на вопрос
«сколько ждали и где», а не содержимое учебной работы; лишние данные о ребёнке
в служебном журнале — плата без пользы.

Revision ID: tsk644_slow_request
Revises: tsk636_task_rules_audit
Create Date: 2026-08-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk644_slow_request"
down_revision: Union[str, None] = "tsk636_task_rules_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slow_request",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("method", sa.String(length=10), nullable=False),
        # Путь шаблонный, если роут удалось сопоставить (`/api/v1/attempts/{attempt_id}/answers`),
        # иначе фактический. Шаблон нужен, чтобы сводка группировалась по
        # обработчику, а не рассыпалась по идентификаторам попыток.
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        schema="public",
    )
    # Сводка всегда читает «последние N дней, худшие сверху».
    op.create_index(
        "ix_slow_request_ts", "slow_request", ["ts"], unique=False, schema="public"
    )


def downgrade() -> None:
    op.drop_index("ix_slow_request_ts", table_name="slow_request", schema="public")
    op.drop_table("slow_request", schema="public")
