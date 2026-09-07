"""tsk-803 (продолжение): аудит УДАЛЕНИЯ результата в task_result_audit.

Контекст. Миграция `tsk803_task_result_audit` закрыла правку оценки, но
оставила дыру, названную в её же шапке: удаление результата следа не
оставляло, а значит пара «удалить и вставить заново» позволяла заменить
оценку неотслеживаемо — ровно тот обход, ради закрытия которого журнал и
заводился. Решение оператора (07.09): включить аудит и на удаление.

Цена вопроса мала: на проде на момент выката — 39 удалений `task_results`
против 22 122 вставок и 4 944 обновлений. Почти все удаления приходят
каскадом от `users`/`tasks`, то есть при удалении ученика или задания — как
раз тот случай, когда след «какие оценки были на момент удаления» ценнее
всего: сами строки исчезают безвозвратно, и восстановить прогресс потом
нечем.

Что пишется. Для `DELETE` — снимок последнего состояния: `old_score` /
`old_is_correct` заполнены, `new_*` пусты (та же форма, что у `task_audit`
из tsk-114, где `new_course_id` у DELETE тоже NULL). `action = 'DELETE'` —
значение уже разрешено CHECK-ограничением `task_result_audit_action_check`,
заведённым «на вырост», поэтому схема не меняется вовсе: только функция и
новый триггер.

Почему нужна ветка по `TG_OP`. В `DELETE`-триггере записи `NEW` не
существует: прежнее тело функции, обращавшееся к `NEW.id`, на удалении
упало бы с ошибкой и заблокировало бы само удаление. Поэтому функция
переписывается целиком с разветвлением, а не дополняется.

Тот же session-var `app.skip_task_result_audit_trigger` гасит и этот триггер:
у операций обслуживания (перенос данных, чистка тестовых артефактов) должен
остаться штатный способ не писать в журнал, а не соблазн отключать триггер
через ALTER TABLE.

Revision ID: tsk803b_result_delete_audit
Revises: tsk803_task_result_audit
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tsk803b_result_delete_audit"
down_revision: Union[str, None] = "tsk803_task_result_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FN_WITH_DELETE = """
CREATE OR REPLACE FUNCTION log_task_result_audit() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO task_result_audit (
            result_id, task_id, user_id, action,
            old_score, new_score,
            old_is_correct, new_is_correct,
            changed_by, db_role
        ) VALUES (
            OLD.id, OLD.task_id, OLD.user_id, 'DELETE',
            OLD.score, NULL,
            OLD.is_correct, NULL,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN OLD;
    ELSE
        INSERT INTO task_result_audit (
            result_id, task_id, user_id, action,
            old_score, new_score,
            old_is_correct, new_is_correct,
            changed_by, db_role
        ) VALUES (
            NEW.id, NEW.task_id, NEW.user_id, 'UPDATE',
            OLD.score, NEW.score,
            OLD.is_correct, NEW.is_correct,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;
"""

# Прежнее тело (tsk803_task_result_audit) — только UPDATE, для downgrade.
_FN_UPDATE_ONLY = """
CREATE OR REPLACE FUNCTION log_task_result_audit() RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO task_result_audit (
        result_id, task_id, user_id, action,
        old_score, new_score,
        old_is_correct, new_is_correct,
        changed_by, db_role
    ) VALUES (
        NEW.id, NEW.task_id, NEW.user_id, 'UPDATE',
        OLD.score, NEW.score,
        OLD.is_correct, NEW.is_correct,
        NULLIF(current_setting('app.audit_actor', true), ''),
        current_user
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Разветвить функцию по TG_OP и повесить AFTER DELETE триггер.

    Порядок важен: сначала функция учится работать без `NEW`, и только потом
    появляется триггер, который её в таком режиме вызывает.
    """
    op.execute(_FN_WITH_DELETE)
    op.execute(
        """
        CREATE TRIGGER trg_task_result_audit_delete
            AFTER DELETE ON task_results
            FOR EACH ROW
            WHEN (current_setting('app.skip_task_result_audit_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION log_task_result_audit();
        """
    )


def downgrade() -> None:
    """Снять DELETE-триггер и вернуть тело функции без ветки удаления.

    Обратный порядок: пока триггер существует, функция обязана уметь работать
    без `NEW`.
    """
    op.execute("DROP TRIGGER IF EXISTS trg_task_result_audit_delete ON task_results;")
    op.execute(_FN_UPDATE_ONLY)
