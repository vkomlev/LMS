"""tsk-802: перенос задания между курсами в триггере set_task_order_position.

Контекст. `trg_set_task_order_position` (BEFORE INSERT OR UPDATE на `tasks`,
миграция 20260521_120000) написан для ПЕРЕСТАНОВКИ ВНУТРИ ОДНОГО КУРСА: на
UPDATE он берёт `OLD.order_position`, сравнивает с `NEW.order_position` и
сдвигает соседей, чтобы освободить место. Обе стороны сравнения считаются по
`NEW.course_id`.

При смене `course_id` это ломается: старая позиция относится к ДРУГОМУ курсу,
и сравнение теряет смысл — триггер сдвигает задания целевого курса по
диапазону, вычисленному из позиции в исходном. Инцидент tsk-801 (06.09):
перенос задания 146 (было курс 108 позиция 41, стало курс 111 позиция 43)
попал в ветку «сдвиг вниз» и выполнил

    UPDATE tasks SET order_position = order_position - 1
    WHERE course_id = 111 AND order_position > 41 AND order_position <= 43

— под сдвиг попало постороннее задание 190 (41 -> 40, где уже стояло 188):
дубликат позиции в курсе, которого никто не трогал. Молча: ошибки нет,
транзакция проходит, своё задание на месте — испорчено чужое.

Решение. В ветке `TG_OP = 'UPDATE'` отличать ПЕРЕЕЗД (`OLD.course_id IS
DISTINCT FROM NEW.course_id`) от перестановки и обрабатывать его как два
разных события в двух разных курсах:

* в ИСХОДНОМ курсе — как удаление: всё, что стояло ниже `OLD.order_position`,
  поднимается на единицу (тот же смысл, что у `reorder_tasks_after_delete`),
  иначе на месте уехавшего задания остаётся дыра;
* в ЦЕЛЕВОМ курсе — как вставка: место под `NEW.order_position`
  освобождается сдвигом вниз, либо задание встаёт в конец.

Куда встаёт задание, если позицию не назначили. Триггер не видит списка
колонок в `SET`: `UPDATE tasks SET course_id = 111` доезжает до него как
`NEW.order_position = OLD.order_position` — цифра из ЧУЖОГО курса, неотличимая
от сознательно заданной. Поэтому позиция не угадывается по совпадению, а
ЗАЖИМАЕТСЯ в диапазон целевого курса: `NULL` либо значение больше
`MAX(order_position) + 1` целевого курса означает конец, всё остальное —
вставку на указанное место со сдвигом соседей. Так типовой перенос заданий из
большого курса в маленький (позиция 41 в курс из пяти) не оставляет дыр
6..40, а явно заданная достижимая позиция всегда уважается — в том числе
совпавшая со старой (перенос «второго задания на второе место» — обычное
дело, а не признак того, что позицию не указывали). Контракт зафиксирован в
docs/database-triggers-contract.md.

Прежнее поведение перестановки внутри курса не меняется — ветки INSERT и
«тот же курс» перенесены дословно (проверяется тестами T1-T9 из
tests/test_tasks_order_position.py).

Revision ID: tsk802_task_move_courses
Revises: tsk804_after_leave_lessons
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tsk802_task_move_courses"
down_revision: Union[str, None] = "tsk804_after_leave_lessons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Новое тело: ветки INSERT и «перестановка внутри курса» дословно из
# 20260521_120000_tasks_order_position_triggers.py, добавлен блок переезда.
_FN_NEW = """
CREATE OR REPLACE FUNCTION set_task_order_position()
RETURNS TRIGGER AS $$
DECLARE
    max_order INTEGER;
    old_order INTEGER;
