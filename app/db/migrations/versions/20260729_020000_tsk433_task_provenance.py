"""tsk-433: происхождение содержимого задания (защита ручной правки от импорта).

Продолжение `tsk433_material_provenance` — та же защита, но для заданий.
`TasksService.bulk_upsert` перезаписывает `task_content` и `solution_rules`
безусловно (условная запись сделана только для `is_active`,
`requirement_level`, `order_position`, `difficulty_provenance`), поэтому без
этой колонки правка методиста через кабинет исчезала бы при ближайшем
переиздании из источника.

Форма значения (jsonb), та же, что у материалов:
    {"source": "manual_web", "edited_at": "2026-07-29T20:10:00+00:00",
     "edited_by": 2, "fields": ["task_content", "solution_rules"]}

Правило чтения (реализовано в TasksService.bulk_upsert):
  - `source = "manual_web"` → поля из `fields` при импорте НЕ перезаписываются;
  - NULL → поведение прежнее.

Почему отдельная колонка, а не ключ внутри `task_content`: `bulk_upsert` кладёт
`task_content` payload'ом целиком, и пометка исчезала бы при каждом переиздании
(ловушка tsk-377). Рядом уже живёт `difficulty_provenance` (tsk-381) — колонка
про происхождение ОЦЕНКИ СЛОЖНОСТИ, это другое поле и другой смысл; здесь про
происхождение самого содержимого.

Revision ID: tsk433_task_provenance
Revises: tsk433_material_provenance
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk433_task_provenance"
down_revision: Union[str, None] = "tsk433_material_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет nullable-колонку происхождения содержимого задания.

    Без дефолта и без backfill: существующие строки остаются NULL — «правок
    руками не было», импорт обновляет их как раньше. Данные не переписываются.
    """
    op.add_column(
        "tasks",
        sa.Column(
            "content_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Происхождение содержимого задания: {source, edited_at, edited_by, fields}. "
                "source=manual_web → перечисленные поля (task_content/solution_rules) "
                "не перезаписываются импортом. NULL — правок руками не было. "
                "Не путать с difficulty_provenance — та про обоснование сложности."
            ),
        ),
    )


def downgrade() -> None:
    """Убирает колонку; пометки ручной правки теряются.

    После отката ближайший импорт снова перезапишет вручную правленые задания —
    ожидаемое следствие, а не побочный дефект.
    """
    op.drop_column("tasks", "content_provenance")
