"""tsk-996 (фаза B): якорь задания к материалу своего узла.

ADR-0008 (`docs/ai/adr/0008-interleave-theory-practice-anchor.md`), шаг 5
постановки `docs/briefs/2026-10-19-p7-anchor-implementation.md`.

`tasks.anchor_material_id` — материал того же курса, сразу после которого
задание стоит в учебном порядке узла (`app/services/node_sequence.py`).
NULL — задание в конце узла, как до этой миграции. Заполнения нет: у всех
заданий якорь NULL, и порядок для учеников не меняется (ADR-0008 Р3).

`tasks.anchor_source` — кто поставил якорь: `manual` (методист в кабинете)
или `publish` (публикатор ContentBackbone, фаза C). Переиздание перезаписывает
только `publish`/NULL (ADR-0008 «Владение якорем»). `manual` при NULL-якоре —
сознательный выбор методиста «в конце раздела».

Инварианты держит база, а не только сервис (правка руками, переезды):

* `trg_tasks_anchor_guard` (BEFORE INSERT/UPDATE на `tasks`):
  - переезд задания в другой курс (tsk-802: PATCH, CRUD, bulk-upsert с
    маршрутизацией HARD) обнуляет якорь и источник — материал старого узла
    в новом недопустим; если в том же UPDATE якорь сменили на ДРУГОЙ
    материал (нового курса), он остаётся и проверяется как обычно;
  - удаление материала (`ON DELETE SET NULL`) обнуляет и источник: `manual`
    при NULL-якоре значит «методист сам выбрал конец раздела», а не «материал
    пропал» — иначе публикатор (фаза C) такое задание уже не перепривязал бы;
  - якорь на материал другого курса или несуществующий — ошибка
    `check_violation` (23514). Материал читается `FOR SHARE`: параллельный
    переезд материала дождётся этой транзакции, и его триггер увидит якорь.
  Триггер срабатывает только когда меняются `course_id`/`anchor_material_id`
  (`UPDATE OF ...` + сравнение внутри), поэтому сдвиги `order_position`
  триггерами tsk-813 его не задевают.
* `trg_materials_anchor_on_move` (AFTER UPDATE OF course_id на `materials`):
  материал уехал в другой курс — якоря заданий старого узла на него
  обнуляются (вместе с источником).
* Удаление материала — `ON DELETE SET NULL` (+ источник NULL, см. страж):
  задания уходят в конец узла.

Триггеры и UNIQUE `tasks.order_position` (tsk-813), `host_order_position`
(tsk-1198) и функция `set_task_order_position` (tsk-802) не меняются.

Rollback: `alembic downgrade tsk999_help_reflection` — триггеры, индекс и
колонки снимаются; данных до выката публикатора (фаза C) в колонках нет,
кроме ручных якорей методистов.

Revision ID: tsk996_task_anchor
Revises: tsk999_help_reflection
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk996_task_anchor"
down_revision: Union[str, None] = "tsk999_help_reflection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GUARD_FN = """
CREATE OR REPLACE FUNCTION tasks_anchor_guard()
RETURNS TRIGGER AS $$
DECLARE
    anchor_course INTEGER;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- Переезд задания: якорь, оставшийся от старого узла, снимается.
        IF OLD.course_id IS DISTINCT FROM NEW.course_id
           AND NEW.anchor_material_id IS NOT DISTINCT FROM OLD.anchor_material_id THEN
            NEW.anchor_material_id := NULL;
            NEW.anchor_source := NULL;
        END IF;
        IF NEW.anchor_material_id IS NOT DISTINCT FROM OLD.anchor_material_id
           AND NEW.course_id IS NOT DISTINCT FROM OLD.course_id THEN
            RETURN NEW;
        END IF;
        -- Материал удалён (FK SET NULL): якоря нет не по выбору методиста.
        IF OLD.anchor_material_id IS NOT NULL
           AND NEW.anchor_material_id IS NULL
           AND NEW.anchor_source IS NOT DISTINCT FROM OLD.anchor_source
           AND NOT EXISTS (SELECT 1 FROM materials WHERE id = OLD.anchor_material_id) THEN
            NEW.anchor_source := NULL;
        END IF;
    END IF;

    IF NEW.anchor_material_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT course_id INTO anchor_course
    FROM materials
    WHERE id = NEW.anchor_material_id
    FOR SHARE;

    IF anchor_course IS DISTINCT FROM NEW.course_id THEN
        RAISE EXCEPTION
            'tsk-996: якорь задания % на материал % не из курса задания % (курс материала: %)',
            NEW.id, NEW.anchor_material_id, NEW.course_id, anchor_course
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_MOVE_FN = """
CREATE OR REPLACE FUNCTION materials_anchor_on_move()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.course_id IS DISTINCT FROM NEW.course_id THEN
        UPDATE tasks
        SET anchor_material_id = NULL,
            anchor_source = NULL
        WHERE anchor_material_id = NEW.id
          AND course_id IS DISTINCT FROM NEW.course_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "anchor_material_id",
            sa.Integer(),
            sa.ForeignKey(
                "materials.id",
                ondelete="SET NULL",
                name="tasks_anchor_material_id_fkey",
            ),
            nullable=True,
            comment=(
                "tsk-996: материал того же курса, сразу после которого задание "
                "стоит в учебном порядке узла; NULL — в конце узла"
            ),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "anchor_source",
            sa.String(16),
            nullable=True,
            comment=(
                "tsk-996: кто поставил якорь — manual (методист) или publish "
                "(публикатор); переиздание не перезаписывает manual"
            ),
        ),
    )
    op.create_check_constraint(
        "ck_tasks_anchor_source",
        "tasks",
        "anchor_source IS NULL OR anchor_source IN ('publish', 'manual')",
    )
    op.create_index(
        "ix_tasks_anchor_material",
        "tasks",
        ["anchor_material_id"],
        postgresql_where=sa.text("anchor_material_id IS NOT NULL"),
    )

    op.execute(_GUARD_FN)
    op.execute(
        """
        CREATE TRIGGER trg_tasks_anchor_guard
        BEFORE INSERT OR UPDATE OF course_id, anchor_material_id ON tasks
        FOR EACH ROW EXECUTE FUNCTION tasks_anchor_guard();
        """
    )
    op.execute(_MOVE_FN)
    op.execute(
        """
        CREATE TRIGGER trg_materials_anchor_on_move
        AFTER UPDATE OF course_id ON materials
        FOR EACH ROW EXECUTE FUNCTION materials_anchor_on_move();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_materials_anchor_on_move ON materials")
    op.execute("DROP FUNCTION IF EXISTS materials_anchor_on_move()")
    op.execute("DROP TRIGGER IF EXISTS trg_tasks_anchor_guard ON tasks")
    op.execute("DROP FUNCTION IF EXISTS tasks_anchor_guard()")
    op.drop_index("ix_tasks_anchor_material", table_name="tasks")
    op.drop_constraint("ck_tasks_anchor_source", "tasks", type_="check")
    op.drop_column("tasks", "anchor_source")
    op.drop_column("tasks", "anchor_material_id")
