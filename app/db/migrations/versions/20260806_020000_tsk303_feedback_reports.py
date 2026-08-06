"""tsk-303 Фаза 4: обращения о проблемах системы, контента и идеях фич (Поток B).

Второй поток единого инбокса. С лестницей помощи (Поток A) он не пересекается
ничем, кроме экрана: там заявка ученика по конкретному заданию, здесь —
сообщение о системе или контенте, и адресат другой (методист/админ, а не
преподаватель ученика). Поэтому отдельная таблица, а не ещё один
`request_type` в `help_requests`: у той всё построено вокруг пары
(ученик, задание), которой здесь может не быть вовсе.

**Автор — nullable (`ON DELETE SET NULL`).** Обращение переживает удаление
учётки: «в уроке битая ссылка» остаётся правдой и без автора, а каскад стирал
бы список задач методиста задним числом.

**Привязка к курсу/материалу/заданию — три отдельных nullable FK**, а не
свободный JSON: по ним методист попадает в конкретный объект, и целостность
держит БД. Все три пустые — обращение про систему в целом, это нормальный
случай, а не пропуск.

**`title` намеренно НЕТ.** Форма и так просит выбрать тип и описать проблему;
третье обязательное поле — лишняя ступень на пути к тому, чтобы человек вообще
написал. Список инбокса показывает тип + начало текста.

Rollback: `alembic downgrade tsk303_help_ladder` сносит таблицу целиком.
ВНИМАНИЕ: вместе с ней пропадают все обращения (жалобы на контент, идеи) —
восстановить будет неоткуда. На момент применения таблица пуста.

Revision ID: tsk303_feedback_reports
Revises: tsk303_help_ladder
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk303_feedback_reports"
down_revision: Union[str, None] = "tsk303_help_ladder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPES = ("bug", "content", "feature_idea")
_STATUSES = ("open", "closed")


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    """Создаёт таблицу обращений и индексы под инбокс методиста."""
    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "report_type",
            sa.String(32),
            nullable=False,
            comment="bug — проблема системы; content — проблема контента; feature_idea — идея",
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "author_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Кто написал. NULL — учётка удалена, само обращение остаётся",
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "course_id",
            sa.Integer,
            sa.ForeignKey("courses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "material_id",
            sa.Integer,
            sa.ForeignKey("materials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            sa.Integer,
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_comment", sa.Text, nullable=True),
        sa.CheckConstraint(
            f"report_type IN ({_in_list(_TYPES)})", name="ck_feedback_reports_type"
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(_STATUSES)})", name="ck_feedback_reports_status"
        ),
        # Пустой текст — обращение без содержания: в инбоксе видно строку, а
        # разобрать нечего. Тот же класс, что уже закрыт у вебинар-ссылки
        # (см. `docs/ai/ERRORS.md`, 2026-07-22): проверка через `~ '\S'`, а не
        # `length(btrim(...)) > 0` — btrim без второго аргумента срезает только
        # пробелы и пропустил бы строку из табуляции.
        sa.CheckConstraint(r"body ~ '\S'", name="ck_feedback_reports_body_not_blank"),
        # Закрытие всегда со следом: «закрыто» без времени — состояние без истории.
        sa.CheckConstraint(
            "(status = 'open') = (closed_at IS NULL)",
            name="ck_feedback_reports_closed_has_timestamp",
        ),
        comment="tsk-303 Поток B: обращения о проблемах системы/контента и идеи фич",
    )
    # Инбокс методиста: сначала открытые, свежие сверху.
    op.create_index(
        "idx_feedback_reports_status_created",
        "feedback_reports",
        ["status", sa.text("created_at DESC")],
    )
    # «Мои обращения» у преподавателя.
    op.create_index(
        "idx_feedback_reports_author",
        "feedback_reports",
        ["author_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Сносит таблицу обращений целиком (вместе со всей их историей)."""
    op.drop_index("idx_feedback_reports_author", table_name="feedback_reports")
    op.drop_index("idx_feedback_reports_status_created", table_name="feedback_reports")
    op.drop_table("feedback_reports")
