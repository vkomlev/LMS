"""tsk-742: закрепление ученика за куратором, с историей.

**Почему новая таблица, а не колонка в `users` и не `student_teacher_links`.**

`student_teacher_links` уже есть, но это НЕ то отношение. Там «все преподаватели
этого ученика» — многие-ко-многим без ответственного: по ней нельзя ответить
«кто отвечает за результат», потому что отвечают все, то есть никто. Ломать её
смыслом нельзя: на неё завязан ACL кабинета преподавателя (лента активности,
очередь проверок, заявки помощи), и сужение до одного человека мгновенно
отобрало бы доступ к ученикам у тех, кто с ними работает.

Колонка `users.curator_id` не годится по другой причине: история. Через полгода
вопрос «кто отвечал за этого ученика в сентябре» должен иметь ответ, а колонка
хранит только «сейчас» и молча теряет прошлое при каждой смене.

**Форма — интервалы, а не «текущее + журнал».** Одна строка = один период
ответственности: открытая (`ended_at IS NULL`) — текущий куратор, закрытая —
прошлый. Два хранилища (актуальное и история) разъезжаются в первый же день;
здесь разъезжаться нечему. Единственность текущего держит частичный уникальный
индекс в базе, а не проверка в коде: одновременная смена из двух мест иначе
оставила бы у ученика двух ответственных, и оба считали бы, что второй разберётся.

**Причины две, и они про разное.** `reason` — почему назначен ЭТОТ куратор
(«постоянный слот», «больше занятий за 90 дней», текст человека). `ended_reason`
— почему он перестал им быть. Одна колонка на оба смысла превращает историю в
загадку: непонятно, к какому концу отрезка относится запись.

Rollback: `alembic downgrade tsk760_task_touch_trail`. Таблица новая, ни одна
существующая не меняется; откат теряет только раскладку кураторов — её можно
вывести из расписания заново тем же правилом (docs/curator-charter.md § 6).

Revision ID: tsk742_student_curator
Revises: tsk760_task_touch_trail
Create Date: 2026-09-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk742_student_curator"
down_revision: Union[str, None] = "tsk760_task_touch_trail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создаёт student_curator — периоды ответственности куратора за ученика."""
    op.create_table(
        "student_curator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="За кого отвечают",
        ),
        sa.Column(
            "curator_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="Кто отвечает — преподаватель, который и так ведёт занятия",
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Начало периода ответственности",
        ),
        sa.Column(
            "ended_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Конец периода; NULL — куратор действующий",
        ),
        sa.Column(
            "source",
            sa.String(16),
            nullable=False,
            comment=(
                "derived — выведено из расписания правилом; "
                "manual — закрепил человек"
            ),
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=True,
            comment="Почему назначен ЭТОТ куратор (правило вывода или слова человека)",
        ),
        sa.Column(
            "assigned_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment=(
                "Кто закрепил: запустивший раскладку или закрепивший вручную; "
                "NULL — сервисный ключ"
            ),
        ),
        sa.Column(
            "ended_reason",
            sa.Text(),
            nullable=True,
            comment="Почему перестал быть куратором — отдельный смысл от reason",
        ),
        sa.Column(
            "ended_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Кто снял",
        ),
        comment="tsk-742: периоды ответственности куратора за ученика (история)",
    )

    # Один действующий куратор на ученика. Проверкой в коде это не удержать:
    # две одновременные смены прошли бы обе, и у ученика оказалось бы два
    # ответственных — худший из возможных исходов, потому что каждый из них
    # считает, что разберётся второй.
    op.create_index(
        "uq_student_curator_current",
        "student_curator",
        ["student_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    # Главный запрос экрана — «мои ученики».
    op.create_index(
        "ix_student_curator_roster",
        "student_curator",
        ["curator_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    # История по ученику: «кто отвечал за него в сентябре».
    op.create_index(
        "ix_student_curator_history",
        "student_curator",
        ["student_id", "assigned_at"],
    )
    op.create_check_constraint(
        "ck_student_curator_source",
        "student_curator",
        "source IN ('derived', 'manual')",
    )
    # Человек не может быть куратором самому себе. У преподавателей в этой базе
    # есть и роль student (они заведены как обычные пользователи), так что
    # правило не теоретическое.
    op.create_check_constraint(
        "ck_student_curator_not_self",
        "student_curator",
        "curator_id <> student_id",
    )
    # Отрезок не может кончаться раньше, чем начался.
    op.create_check_constraint(
        "ck_student_curator_period",
        "student_curator",
        "ended_at IS NULL OR ended_at >= assigned_at",
    )


def downgrade() -> None:
    """Убирает student_curator. Другие таблицы не затрагиваются."""
    op.drop_table("student_curator")
