"""tsk-798: приоритет номера ЕГЭ и подкурсы, выпавшие из сокращённой программы.

**Зачем.** Ученику, пришедшему в феврале-марте, не помещается даже
несокращаемое ядро: 605 элементов за 4 недели — это 141 в неделю. До сих пор
система в таком случае честно говорила «решите с методистом» и не резала ничего
сама. Решение оператора 05.09: резать и ядро, но **по номерам ЕГЭ** — то есть
выбрасывать номер целиком, а не куски из каждого.

**Почему порядок задаёт человек, а не данные.** Замер боевой базы 05.09:
сложность заданий внутри номеров почти не различается — `HARD` есть только у
двух подкурсов из 25, а средняя сложность у «Задания 1» и «Задания 27»
одинаковая. То есть вывести «какой номер дешевле освоить» из базы невозможно;
это методическое знание, и живёт оно в `courses.program_priority`.

**Пока приоритет не проставлен — не режем.** NULL означает «не размечено», и
такой подкурс не выпадает никогда. Выбросить у выпускника разбор номера по
догадке хуже, чем показать преподавателю, что программа не помещается: первое
он заметит в июне, второе — сегодня.

`student_program_scope.excluded_courses` — что именно выпало у КОНКРЕТНОГО
ученика. Хранится рядом с планом и по той же причине: набор обязан быть
устойчивым, а не пересчитываться при каждом заходе.

Rollback: `alembic downgrade tsk798_program_scope`. Обе колонки необязательные,
существующие данные не трогаются; откат теряет разметку приоритетов.

Revision ID: tsk798_core_priority
Revises: tsk798_program_scope
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk798_core_priority"
down_revision: Union[str, None] = "tsk798_program_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить приоритет подкурса и список выпавших подкурсов ученика."""
    op.add_column(
        "courses",
        sa.Column(
            "program_priority",
            sa.SmallInteger(),
            nullable=True,
            comment=(
                "Место номера ЕГЭ в сокращённой программе: меньше — входит "
                "раньше. NULL — не размечено, такой подкурс не выпадает "
                "никогда. Ставит методист: из данных этот порядок не выводится"
            ),
        ),
    )
    op.add_column(
        "student_program_scope",
        sa.Column(
            "excluded_courses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=(
                "Подкурсы, выпавшие из программы этого ученика: бюджета не "
                "хватило даже на ядро. Задания и материалы этих подкурсов ему "
                "не выдаются и не считаются в прогрессе"
            ),
        ),
    )


def downgrade() -> None:
    """Убрать приоритет и список выпавших подкурсов."""
    op.drop_column("student_program_scope", "excluded_courses")
    op.drop_column("courses", "program_priority")
