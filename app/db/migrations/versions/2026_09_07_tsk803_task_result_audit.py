"""tsk-803: журнал правок оценок — таблица task_result_audit + триггер на task_results.

Контекст. При починке 51 ложного незачёта (tsk-801, 06.09) выяснилось, что
правку боевой оценки откатить нечем: восстановление держалось на бэкапе в
скратчпаде сессии и списке id, вписанном в задачу руками.

Что уже было (проверено, а не предположено): штатный путь ПРИКРЫТ —
`POST /review/grade` пишет в `audit_event` событие `teacher.review.graded`
(142 события на 06.09). Пробелов ровно два:

1. **Прямые правки БД журнал обходят.** Именно ими чинилась бо́льшая часть
   дефектов августа-сентября (tsk-760, tsk-796, tsk-801): все шли скриптом по
   протоколу `/db-check`, и ни одна не попала в `audit_event`. `audit_event` —
   журнал уровня приложения, он в принципе не видит того, что сделано в обход
   приложения.
2. **Прежнее значение не хранится нигде.** В `details` события `graded` лежит
   только новый балл, а `previous_score` в уведомлении ученику заполняется
   жёстким `None`. Понять «что стало» можно, «что было» — нет.

Асимметрия, ради которой заведена задача: содержимое заданий защищено
триггером `trg_task_audit_update` (`task_audit`, tsk-114/tsk-636 — старое и
новое значение курса, активности, эталона), а оценка ученика — то, от чего
зависит его прогресс и доступ к следующим курсам — не защищена ничем.

Решение — тот же паттерн, что у заданий: триггер уровня БД ловит ЛЮБОЙ путь
записи (API, скрипт, ручной `UPDATE` в psql), а не только кооперативный.

Только UPDATE. `task_results` растёт на сотни строк в день (на 06.09 — 22 276
строк, из них 14 676 проверенных); аудит на INSERT обогнал бы исходную
таблицу за неделю, ничего не добавив: сама запись результата и есть его
первое состояние. По статистике прода на момент миграции — 22 122 вставки
против 4 944 обновлений, то есть журнал растёт примерно впятеро медленнее
самой таблицы. DELETE НЕ аудируется (39 удалений за тот же период — почти все
каскадом от `users`/`tasks`); это осознанная граница задачи, а не упущение:
удаление результата остаётся непрослеживаемым, см. docs/ai/task-result-audit.md.

Условие `WHEN` — только реальное изменение `score`/`is_correct`. Остальные
UPDATE (`review_claim_*` при захвате проверки, `metrics`, `code_review`,
`checked_at`) функцию не вызывают, поэтому обычный трафик очереди
преподавателя не замедляется.

Ретроспективы не будет: восстановить, кто правил оценки ДО появления триггера,
нельзя — данных нет. Правки tsk-801 задокументированы списком id в самой
задаче, более ранние (tsk-760, tsk-796) — нет.

`result_id` — БЕЗ FK на `task_results.id`: запись журнала обязана пережить
удаление результата (каскад от `users`/`tasks`), иначе история исчезнет
вместе с тем, что она объясняет. По той же причине `task_id`/`user_id` —
снимки на момент изменения.

Append-only (`task_result_audit_no_modify`) — зеркало `task_audit` и
`audit_event`: тот, кто умеет менять оценки, не должен уметь стереть след.

Revision ID: tsk803_task_result_audit
Revises: tsk802_task_move_courses
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "tsk803_task_result_audit"
down_revision: Union[str, None] = "tsk802_task_move_courses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Шаги:
    1. CREATE TABLE task_result_audit (bigserial PK, без FK на task_results).
    2. Индексы: история одной работы, недавние правки по всем работам,
       правки оценок конкретного ученика.
    3. Функция log_task_result_audit() + AFTER UPDATE триггер на task_results
       (WHEN — только реальное изменение score/is_correct, плюс session-var
       app.skip_task_result_audit_trigger как safety-valve по образцу
       app.skip_task_audit_trigger).
    4. Append-only enforcement (task_result_audit_no_modify).
    """

    # 1. Таблица
    op.create_table(
        "task_result_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "result_id", sa.Integer, nullable=False,
            comment="task_results.id на момент изменения. Без FK: запись должна "
                    "пережить удаление результата (каскад от users/tasks).",
        ),
        sa.Column(
            "task_id", sa.Integer, nullable=True,
            comment="Снимок task_results.task_id — за какое задание оценка",
        ),
        sa.Column(
            "user_id", sa.Integer, nullable=True,
            comment="Снимок task_results.user_id — чья оценка изменена",
        ),
        sa.Column(
            "action", sa.String(16), nullable=False,
            comment="Сейчас всегда 'UPDATE'. 'DELETE' зарезервирован: удаление "
                    "результата не аудируется (см. шапку миграции).",
        ),
        sa.Column("old_score", sa.Integer, nullable=True),
        sa.Column("new_score", sa.Integer, nullable=True),
        sa.Column("old_is_correct", sa.Boolean, nullable=True),
        sa.Column("new_is_correct", sa.Boolean, nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"), nullable=False,
            comment="clock_timestamp(), не now(): реальный момент записи строки, "
                    "а не момент начала транзакции — важно для пакетных правок",
        ),
        sa.Column(
            "changed_by", sa.Text, nullable=True,
            comment="app.audit_actor на момент записи. NULL = источник не "
                    "назвался (прямой SQL / скрипт без опты-ин)",
        ),
        sa.Column(
            "db_role", sa.Text, nullable=False,
            comment="current_user соединения — заполняется всегда, не зависит "
                    "от кооперации кода",
        ),
        sa.CheckConstraint(
            "action IN ('UPDATE', 'DELETE')", name="task_result_audit_action_check"
        ),
        comment="tsk-803: append-only журнал изменений оценки (score/is_correct) "
                "в task_results. Наполняется триггером, писать из кода не нужно.",
    )

    # 2. Индексы
    op.create_index(
        "idx_task_result_audit_result",
        "task_result_audit",
        ["result_id", sa.text("changed_at DESC")],
    )
    op.create_index(
        "idx_task_result_audit_changed_at",
        "task_result_audit",
        [sa.text("changed_at DESC")],
    )
    # Вопрос расследования «правили ли оценки этому ученику» — самый частый
    # после «что было с этой работой», и по result_id он не отвечается.
    op.create_index(
        "idx_task_result_audit_user",
        "task_result_audit",
        ["user_id", sa.text("changed_at DESC")],
    )

    # 3. Функция + триггер
    op.execute(
        """
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
    )

    op.execute(
        """
        CREATE TRIGGER trg_task_result_audit_update
            AFTER UPDATE ON task_results
            FOR EACH ROW
            WHEN (
                current_setting('app.skip_task_result_audit_trigger', true) IS DISTINCT FROM 'true'
                AND (
                    OLD.score IS DISTINCT FROM NEW.score
                    OR OLD.is_correct IS DISTINCT FROM NEW.is_correct
                )
            )
            EXECUTE FUNCTION log_task_result_audit();
        """
    )

    # 4. Append-only enforcement (зеркало task_audit / audit_event)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION task_result_audit_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'task_result_audit is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_result_audit_no_modify
            BEFORE UPDATE OR DELETE ON task_result_audit
            FOR EACH ROW EXECUTE FUNCTION task_result_audit_immutable();
        """
    )


def downgrade() -> None:
    """Откат: удаление триггеров/функций/индексов/таблицы. История правок оценок теряется."""
    op.execute("DROP TRIGGER IF EXISTS task_result_audit_no_modify ON task_result_audit;")
    op.execute("DROP FUNCTION IF EXISTS task_result_audit_immutable();")
    op.execute("DROP TRIGGER IF EXISTS trg_task_result_audit_update ON task_results;")
    op.execute("DROP FUNCTION IF EXISTS log_task_result_audit();")
    op.drop_index("idx_task_result_audit_user", table_name="task_result_audit")
    op.drop_index("idx_task_result_audit_changed_at", table_name="task_result_audit")
    op.drop_index("idx_task_result_audit_result", table_name="task_result_audit")
    op.drop_table("task_result_audit")
