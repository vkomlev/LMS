"""tsk-1124: группы расписания (аудитория + предмет).

Оператор 25.09: «Нужны группы расписаний… развести взрослых и детей». Группа
управляет тем, какие слоты видит ученик при записи и переносе, фильтрами
кабинетов, аудиторией опроса и подсказкой тарифа.

Что делает:
- справочник `schedule_group` (аудитория kids/adults, предмет, название,
  подсказка тарифной группы `pricing_group_id`, флаг `is_default` — ровно одна
  группа по умолчанию, частичный уникальный индекс);
- `user_schedule_group` — группы ученика (многие-ко-многим). Ученик без строк
  считается членом группы по умолчанию — так никто не теряет запись;
- `lesson_slot.group_id` — у слота ровно одна группа, NOT NULL;
- триггер `lesson_slot_default_group`: вставка без группы получает группу по
  умолчанию. Держит старые пути создания слота (API до Ф2, построитель
  расписания, сырые INSERT в скриптах и тестах) рабочими без правок;
- сид: «Дети · Информатика» (по умолчанию) и «Взрослые · Тестирование»
  (подсказка тарифа — группа плана `adults`, ищется по коду плана, не по id);
- разметка: ВСЕ слоты (и неактивные — для истории) → «Дети · Информатика».
  Решение оператора 25.09: ЕГЭ и ОГЭ занимаются вместе, делить нечем.

Rollback: `alembic downgrade tsk1088_adults_static_price` — снимает триггер,
колонку и обе таблицы. Данные групп при этом теряются (до Ф7 их нет).

Revision ID: tsk1124_schedule_groups
Revises: tsk1088_adults_static_price
Create Date: 2026-09-26
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1124_schedule_groups"
down_revision: Union[str, None] = "tsk1088_adults_static_price"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_KIDS_NAME = "Дети · Информатика"
_ADULTS_NAME = "Взрослые · Тестирование"
_ADULTS_PLAN_CODE = "adults"


def upgrade() -> None:
    op.create_table(
        "schedule_group",
        sa.Column("id", sa.Integer(), primary_key=True, comment="ID группы расписания"),
        sa.Column("audience", sa.Text(), nullable=False, comment="kids | adults"),
        sa.Column("subject", sa.Text(), nullable=False, comment="Предмет: «Информатика», «Тестирование»"),
        sa.Column("name", sa.Text(), nullable=False, comment="Подпись в интерфейсе: «Дети · Информатика»"),
        sa.Column(
            "pricing_group_id", sa.Integer(),
            sa.ForeignKey("pricing_group.id", ondelete="SET NULL", name="schedule_group_pricing_group_id_fkey"),
            nullable=True,
            comment="Подсказка тарифной группы; деньги по ней не двигаются автоматически",
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false"),
                  comment="Группа ученика без явных групп и слота без группы"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("audience IN ('kids', 'adults')", name="schedule_group_audience_check"),
        sa.UniqueConstraint("name", name="schedule_group_name_key"),
        comment="Группа расписания: аудитория + предмет (tsk-1124)",
    )
    op.create_index(
        "schedule_group_single_default_idx", "schedule_group", ["is_default"],
        unique=True, postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "user_schedule_group",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE", name="user_schedule_group_user_id_fkey"),
                  nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("schedule_group.id", ondelete="CASCADE", name="user_schedule_group_group_id_fkey"),
                  nullable=False),
        sa.Column("added_by", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL", name="user_schedule_group_added_by_fkey"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("user_id", "group_id", name="user_schedule_group_pkey"),
        comment="Группы расписания ученика; нет строк — группа по умолчанию (tsk-1124)",
    )
    op.create_index("user_schedule_group_group_id_idx", "user_schedule_group", ["group_id"])

    conn = op.get_bind()
    adults_pg = conn.execute(
        sa.text("SELECT pricing_group_id FROM subscription_plan WHERE code = :c"),
        {"c": _ADULTS_PLAN_CODE},
    ).scalar()
    if adults_pg is None:
        logger.warning("tsk-1124: план %s без тарифной группы — подсказка тарифа пустая", _ADULTS_PLAN_CODE)
    kids_id = conn.execute(
        sa.text(
            "INSERT INTO schedule_group (audience, subject, name, is_default) "
            "VALUES ('kids', 'Информатика', :n, true) RETURNING id"
        ),
        {"n": _KIDS_NAME},
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO schedule_group (audience, subject, name, pricing_group_id) "
            "VALUES ('adults', 'Тестирование', :n, :pg)"
        ),
        {"n": _ADULTS_NAME, "pg": adults_pg},
    )

    op.add_column(
        "lesson_slot",
        sa.Column("group_id", sa.Integer(), nullable=True, comment="Группа расписания слота (tsk-1124)"),
    )
    marked = conn.execute(
        sa.text("UPDATE lesson_slot SET group_id = :g WHERE group_id IS NULL"), {"g": kids_id}
    ).rowcount
    logger.info("tsk-1124: слотов размечено в «%s»: %s", _KIDS_NAME, marked)
    op.alter_column("lesson_slot", "group_id", nullable=False)
    op.create_foreign_key(
        "lesson_slot_group_id_fkey", "lesson_slot", "schedule_group",
        ["group_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("lesson_slot_group_id_idx", "lesson_slot", ["group_id"])

    op.execute(
        """
        CREATE FUNCTION lesson_slot_default_group() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.group_id IS NULL THEN
                SELECT id INTO NEW.group_id FROM schedule_group WHERE is_default;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER lesson_slot_default_group BEFORE INSERT ON lesson_slot "
        "FOR EACH ROW EXECUTE FUNCTION lesson_slot_default_group()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS lesson_slot_default_group ON lesson_slot")
    op.execute("DROP FUNCTION IF EXISTS lesson_slot_default_group()")
    op.drop_index("lesson_slot_group_id_idx", table_name="lesson_slot")
    op.drop_constraint("lesson_slot_group_id_fkey", "lesson_slot", type_="foreignkey")
    op.drop_column("lesson_slot", "group_id")
    op.drop_index("user_schedule_group_group_id_idx", table_name="user_schedule_group")
    op.drop_table("user_schedule_group")
    op.drop_index("schedule_group_single_default_idx", table_name="schedule_group")
    op.drop_table("schedule_group")
