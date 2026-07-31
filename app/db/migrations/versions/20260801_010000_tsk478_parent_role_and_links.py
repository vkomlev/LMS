"""tsk-478: роль parent + таблица parent_student_links.

Кабинет родителя (read-only дашборд ученика, tsk-494). Новая роль вместо
переиспользования `customer` — та существует в БД, но нигде функционально
не используется и по смыслу не подходит («заказчик» ≈ платящий клиент в
целом, не «родитель конкретного ученика»). `parent_student_links` — тот же
M2M-паттерн, что уже существующая `student_teacher_links` (PK составной,
FK CASCADE на users, `linked_at`).

Revision ID: tsk478_parent_role_and_links
Revises: tsk432_user_blocking
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk478_parent_role_and_links"
down_revision: Union[str, None] = "tsk432_user_blocking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # roles.id НЕ serial/identity (проверено на dev и прод — column_default
    # NULL, таблица изначально засеяна вручную явными id) — INSERT без id
    # падает NOT NULL. Считаем следующий id сами.
    #
    # ВАЖНО (найдено round-trip тестом upgrade→downgrade→upgrade на dev):
    # "INSERT ... SELECT ... WHERE NOT EXISTS(...)" здесь НЕ работает как
    # guard целого INSERT — WHERE фильтрует строки ДО агрегации MAX(id), и
    # агрегат без строк всё равно возвращает одну строку (MAX=NULL→COALESCE
    # даёт 0+1=1) — INSERT всё равно выполняется и падает PK-конфликтом с
    # id=1. Верно — вычислить id в VALUES через подзапрос и положиться на
    # `ON CONFLICT (name)` (roles_name_key) для идемпотентности: конфликт по
    # имени гасит INSERT целиком, даже если вычисленный id к этому моменту
    # уже занят другой ролью.
    op.execute(
        "INSERT INTO roles (id, name) "
        "VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM roles), 'parent') "
        "ON CONFLICT (name) DO NOTHING"
    )

    op.create_table(
        "parent_student_links",
        sa.Column("parent_id", sa.Integer(), primary_key=True, nullable=False, comment="ID родителя"),
        sa.Column("student_id", sa.Integer(), primary_key=True, nullable=False, comment="ID ученика"),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False,
            comment="Когда добавлена связка",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["users.id"], ondelete="CASCADE",
            name="parent_student_links_parent_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="parent_student_links_student_id_fkey",
        ),
        sa.PrimaryKeyConstraint("parent_id", "student_id", name="parent_student_links_pkey"),
        comment="Привязка родителей к ученикам (tsk-478, кабинет родителя)",
    )


def downgrade() -> None:
    op.drop_table("parent_student_links")
    # Роль НЕ удаляем: если к моменту отката кому-то уже назначена (user_roles
    # FK на roles.id), DELETE упадёт; роль без назначений безвредна оставленной.
