"""tsk-996 (фаза D): якорь задания банка на материал прозрачного хозяина.

Пилот 20.10 (курс ОГЭ): задания уроков ОГЭ живут не в узле урока, а в
курсах-банках (задание 6308 — в 1948 «lms:tsk1161:bank:oge1»), подвешенных к
узлу урока (1111 «wp:oge-z1») ПРОЗРАЧНОЙ связью `course_parents.is_transparent`
(tsk-1198). Ученик видит их внутри урока, а якорь фазы B разрешал только
материал курса самого задания — PATCH на материал 2358 урока давал 422.

Правило якоря теперь: материал курса задания ИЛИ материал курса P, если есть
`course_parents(course_id = курс задания, parent_course_id = P,
is_transparent = true)` — прямой родитель, для которого курс задания прозрачен.
Тот же предикат — в сервисе (`TasksService.anchor_course_ids`, 422 в PATCH и
список вариантов методисту) и в порядке узла (`node_sequence.anchor_of`:
в списке хозяина якорь на его материал действует, в самом банке как разделе
практикума — нет).

* `tasks_anchor_guard` — та же функция фазы B, проверка курса материала
  расширена прозрачным родителем. Связь читается `FOR SHARE`: параллельное
  снятие прозрачности дождётся этой транзакции, и его триггер увидит якорь.
  Переезд задания по-прежнему обнуляет якорь (как в фазе B).
* `course_parents_anchor_on_unlink` (AFTER UPDATE OF is_transparent, course_id,
  parent_course_id OR DELETE на `course_parents`) — прозрачная связь снята,
  удалена или перенесена на другого родителя (`set_parent_courses` переносит
  родителя UPDATE-ом `parent_course_id`, tsk-174): якоря заданий подкурса на
  материалы бывшего хозяина обнуляются вместе с источником. Триггер, а не
  сервис: связь правят `courses_repo.set_parent_courses` (UPDATE и DELETE),
  `individual_courses_service`, скрипт разметки tsk-1198 и ручной SQL, а
  удаление курса снимает её каскадом — сервис покрыл бы только часть путей.
  При каскадном удалении курса (строки курса уже нет) триггер ничего не делает:
  задания удаляемого курса уходят каскадом, материалы удаляемого хозяина —
  `ON DELETE SET NULL` на `tasks.anchor_material_id`.
* `materials_anchor_on_move` — материал уехал в другой курс: якорь остаётся
  только у заданий, для которых новый курс материала допустим (свой курс или
  прозрачный хозяин), остальные обнуляются.

Данных не меняет: новые якоря ставят методист и публикатор.

Rollback: `alembic downgrade tsk1384_help_stuck_reason` — функции фазы B
возвращаются, триггер на `course_parents` снимается; якоря на материалы
хозяина (их прежний страж не допускает) обнуляются вместе с источником.

Revision ID: tsk996_bank_anchor
Revises: tsk1384_help_stuck_reason
Create Date: 2026-10-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "tsk996_bank_anchor"
down_revision: Union[str, None] = "tsk1384_help_stuck_reason"
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

    IF anchor_course IS NOT DISTINCT FROM NEW.course_id THEN
        RETURN NEW;
    END IF;

    -- tsk-996 (фаза D): материал прозрачного хозяина — курс задания подвешен
    -- к курсу материала прозрачной связью (tsk-1198).
    IF anchor_course IS NOT NULL THEN
        PERFORM 1
        FROM course_parents
        WHERE course_id = NEW.course_id
          AND parent_course_id = anchor_course
          AND is_transparent
        FOR SHARE;
        IF FOUND THEN
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION
        'tsk-996: якорь задания % на материал % не из курса задания % и не из его прозрачного хозяина (курс материала: %)',
        NEW.id, NEW.anchor_material_id, NEW.course_id, anchor_course
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""

_MOVE_FN = """
CREATE OR REPLACE FUNCTION materials_anchor_on_move()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.course_id IS DISTINCT FROM NEW.course_id THEN
        UPDATE tasks t
        SET anchor_material_id = NULL,
            anchor_source = NULL
        WHERE t.anchor_material_id = NEW.id
          AND t.course_id IS DISTINCT FROM NEW.course_id
          AND NOT EXISTS (
              SELECT 1 FROM course_parents cp
              WHERE cp.course_id = t.course_id
                AND cp.parent_course_id = NEW.course_id
                AND cp.is_transparent
          );
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

_UNLINK_FN = """
CREATE OR REPLACE FUNCTION course_parents_anchor_on_unlink()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT OLD.is_transparent THEN
        RETURN NULL;
    END IF;
    -- Каскадное удаление курса: задания и материалы уходят своими путями.
    IF NOT EXISTS (SELECT 1 FROM courses WHERE id = OLD.course_id)
       OR NOT EXISTS (SELECT 1 FROM courses WHERE id = OLD.parent_course_id) THEN
        RETURN NULL;
    END IF;
    -- Связь (подкурс, хозяин) осталась прозрачной — якоря действуют.
    IF EXISTS (
        SELECT 1 FROM course_parents
        WHERE course_id = OLD.course_id
          AND parent_course_id = OLD.parent_course_id
          AND is_transparent
    ) THEN
        RETURN NULL;
    END IF;
    UPDATE tasks t
    SET anchor_material_id = NULL,
        anchor_source = NULL
    FROM materials m
    WHERE m.id = t.anchor_material_id
      AND m.course_id = OLD.parent_course_id
      AND t.course_id = OLD.course_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

# Функции фазы B (`2026_10_19_tsk996_task_anchor.py`) — для downgrade.
_GUARD_FN_PHASE_B = """
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

_MOVE_FN_PHASE_B = """
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
    op.execute(_GUARD_FN)
    op.execute(_MOVE_FN)
    op.execute(_UNLINK_FN)
    op.execute(
        """
        CREATE TRIGGER trg_course_parents_anchor_on_unlink
        AFTER UPDATE OF is_transparent, course_id, parent_course_id OR DELETE
        ON course_parents
        FOR EACH ROW EXECUTE FUNCTION course_parents_anchor_on_unlink();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_course_parents_anchor_on_unlink ON course_parents"
    )
    op.execute("DROP FUNCTION IF EXISTS course_parents_anchor_on_unlink()")
    # Якоря на материал хозяина прежний страж не допускает — снимаются.
    op.execute(
        """
        UPDATE tasks t
        SET anchor_material_id = NULL,
            anchor_source = NULL
        FROM materials m
        WHERE m.id = t.anchor_material_id
          AND m.course_id <> t.course_id
        """
    )
    op.execute(_MOVE_FN_PHASE_B)
    op.execute(_GUARD_FN_PHASE_B)
