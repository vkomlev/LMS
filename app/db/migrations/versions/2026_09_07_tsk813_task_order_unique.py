"""tsk-813: уникальность пары (course_id, order_position) в tasks — отложенная.

Контекст. Порядок заданий держался на одном триггере
`trg_set_task_order_position`, а на уровне БД дубликаты позиций не запрещало
ничто: у `tasks` был только обычный индекс `idx_tasks_course_order`.
Уникальны были лишь `id` и `external_uid`. Любая запись, обошедшая триггер,
клала дубль молча — и обходов ровно два: `TasksRepository.reorder_tasks`
(глушит триггер по устройству) и ad-hoc скрипты правки данных с тем же
рубильником.

Так и возникли дубликаты в курсах 146, 147 и 1397 (tsk-810, 112 строк):
кабинет методиста нумерует от единицы ОТФИЛЬТРОВАННЫЙ список, и под фильтром
«Активные» активные задания получали 1..N поверх чисел, на которых стояли
выключенные. Ученик этого не видел (ему показывают только активные), поэтому
дефект прожил незамеченным до ручной сверки позиций.

Почему DEFERRABLE INITIALLY DEFERRED, а не обычный UNIQUE. Перестановка по
своей природе проходит через состояния с дублями: и триггер (`order_position
+ 1` по диапазону соседей), и реордер (построчные UPDATE в одной транзакции)
временно ставят двум заданиям одно число. Немедленная проверка отвергала бы
законные операции. Отложенная срабатывает на COMMIT: внутри транзакции всё
разрешено, закоммитить дубль — нельзя.

Порядок работ соблюдён: сначала (в этом же коммите) частичный реордер
перестал оставлять непереданные задания на прежних числах — он достраивает
полный порядок курса. Повесить ограничение раньше значило бы обрушить
рабочий drag-list методиста.

NULL ограничение не трогает: в PostgreSQL два NULL друг другу не
противоречат, поэтому 39 заданий без позиции в курсах 1491–1494 остаются как
есть.

Старый `idx_tasks_course_order` удаляется: индекс, который создаёт
ограничение, покрывает те же запросы (`get_by_course`, next-item picker
Learning Engine) по тем же колонкам в том же порядке. Держать два одинаковых
индекса — платить за каждую запись дважды.

Данные к моменту миграции чистые: дубликатов нет ни в одном курсе (проверено
07.09 после tsk-810), поэтому ALTER проходит без предварительной правки
данных. Если ALTER всё же упадёт — значит дубли успели появиться снова, и
это ответ на вопрос «кто их создаёт»; развести их можно
`scripts/tsk810_fix_duplicate_order_positions.py`.

Revision ID: tsk813_task_order_unique
Revises: tsk803b_result_delete_audit
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tsk813_task_order_unique"
down_revision: Union[str, None] = "tsk803b_result_delete_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Повесить отложенное ограничение и убрать ставший лишним индекс."""
    op.execute(
        """
        ALTER TABLE tasks
        ADD CONSTRAINT tasks_course_order_unique
        UNIQUE (course_id, order_position)
        DEFERRABLE INITIALLY DEFERRED
        """
    )
    op.execute(
        "COMMENT ON CONSTRAINT tasks_course_order_unique ON tasks IS "
        "'tsk-813: в курсе не может быть двух заданий на одной позиции. "
        "Отложенное: перестановка внутри транзакции проходит через дубли.'"
    )
    op.execute("DROP INDEX IF EXISTS idx_tasks_course_order")


def downgrade() -> None:
    """Вернуть обычный индекс и снять ограничение."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_course_order
        ON tasks (course_id, order_position NULLS LAST)
        """
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_course_order_unique")
