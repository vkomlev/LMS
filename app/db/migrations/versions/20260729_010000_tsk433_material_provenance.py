"""tsk-433: происхождение содержимого материала (защита ручной правки от импорта).

Кабинет методиста получает правку материалов, но `bulk_upsert` перезаписывает
`title`/`content`/`description`/`caption` **безусловно** — условная запись
(tsk-377/378/407) была сделана только для `is_active`, `order_position` и
`requirement_level`. Без этой колонки методист поправил бы текст, а ближайшее
переиздание из источника молча вернуло бы старый — и объяснить, куда делась
правка, было бы нечем.

Форма значения (jsonb):
    {"source": "manual_web", "edited_at": "2026-07-29T18:40:00+00:00",
     "edited_by": 2, "fields": ["title", "content"]}

Правило чтения (реализовано в MaterialsService.bulk_upsert):
  - `source = "manual_web"` → поля из `fields` при импорте НЕ перезаписываются;
  - NULL → поведение прежнее, импорт обновляет всё как раньше;
  - защита пофайлово по СПИСКУ полей, а не по материалу целиком: правка
    заголовка не должна блокировать полезное обновление содержимого.

Почему отдельная колонка, а не ключ внутри `content`:
  - `content` — это то, что видит ученик; служебным метаданным там не место;
  - `bulk_upsert` кладёт `content` payload'ом целиком, и провенанс исчезал бы
    при каждом переиздании (ровно ловушка tsk-377).

Образец формы и обоснования — `tasks.difficulty_provenance` (tsk-381).

Revision ID: tsk433_material_provenance
Revises: tsk443_lesson_multi_teacher
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk433_material_provenance"
down_revision: Union[str, None] = "tsk443_lesson_multi_teacher"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет nullable-колонку происхождения содержимого материала.

    Колонка без дефолта и без backfill: существующие строки остаются NULL,
    то есть «правок руками не было» — их импорт обновляет как раньше.
    Переписывания данных нет, только метаданные таблицы.
    """
    op.add_column(
        "materials",
        sa.Column(
            "content_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Происхождение содержимого: {source, edited_at, edited_by, fields}. "
                "source=manual_web → перечисленные поля не перезаписываются импортом. "
                "NULL — правок руками не было."
            ),
        ),
    )


def downgrade() -> None:
    """Убирает колонку. Пометки ручной правки при этом теряются.

    После отката ближайший импорт снова перезапишет вручную правленые поля —
    это ожидаемое следствие, а не побочный дефект.
    """
    op.drop_column("materials", "content_provenance")
