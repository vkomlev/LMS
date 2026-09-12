"""tsk-923 п.1: отметка «пожелание получено вручную», без анкеты.

Контекст. Методист видит на «Пожеланиях к расписанию» список тех, кто не
ответил через форму — но часть учеников присылает пожелание в телеграм или
лично, и форму так и не открывает. До этой миграции убрать такого ученика из
списка «не ответил» было нечем, кроме как выдумать за него анкету — а это
означало бы вписать несуществующие часы в спрос по расписанию (tsk-674).

Почему отдельная таблица, а не флаг в `student_schedule_preference`. У отметки
нет содержания: ни часов, ни `lessons_per_week`. Класть её в ту же таблицу
означало бы либо разрешить пустую анкету (а проверка сохранения этого не
допускает, и правильно — иначе спрос считал бы фиктичные нули), либо городить
нулевые заглушки. Отдельная таблица с одной строкой на ученика (PK — сам
`student_id`) — и `is_filled` в сводке становится `pref.id IS NOT NULL OR
ack.student_id IS NOT NULL`, а спрос по часам как считался по реальным строкам
`student_schedule_preference_hour`, так и считается: у отметки таких строк нет.

Напоминания (`schedule_preference_reminder_service.list_silent`) тем же
JOIN'ом перестают тревожить того, кого уже отметили, — методист поставил
отметку именно потому, что человек ответил, а не потому, что решил считать
его отвеченным для отчётности.

Rollback: `alembic downgrade tsk877_course_is_service` — таблица с отметками
удаляется целиком. Если к моменту отката отметки уже стоят на живых
учениках, они вернутся в список «не ответил» — это ожидаемо (сама отметка
существует только в этой таблице).

Revision ID: tsk923_schedule_preference_ack
Revises: tsk877_course_is_service
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk923_schedule_preference_ack"
down_revision: Union[str, None] = "tsk877_course_is_service"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_schedule_preference_ack",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "acknowledged_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "acknowledged_by", sa.Integer(), nullable=True,
            comment="Методист, поставивший отметку",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_schedule_preference_ack_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"], ondelete="SET NULL",
            name="student_schedule_preference_ack_acknowledged_by_fkey",
        ),
        sa.PrimaryKeyConstraint("student_id", name="student_schedule_preference_ack_pkey"),
        comment="Пожелание получено вручную (телеграм/лично), без анкеты (tsk-923)",
    )


def downgrade() -> None:
    op.drop_table("student_schedule_preference_ack")
