"""tsk-868: сеансы работы ученика над элементом программы.

Revision ID: tsk868_learning_time_session
Revises: tsk866_price_override_ends_on
Create Date: 2026-09-09

Зачем таблица. Время прохождения материала и задания нигде не сохранялось:
`student_presence` — снимок на ученика (одна строка, история намеренно не
велась, tsk-591), а `product_event` заведена в апреле и с тех пор пуста.

Хранятся СЕАНСЫ, а не пульсы (решение оператора 09.09). Пульс приходит раз в
две минуты; писать каждый — это десятки тысяч строк в месяц ради данных, от
которых в tsk-591 осознанно отказались. Сеанс — одна строка на непрерывную
работу над элементом: видно и сколько заняло, и когда, и сколько было заходов.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk868_learning_time_session"
down_revision: Union[str, None] = "tsk866_price_override_ends_on"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_time_session",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "student_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Тип элемента — тот же словарь, что у контекста присутствия
        # (`student_presence.context`): пульс и сеанс говорят об одном и том же,
        # и расходиться словарями им нельзя.
        sa.Column("item_type", sa.Text, nullable=False),
        # Внешних ключей на задание, материал и курс здесь НЕТ — намеренно, по
        # образцу `student_presence`. Эти значения приходят от кабинета как
        # есть, никем не проверяются, и ссылка на удалённое задание не должна
        # ронять запись: телеметрия — побочная запись, а пульс, в который она
        # встроена, держит сигнал преподавателю о простое ученика. Ужесточение
        # здесь стоило падения пульса на тестах ещё до выката.
        sa.Column("task_id", sa.Integer, nullable=True),
        sa.Column("material_id", sa.Integer, nullable=True),
        sa.Column("course_id", sa.Integer, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        # Секунды хранятся отдельно от разницы меток намеренно: у видео это
        # РЕАЛЬНО проигранное время, которое меньше промежутка «открыл-закрыл»
        # (паузы, перемотка назад). Для присутствия оба значения совпадают.
        sa.Column("seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("beats", sa.Integer, nullable=False, server_default="1"),
        sa.Column("interactions", sa.Integer, nullable=False, server_default="0"),
        # Чем измерен сеанс: пульсом присутствия или событиями видеоплеера.
        # Смешивать их в отчётах нельзя — у них разная точность (пульс даёт
        # две минуты, плеер — секунды).
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "item_type IN ('task','material','video','course','other')",
            name="ck_learning_time_session_item_type",
        ),
        sa.CheckConstraint(
            "source IN ('presence','player')",
            name="ck_learning_time_session_source",
        ),
        sa.CheckConstraint("seconds >= 0", name="ck_learning_time_session_seconds"),
        sa.CheckConstraint(
            "ended_at >= started_at", name="ck_learning_time_session_order",
        ),
    )
    # Продление сеанса ищет последний открытый сеанс ученика — это самый частый
    # запрос (на каждый пульс), и он должен попадать в индекс.
    op.create_index(
        "ix_learning_time_session_student_ended",
        "learning_time_session",
        ["student_id", "ended_at"],
    )
    # Отчёты идут от элемента: «сколько занимает этот материал у всех».
    op.create_index(
        "ix_learning_time_session_material",
        "learning_time_session",
        ["material_id", "started_at"],
        postgresql_where=sa.text("material_id IS NOT NULL"),
    )
    op.create_index(
        "ix_learning_time_session_task",
        "learning_time_session",
        ["task_id", "started_at"],
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_learning_time_session_task", table_name="learning_time_session")
    op.drop_index(
        "ix_learning_time_session_material", table_name="learning_time_session",
    )
    op.drop_index(
        "ix_learning_time_session_student_ended", table_name="learning_time_session",
    )
    op.drop_table("learning_time_session")
