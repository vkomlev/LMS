"""tsk-314: конфиг выборки заданий по сложности на подкурс.

Курс ЕГЭ разросся (~3000 заданий), прохождение затягивается. Часть 1 (вынос
HARD в опциональный подкурс) закрыта отдельной задачей tsk-347. Эта колонка —
для частей 2/3: если EASY+NORMAL заданий подкурса больше настроенного порога,
студенту показывается не всё, а случайная часть стабильного размера.

Формат значения (jsonb), см. `app/schemas/course_sampling.py`:
    {"enabled": true, "threshold": 40, "easy_ratio": 0.5}
threshold — порог И размер итоговой выборки при превышении (сумма EASY+NORMAL
заданий курса); easy_ratio — доля EASY в выборке (0..1, NORMAL — остаток).
THEORY и прочая сложность (HARD/PROJECT) выборке не подлежат — выдаются
всегда целиком, независимо от этой настройки.

NULL / enabled=false — прежнее поведение (выдаются все задания курса).

Почему поле на courses, а не отдельная таблица правил: решение оператора
(tsk-314, «Решения оператора» 2026-07-19) — настройка живёт в поле-конфиге
курса, не в отдельной таблице. Тот же паттерн, что difficulty_provenance
(tsk-381) на tasks — nullable JSONB, конвейеры импорта его не трогают.

Revision ID: tsk314_course_sampling_config
Revises: tsk010_student_payment
Create Date: 2026-08-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk314_course_sampling_config"
down_revision: Union[str, None] = "tsk010_student_payment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет nullable-колонку конфига выборки заданий по сложности."""
    op.add_column(
        "courses",
        sa.Column(
            "sampling_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Выборка заданий по сложности (tsk-314): "
                "{enabled, threshold, easy_ratio}. NULL/enabled=false = все задания."
            ),
        ),
    )


def downgrade() -> None:
    """Убирает колонку конфига выборки."""
    op.drop_column("courses", "sampling_config")
