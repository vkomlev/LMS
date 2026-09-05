"""tsk-798: персональный объём программы подготовки — сколько влезает в срок.

**Зачем хранить, а не считать на лету.** Объём решает, какие задания вообще
попадут ученику в обход (`resolve_next_item`), а этот путь зовётся на каждый
шаг урока. Считать там бюджет программы — значит на каждом шаге поднимать
дерево курсов, распределение по сложности и темп за три недели. Плюс вторая,
важнее: план обязан быть УСТОЙЧИВЫМ. Пересчёт «на лету» менял бы состав
программы от захода к заходу вслед за колебаниями недельного темпа, и ученик
видел бы, как задания то появляются, то исчезают.

**Порог только растёт.** `per_course` хранит выбранный порог на подкурс;
пересчёт берёт максимум со старым значением. Механика выборки (tsk-798,
перестановка вместо `random.sample`) даёт вложенные наборы, поэтому рост
порога только ДОБАВЛЯЕТ задания к уже выданным. Уменьшать порог нельзя: у
человека пропали бы задания, часть из которых он уже решил.

Замер боевой базы 05.09: программа ЕГЭ (курсы 88+112) — 1439 обязательных
элементов, из них несокращаемое ядро 605 (вся теория, все номера ЕГЭ,
материалы) и 834 задания тренажёра (EASY+NORMAL). Ученику, стартующему
1 ноября, до 31 марта остаётся 21.4 недели: при темпе 25 в неделю бюджет 535,
то есть не помещается даже ядро. Отсюда поле `core_trimmed` — признак, что
пришлось резать и ядро (решение оператора 05.09).

Rollback: `alembic downgrade tsk743_absence_followup`. Таблица новая, ни одна
существующая не меняется; откат теряет только рассчитанные планы — они
восстанавливаются пересчётом.

Revision ID: tsk798_program_scope
Revises: tsk743_absence_followup
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk798_program_scope"
down_revision: Union[str, None] = "tsk743_absence_followup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу персонального объёма программы."""
    op.create_table(
        "student_program_scope",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "program_kind",
            sa.String(length=16),
            nullable=False,
            comment="ege | oge — программа подготовки, к которой относится план",
        ),
        sa.Column(
            "deadline",
            sa.Date(),
            nullable=False,
            comment="К какому дню программу нужно закончить",
        ),
        sa.Column(
            "planned_pace",
            sa.Integer(),
            nullable=False,
            comment=(
                "Недельный темп, на который рассчитан план: базовое ожидание "
                "школы либо фактический темп ученика, если он выше"
            ),
        ),
        sa.Column(
            "core_total",
            sa.Integer(),
            nullable=False,
            comment="Несокращаемых элементов в программе (теория, номера, материалы)",
        ),
        sa.Column(
            "drill_total",
            sa.Integer(),
            nullable=False,
            comment="Заданий тренажёра (EASY+NORMAL), подлежащих выборке",
        ),
        sa.Column(
            "drill_allowed",
            sa.Integer(),
            nullable=False,
            comment="Сколько заданий тренажёра помещается в срок при этом темпе",
        ),
        sa.Column(
            "core_trimmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "Бюджета не хватило даже на ядро — программу пришлось резать "
                "по номерам ЕГЭ. Сигнал преподавателю, не тихое обрезание"
            ),
        ),
        sa.Column(
            "per_course",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "{course_id: порог выборки} — бюджет тренажёра, разложенный по "
                "подкурсам пропорционально их размеру. Только растёт"
            ),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Один действующий план на ученика и программу: девятикласснику могли
    # открыть материалы ЕГЭ, и оба плана существовать не должны одновременно
    # молча — программа выбирается один раз, в homework_volume_service.
    op.create_unique_constraint(
        "uq_student_program_scope_student_kind",
        "student_program_scope",
        ["student_id", "program_kind"],
    )


def downgrade() -> None:
    """Удалить таблицу персонального объёма программы."""
    op.drop_constraint(
        "uq_student_program_scope_student_kind",
        "student_program_scope",
        type_="unique",
    )
    op.drop_table("student_program_scope")
