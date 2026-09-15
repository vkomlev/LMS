"""tsk-943: вложения к заявке помощи + заявки по материалам.

Три доработки одного механизма (оператор, 15.09):

1. Вложение к заявке (скрин ошибки, файл, код) — четыре новые nullable-колонки,
   имя как в `attempt_attachments`/сообщениях: сам файл живёт в объектном
   хранилище (`attachment_storage`, пространство `help_requests`), в таблице —
   только идентификатор ключа и метаданные для отображения преподавателю.
2. Заявка «Я не понял» по МАТЕРИАЛУ, не только по заданию — `task_id` был
   `NOT NULL`, материал заданием не является. Колонка становится nullable,
   добавляется `material_id` (тоже nullable, FK на `materials`). Инвариант
   «ровно один из task_id/material_id» проверяется в сервисном слое
   (`help_requests_service`), не CHECK-констрейнтом — тот же принцип, что уже
   применён к пустому тексту заявки (tsk-261, A10): гейт на уровне API даёт
   понятную ошибку клиенту, а не голое нарушение ограничения БД.

Прочитано перед миграцией (read-only, локальная копия боевой схемы,
2026-09-15): `help_requests` — 937 строк, единственное ограничение на
`task_id` — `NOT NULL` и FK на `tasks(id) ON DELETE CASCADE`, других
constraints/триггеров на эту колонку нет. Оба изменения безопасны на живых
данных: снятие `NOT NULL` — только метаданные каталога, новые колонки —
nullable без `server_default`, обе операции без блокировки таблицы на чтение
(Postgres >= 11 не переписывает файл данных ради ADD COLUMN NULL).

Rollback: `alembic downgrade tsk930_admin_login_link`. Восстановить
`task_id NOT NULL` при откате НЕЛЬЗЯ безусловно — если к моменту отката уже
есть заявки по материалам (`task_id IS NULL`), ALTER упадёт на них. Даунгрейд
поэтому не трогает nullable у `task_id` — теряется только строгость
ограничения, данные и остальные колонки целы.

Revision ID: tsk943_help_request_attachments
Revises: tsk930_admin_login_link
Create Date: 2026-09-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk943_help_request_attachments"
down_revision: Union[str, None] = "tsk930_admin_login_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """task_id -> nullable, + material_id, + метаданные вложения."""
    op.alter_column("help_requests", "task_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "help_requests",
        sa.Column(
            "material_id",
            sa.Integer(),
            nullable=True,
            comment=(
                "Материал, по которому задан вопрос «Я не понял» (tsk-943). "
                "Ровно один из task_id/material_id заполнен — проверяется в "
                "help_requests_service, не здесь."
            ),
        ),
    )
    op.create_foreign_key(
        "help_requests_material_id_fkey",
        "help_requests",
        "materials",
        ["material_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "attachment_id",
            sa.String(length=255),
            nullable=True,
            comment="Ключ файла в attachment_storage (пространство help_requests).",
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "attachment_filename",
            sa.String(length=255),
            nullable=True,
            comment="Исходное имя файла для отображения преподавателю.",
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "attachment_content_type",
            sa.String(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "attachment_size_bytes",
            sa.Integer(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Убрать вложение и material_id. task_id остаётся nullable (см. docstring)."""
    op.drop_column("help_requests", "attachment_size_bytes")
    op.drop_column("help_requests", "attachment_content_type")
    op.drop_column("help_requests", "attachment_filename")
    op.drop_column("help_requests", "attachment_id")
    op.drop_constraint("help_requests_material_id_fkey", "help_requests", type_="foreignkey")
    op.drop_column("help_requests", "material_id")
