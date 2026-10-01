"""tsk-1192: у каждого задания есть `external_uid` — код `lms:task-{id}` по умолчанию.

Ученический клиент строит адрес задания из `external_uid`; у 29 активных заданий
его не было (курс 1451 «Рекурсия», курс 165 «Черепаха», два старых задания курса 1),
и пункты домашней работы с ними рисовались текстом без ссылки (74 пункта на проде).
Их создавали скрипты прямым `INSERT INTO tasks` без кода (tsk-412, tsk-524), а API
создания и импорт таблиц тоже допускают пустое поле. Поэтому закрываем на уровне
БД: триггер ставит код любому заданию, пришедшему без него, — каким бы путём оно
ни пришло. Своего кода у задания нет — значит, внешней системы за ним нет, и
номер из нашей же базы ничего не ломает. Уникальность держит прежний индекс
`tasks_external_uid_key`.

Rollback: `alembic downgrade tsk1147_reply_kind` — снимается триггер; проставленные
коды остаются (они безвредны, а стирать их значит снова сломать ссылки).

Revision ID: tsk1192_task_external_uid
Revises: tsk1147_reply_kind
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "tsk1192_task_external_uid"
down_revision: Union[str, None] = "tsk1147_reply_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Заполнить пустые коды и поставить триггер на будущие вставки и правки."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tasks_default_external_uid() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.external_uid IS NULL OR btrim(NEW.external_uid) = '' THEN
                NEW.external_uid := 'lms:task-' || NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER tasks_default_external_uid
        BEFORE INSERT OR UPDATE OF external_uid ON tasks
        FOR EACH ROW EXECUTE FUNCTION tasks_default_external_uid()
        """
    )
    op.execute(
        "UPDATE tasks SET external_uid = 'lms:task-' || id "
        "WHERE external_uid IS NULL OR btrim(external_uid) = ''"
    )


def downgrade() -> None:
    """Снять триггер; коды остаются."""
    op.execute("DROP TRIGGER IF EXISTS tasks_default_external_uid ON tasks")
    op.execute("DROP FUNCTION IF EXISTS tasks_default_external_uid()")
