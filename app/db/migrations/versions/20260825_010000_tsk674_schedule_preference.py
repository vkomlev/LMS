"""tsk-674 фаза 1: пожелания ученика по расписанию.

Контекст. К 30 августа школа собирает со всех учащихся пожелания по осеннему
расписанию, чтобы методист сверстал по ним сетку слотов. До сих пор ученик
своих предпочтений не сообщал нигде: расписание вели руками в боте методиста
(tsk-437), а слот появлялся у ученика уже готовым фактом.

Почему новые таблицы, а не поля в `lesson_slot_student`. Слот — уже принятое
решение школы, пожелание — просьба ДО решения, и у половины учащихся осенью
слот сменится (нынешние 10:00 и 11:00 МСК в новый диапазон Пн-Чт 12-19 / Сб
9-14 не попадают вовсе). Класть просьбу в строку решения значит потерять её в
тот момент, когда решение перепишут.

Три таблицы:
- `student_schedule_preference` — действующее состояние, одна строка на ученика;
- `student_schedule_preference_hour` — выбранные часы, время МОСКОВСКОЕ
  (сетку школа ведёт по Москве; пояс ученика дорисовывает клиент, tsk-588);
- `student_schedule_preference_revision` — снимок на каждое сохранение.
  История нужна не для аудита, а по прямому требованию: пожелания правятся весь
  срок обучения, и методисту важно видеть, что человек просил в августе и что
  просит в ноябре.

Уникальность часа по паре «день + время» внутри одного пожелания намеренно не
разделена по виду: один и тот же час не может быть одновременно «желательным» и
«возможным», и запретить это на уровне БД дешевле, чем ловить в сервисе.

Rollback: `alembic downgrade tsk673_alumni_course_work` — все три таблицы
удаляются вместе с собранными пожеланиями. Перед откатом после старта опроса
данные надо выгрузить: восстанавливать их будет неоткуда.

Порядок в цепочке. Изначально ревизия стояла на `tsk653_gap_signal_reason`, но
на ту же основу параллельно встала `tsk673_alumni_course_work` — получались две
головы, и `alembic upgrade head` в `deploy/vps/deploy.sh` упал бы на проде
(«Multiple head revisions are present»), уронив выкат обеим задачам. Цепочка
выпрямлена сдвигом ЭТОЙ ревизии, потому что она была применена только на dev и
откатывается без потери данных (таблицы пустые).

Revision ID: tsk674_schedule_preference
Revises: tsk673_alumni_course_work
Create Date: 2026-08-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk674_schedule_preference"
down_revision: Union[str, None] = "tsk673_alumni_course_work"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_schedule_preference",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "lessons_per_week",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("2"),
            comment="Сколько занятий в неделю нужно ученику; умолчание — 2",
        ),
        sa.Column(
            "comment",
            sa.Text(),
            nullable=True,
            comment="Свободная приписка ученика: «после 17 не могу» и т.п.",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_by", sa.Integer(), nullable=True,
            comment="Кто сохранил последнюю версию: сам ученик или сотрудник",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_schedule_preference_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], ondelete="SET NULL",
            name="student_schedule_preference_updated_by_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="student_schedule_preference_pkey"),
        sa.UniqueConstraint("student_id", name="uq_student_schedule_preference_student"),
        sa.CheckConstraint(
            "lessons_per_week BETWEEN 1 AND 7",
            name="ck_student_schedule_preference_lessons_per_week",
        ),
        comment="Пожелания ученика по расписанию: занятий в неделю + часы (tsk-674)",
    )

    op.create_table(
        "student_schedule_preference_hour",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("preference_id", sa.Integer(), nullable=False),
        sa.Column(
            "weekday", sa.SmallInteger(), nullable=False,
            comment="0=понедельник .. 6=воскресенье",
        ),
        sa.Column("start_time", sa.Time(), nullable=False, comment="Начало часа по Москве"),
        sa.Column(
            "kind", sa.Text(), nullable=False,
            comment="preferred — желательный час, possible — возможный",
        ),
        sa.ForeignKeyConstraint(
            ["preference_id"], ["student_schedule_preference.id"], ondelete="CASCADE",
            name="student_schedule_preference_hour_preference_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="student_schedule_preference_hour_pkey"),
        sa.UniqueConstraint(
            "preference_id", "weekday", "start_time",
            name="uq_student_schedule_preference_hour_slot",
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_schedule_preference_hour_weekday"),
        sa.CheckConstraint(
            "kind IN ('preferred', 'possible')", name="ck_schedule_preference_hour_kind"
        ),
        comment="Выбранные часы пожелания, время московское (tsk-674)",
    )
    # Спрос по часам («сколько человек просят среду 17:00») считается группировкой
    # именно по этой паре — это главный запрос помощника вёрстки в фазе 2.
    op.create_index(
        "ix_schedule_preference_hour_demand",
        "student_schedule_preference_hour",
        ["weekday", "start_time", "kind"],
    )

    op.create_table(
        "student_schedule_preference_revision",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("lessons_per_week", sa.SmallInteger(), nullable=False),
        sa.Column(
            "hours",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Снимок часов: [{weekday, start_time, kind}, …], время московское",
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "source", sa.Text(), nullable=False, server_default=sa.text("'student'"),
            comment="Кто и откуда правил: student | onboarding | staff",
        ),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_schedule_preference_revision_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"], ["users.id"], ondelete="SET NULL",
            name="student_schedule_preference_revision_changed_by_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="student_schedule_preference_revision_pkey"),
        comment="История правок пожеланий ученика по расписанию (tsk-674)",
    )
    op.create_index(
        "ix_schedule_preference_revision_student",
        "student_schedule_preference_revision",
        ["student_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_preference_revision_student",
        table_name="student_schedule_preference_revision",
    )
    op.drop_table("student_schedule_preference_revision")
    op.drop_index(
        "ix_schedule_preference_hour_demand",
        table_name="student_schedule_preference_hour",
    )
    op.drop_table("student_schedule_preference_hour")
    op.drop_table("student_schedule_preference")
