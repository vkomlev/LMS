"""tsk-743: отметка «спросил про пропуск» — чтобы список пропустивших схлопывался.

**Зачем вообще хранить.** План занятия показывает преподавателю, у кого спросить
причину пропуска. Без отметки этот список висит вечно: ученик пропустил 28.08 —
и строка «спросите почему» будет всплывать на каждом занятии до конца учебного
года, потому что система не знает, что разговор уже был. Ровно тот случай, когда
напоминание перестают читать.

Замер боевой базы (30 дней до 04.09): 92 пропуска при 251 явке, из них 38 —
ученик пришёл на следующее занятие (то есть спросить реально есть у кого), 31 —
пропустил дважды подряд. Это не редкий случай, а работа на каждом занятии.

**Форма — факт разговора, а не статус пропуска.** Статус участия
(`lesson_occurrence_participant.status`) трогать нельзя: `no_show` — это факт
явки, он идёт в деньги, нагон ДЗ (tsk-741) и посещаемость. Отметка разговора —
про другое: она про работу преподавателя, живёт рядом и ничего не переписывает.

**Причина необязательна.** На уроке печатать некогда: главное действие —
«спросил», код причины (`illness`/`forgot`/`busy`/`no_answer`/`other`) ставится
одним нажатием, свободный текст — по желанию. Обязательное поле причины дало бы
либо пропуск шага, либо мусор в данных.

Rollback: `alembic downgrade tsk742_student_curator`. Таблица новая, ни одна
существующая не меняется; откат теряет только отметки разговоров — пропуски и
явка остаются на месте.

Revision ID: tsk743_absence_followup
Revises: tsk742_student_curator
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk743_absence_followup"
down_revision: Union[str, None] = "tsk742_student_curator"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создаёт lesson_absence_followup — «про этот пропуск уже спросили»."""
    op.create_table(
        "lesson_absence_followup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="Кого спрашивали",
        ),
        sa.Column(
            "occurrence_id",
            sa.Integer(),
            sa.ForeignKey("lesson_occurrence.id", ondelete="CASCADE"),
            nullable=False,
            comment="Про какое пропущенное занятие спрашивали",
        ),
        sa.Column(
            "asked_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Кто спросил; NULL — сервисный ключ или удалённый пользователь",
        ),
        sa.Column(
            "asked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Когда состоялся разговор",
        ),
        sa.Column(
            "reason",
            sa.String(16),
            nullable=True,
            comment=(
                "Код причины в одно нажатие: illness | forgot | busy | "
                "no_answer | other. NULL — спросил, причину не записал"
            ),
        ),
        sa.Column(
            "note",
            sa.Text(),
            nullable=True,
            comment="Свободный текст преподавателя, если кода мало",
        ),
        comment="tsk-743: отметка «про этот пропуск у ученика уже спросили»",
    )

    # Один разговор на один пропуск. Держит база, а не код: план занятия
    # открыт у преподавателя и на телефоне, и в браузере — двойное нажатие
    # иначе оставило бы две записи об одном разговоре.
    op.create_index(
        "uq_lesson_absence_followup",
        "lesson_absence_followup",
        ["student_id", "occurrence_id"],
        unique=True,
    )
    # Главный запрос экрана — «какие пропуска этих учеников ещё не разобраны».
    op.create_index(
        "ix_lesson_absence_followup_student",
        "lesson_absence_followup",
        ["student_id", "asked_at"],
    )
    op.create_check_constraint(
        "ck_lesson_absence_followup_reason",
        "lesson_absence_followup",
        "reason IS NULL OR reason IN ('illness', 'forgot', 'busy', 'no_answer', 'other')",
    )


def downgrade() -> None:
    """Убирает lesson_absence_followup. Другие таблицы не затрагиваются."""
    op.drop_table("lesson_absence_followup")