BEGIN
    -- Максимум ЦЕЛЕВОГО курса. При переезде строка физически ещё в старом
    -- курсе (BEFORE UPDATE), поэтому в выборку не попадает; `id != NEW.id`
    -- оставлен для перестановки внутри курса.
    SELECT COALESCE(MAX(order_position), 0)
    INTO max_order
    FROM tasks
    WHERE course_id = NEW.course_id
      AND (TG_OP = 'INSERT' OR id != NEW.id);

    IF TG_OP = 'INSERT' THEN
        IF NEW.order_position IS NULL THEN
            NEW.order_position := max_order + 1;
        ELSE
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position + 1
            WHERE course_id = NEW.course_id
              AND order_position >= NEW.order_position
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        old_order := OLD.order_position;

        -- tsk-802: ПЕРЕЕЗД между курсами — не перестановка.
        -- Старая позиция относится к другому курсу, сравнивать её с новой
        -- нельзя: именно это портило соседей в целевом курсе.
        IF OLD.course_id IS DISTINCT FROM NEW.course_id THEN
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);

            -- Исходный курс: уплотнить, как после удаления.
            IF old_order IS NOT NULL THEN
                UPDATE tasks
                SET order_position = order_position - 1
                WHERE course_id = OLD.course_id
                  AND order_position > old_order
                  AND id != NEW.id;
            END IF;

            -- Целевой курс: позиция зажимается в его диапазон. NULL или
            -- цифра за пределами курса (типично — унаследованная из
            -- исходного) означает конец; достижимая позиция уважается.
            IF NEW.order_position IS NULL
               OR NEW.order_position > max_order + 1 THEN
                NEW.order_position := max_order + 1;
            ELSE
                UPDATE tasks
                SET order_position = order_position + 1
                WHERE course_id = NEW.course_id
                  AND order_position >= NEW.order_position
                  AND id != NEW.id;
            END IF;

            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
            RETURN NEW;
        END IF;

        IF (NEW.order_position IS NULL AND old_order IS NULL)
           OR NEW.order_position = old_order THEN
            RETURN NEW;
        END IF;

        IF old_order IS NULL THEN
            IF NEW.order_position IS NULL THEN
                NEW.order_position := max_order + 1;
            ELSE
                PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                UPDATE tasks
                SET order_position = order_position + 1
                WHERE course_id = NEW.course_id
                  AND order_position >= NEW.order_position
                  AND id != NEW.id;
                PERFORM set_config('app.skip_task_order_trigger', 'false', true);
            END IF;
            RETURN NEW;
        END IF;

        IF NEW.order_position IS NULL THEN
            NEW.order_position := max_order + 1;
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position - 1
            WHERE course_id = NEW.course_id
              AND order_position > old_order
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
            RETURN NEW;
        END IF;

        IF NEW.order_position > old_order THEN
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position - 1
            WHERE course_id = NEW.course_id
              AND order_position > old_order
              AND order_position <= NEW.order_position
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
        ELSE
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position + 1
            WHERE course_id = NEW.course_id
              AND order_position >= NEW.order_position
              AND order_position < old_order
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Прежнее тело (20260521_120000) — дословно, для downgrade.
_FN_OLD = """
CREATE OR REPLACE FUNCTION set_task_order_position()
RETURNS TRIGGER AS $$
DECLARE
    max_order INTEGER;
    old_order INTEGER;
BEGIN
    SELECT COALESCE(MAX(order_position), 0)
    INTO max_order
    FROM tasks
    WHERE course_id = NEW.course_id
      AND (TG_OP = 'INSERT' OR id != NEW.id);

    IF TG_OP = 'INSERT' THEN
        IF NEW.order_position IS NULL THEN
            NEW.order_position := max_order + 1;
        ELSE
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position + 1
            WHERE course_id = NEW.course_id
              AND order_position >= NEW.order_position
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        old_order := OLD.order_position;

        IF (NEW.order_position IS NULL AND old_order IS NULL)
           OR NEW.order_position = old_order THEN
            RETURN NEW;
        END IF;

        IF old_order IS NULL THEN
            IF NEW.order_position IS NULL THEN
                NEW.order_position := max_order + 1;
            ELSE
                PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                UPDATE tasks
                SET order_position = order_position + 1
                WHERE course_id = NEW.course_id
                  AND order_position >= NEW.order_position
                  AND id != NEW.id;
                PERFORM set_config('app.skip_task_order_trigger', 'false', true);
            END IF;
            RETURN NEW;
        END IF;

        IF NEW.order_position IS NULL THEN
            NEW.order_position := max_order + 1;
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position - 1
            WHERE course_id = NEW.course_id
              AND order_position > old_order
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
            RETURN NEW;
        END IF;

        IF NEW.order_position > old_order THEN
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position - 1
            WHERE course_id = NEW.course_id
              AND order_position > old_order
              AND order_position <= NEW.order_position
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
        ELSE
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks
            SET order_position = order_position + 1
            WHERE course_id = NEW.course_id
              AND order_position >= NEW.order_position
              AND order_position < old_order
              AND id != NEW.id;
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Заменить тело функции. Сам триггер не пересоздаётся — CREATE OR REPLACE
    подменяет реализацию под ним, лока на `tasks` не берётся."""
    op.execute(_FN_NEW)


def downgrade() -> None:
    """Вернуть тело версии 20260521_120000 (перенос между курсами снова портит
    позиции соседей — см. шапку)."""
    op.execute(_FN_OLD)
