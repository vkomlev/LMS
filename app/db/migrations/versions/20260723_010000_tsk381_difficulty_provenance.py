"""tsk-381: происхождение оценки сложности рядом со значением.

По `tasks.difficulty_id` нельзя было сказать, откуда взялось значение: канон это,
дефолт импорта или чья-то ручная правка. Из-за этого понадобилась археология по
всей партии Крылова (tsk-355, tsk-381) — сверка задним числом с постами канала и
книгой. Колонка снимает этот класс работ на будущее.

Почему отдельная колонка, а не ключ в `task_content`:
  - `task_content` описывает то, что видит ученик, — метаданные поля там не к месту;
  - `bulk_upsert` перезаписывает `task_content` payload'ом целиком, и провенанс
    молча исчезал бы при каждом переиздании (ровно ловушка tsk-377);
  - колонку, о которой конвейеры не знают, upsert не трогает — сохранность
    достаётся бесплатно, логика нужна только на инвалидацию.

Формат значения (jsonb):
    {"canon": 1, "source": "tg:cyberguru_ege", "evidence": "посты 775: простой",
     "decided_at": "2026-07-23", "task": "tsk-381"}
где canon: 1 — разметка автора в ТГ-разборах, 2 — ручной вердикт оператора,
3 — оценка внешнего сайта. NULL = происхождение неизвестно (значение не
подтверждено ничем).

Revision ID: tsk381_difficulty_provenance
Revises: tsk335_336_hr_student_status_idx
Create Date: 2026-07-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk381_difficulty_provenance"
down_revision: Union[str, None] = "tsk335_336_hr_student_status_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет nullable-колонку происхождения оценки сложности."""
    op.add_column(
        "tasks",
        sa.Column(
            "difficulty_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Происхождение difficulty_id: {canon: 1|2|3, source, evidence, "
                "decided_at, task}. NULL = ничем не подтверждено (tsk-381)"
            ),
        ),
    )


def downgrade() -> None:
    """Убирает колонку происхождения."""
    op.drop_column("tasks", "difficulty_provenance")
