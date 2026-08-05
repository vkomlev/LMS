"""tsk-114: аудит изменений course_id/is_active в tasks.

Контекст (tsk-113): 353 задания курса «Python для ЕГЭ» были незаметно
перемещены в архивный курс сменой ``course_id``. Расследовать причину и дату
не удалось — в ``tasks`` нет ``created_at``/``updated_at``, а ``audit_event``
пишет только login-события. На проде уже 42 ученика и 13 592
``task_results`` (2026-08-05) — повтор инцидента теперь ударит по реальному
прогрессу.

Решение (вариант 2 из tsk-114, выбран оператором): AFTER-триггер на
``tasks`` пишет старое/новое значение ``course_id``/``is_active`` в отдельную
append-only таблицу ``task_audit`` при КАЖДОМ UPDATE/DELETE, где эти поля
реально меняются (``WHEN`` в определении триггера — не вызывает функцию на
остальных ~99% UPDATE, которые правят ``task_content``/``solution_rules``/
``order_position``, поэтому обычный трафик студентов и методиста не
замедляется).

Источник изменения (``changed_by``) — кооперативный: session-var
``app.audit_actor`` (``set_config(..., true)``, is_local — тот же принцип
изоляции, что ``app.skip_task_order_trigger`` в
``20260521_120000_tasks_order_position_triggers.py``: видно только текущей
транзакции, не утекает в чужие сессии пула). Приложение проставляет его в
``app/api/deps.py:get_db`` (``service:api_key`` — единственный auth-путь
generic CRUD-роутера ``tasks``, см. ``app/api/main.py``) и в
``TasksService.bulk_upsert`` (``bulk_upsert`` — перекрывает более общий
ярлык). Ad-hoc скрипты правки данных, не проходящие через приложение,
``app.audit_actor`` не ставят — ``changed_by`` останется NULL; это ЧЕСТНЫЙ
сигнал «источник не назвался», а не дефект: колонка ``db_role`` (всегда
заполнена, из ``current_user`` соединения) — дополнительный бесплатный след,
не зависящий от кооперации кода. Конвенция для будущих скриптов
задокументирована в docs/ai/task-audit.md.

``task_audit`` — append-only (тот же паттерн, что ``audit_event`` из
M4 ``20260428_040000_M4_audit_product_events.py``): триггер
``task_audit_no_modify`` запрещает UPDATE/DELETE самих строк аудита, иначе
скрипт, который умеет менять course_id/is_active, мог бы так же тихо стереть
и свой собственный след.

``task_id`` — БЕЗ FK на ``tasks.id``: запись обязана пережить удаление
задания (DELETE — как раз один из аудируемых случаев), поэтому ссылочная
целостность здесь неуместна; ``external_uid`` — снимок на момент изменения,
переживает и удаление, и смену ``course_id``.

Безопасность под нагрузкой: ``CREATE TABLE`` новой таблицы не блокирует
``tasks``; ``CREATE TRIGGER`` на живой ``tasks`` берёт ACCESS EXCLUSIVE лок
кратко (только на установку метаданных триггера, без ALTER TABLE/rewrite/
scan) — тот же профиль, что уже принят в этом проекте для
``trg_set_task_order_position`` (см. дату миграции выше) и ``audit_event`` в
M4. Каждая команда — отдельный ``op.execute`` (asyncpg не допускает
multi-statement).

Revision ID: tsk114_task_audit
Revises: tsk314_course_sampling_config
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "tsk114_task_audit"
down_revision: Union[str, None] = "tsk314_course_sampling_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Шаги:
    1. CREATE TABLE task_audit (BigSerial PK, без FK на tasks).
    2. Индексы: по task_id (история одного задания) и по changed_at
       (недавние изменения по всем заданиям — для расследования «что вообще
       поменялось за последнюю неделю»).
    3. Функция log_task_audit() + AFTER UPDATE/DELETE триггеры на tasks
       (WHEN — только реальные изменения course_id/is_active, плюс
       session-var app.skip_task_audit_trigger — safety-valve по образцу
       app.skip_task_order_trigger, если будущему bulk-фиксу понадобится
       временно отключить логирование КОНКРЕТНО этого триггера).
    4. Append-only enforcement (task_audit_no_modify), зеркало audit_event.
    """

    # 1. Таблица
    op.create_table(
        "task_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "task_id", sa.Integer, nullable=False,
            comment="tasks.id на момент изменения. Без FK: запись должна "
                    "пережить DELETE самого задания.",
        ),
        sa.Column(
            "external_uid", sa.Text, nullable=True,
            comment="Снимок tasks.external_uid на момент изменения",
        ),
        sa.Column(
            "action", sa.String(16), nullable=False,
            comment="'UPDATE' | 'DELETE'",
        ),
        sa.Column("old_course_id", sa.Integer, nullable=True),
        sa.Column("new_course_id", sa.Integer, nullable=True),
        sa.Column("old_is_active", sa.Boolean, nullable=True),
        sa.Column("new_is_active", sa.Boolean, nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"), nullable=False,
            comment="clock_timestamp(), не now(): реальный момент записи "
                    "строки, а не момент начала транзакции",
        ),
        sa.Column(
            "changed_by", sa.Text, nullable=True,
            comment="app.audit_actor на момент записи. NULL = источник не "
                    "проставил себя (прямой SQL / скрипт без опты-ин)",
        ),
        sa.Column(
            "db_role", sa.Text, nullable=False,
            comment="current_user соединения — заполняется всегда, "
                    "не зависит от кооперации приложения",
        ),
        sa.CheckConstraint("action IN ('UPDATE', 'DELETE')", name="task_audit_action_check"),
    )

    # 2. Индексы
    op.create_index(
        "idx_task_audit_task_id",
        "task_audit",
        ["task_id", sa.text("changed_at DESC")],
    )
    op.create_index(
        "idx_task_audit_changed_at",
        "task_audit",
        [sa.text("changed_at DESC")],
    )

    # 3. Функция + триггеры на tasks
    op.execute(
        """
        CREATE OR REPLACE FUNCTION log_task_audit() RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                INSERT INTO task_audit (
                    task_id, external_uid, action,
                    old_course_id, new_course_id,
                    old_is_active, new_is_active,
                    changed_by, db_role
                ) VALUES (
                    OLD.id, OLD.external_uid, 'DELETE',
                    OLD.course_id, NULL,
                    OLD.is_active, NULL,
                    NULLIF(current_setting('app.audit_actor', true), ''),
                    current_user
                );
                RETURN OLD;
            ELSE
                INSERT INTO task_audit (
                    task_id, external_uid, action,
                    old_course_id, new_course_id,
                    old_is_active, new_is_active,
                    changed_by, db_role
                ) VALUES (
                    NEW.id, NEW.external_uid, 'UPDATE',
                    OLD.course_id, NEW.course_id,
                    OLD.is_active, NEW.is_active,
                    NULLIF(current_setting('app.audit_actor', true), ''),
                    current_user
                );
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_task_audit_update
            AFTER UPDATE ON tasks
            FOR EACH ROW
            WHEN (
                current_setting('app.skip_task_audit_trigger', true) IS DISTINCT FROM 'true'
                AND (
                    OLD.course_id IS DISTINCT FROM NEW.course_id
                    OR OLD.is_active IS DISTINCT FROM NEW.is_active
                )
            )
            EXECUTE FUNCTION log_task_audit();
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_task_audit_delete
            AFTER DELETE ON tasks
            FOR EACH ROW
            WHEN (current_setting('app.skip_task_audit_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION log_task_audit();
        """
    )

    # 4. Append-only enforcement (зеркало audit_event, M4)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION task_audit_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'task_audit is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_audit_no_modify
            BEFORE UPDATE OR DELETE ON task_audit
            FOR EACH ROW EXECUTE FUNCTION task_audit_immutable();
        """
    )


def downgrade() -> None:
    """Откат: удаление триггеров/функций/индексов/таблицы. История аудита теряется."""
    op.execute("DROP TRIGGER IF EXISTS task_audit_no_modify ON task_audit;")
    op.execute("DROP FUNCTION IF EXISTS task_audit_immutable();")
    op.execute("DROP TRIGGER IF EXISTS trg_task_audit_delete ON tasks;")
    op.execute("DROP TRIGGER IF EXISTS trg_task_audit_update ON tasks;")
    op.execute("DROP FUNCTION IF EXISTS log_task_audit();")
    op.drop_index("idx_task_audit_changed_at", table_name="task_audit")
    op.drop_index("idx_task_audit_task_id", table_name="task_audit")
    op.drop_table("task_audit")
