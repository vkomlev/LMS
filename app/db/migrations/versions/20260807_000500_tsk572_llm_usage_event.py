"""tsk-572 этап 1: учёт расхода LLM.

Счётчик включаем с первого дня, лимиты — нет (контракт §8). Причина: подписные
тарифы на ИИ-наставника обсуждаются как следующий шаг, и вводить их без истории
расхода — значит назначать цифры наугад. Плюс это общий инструмент двух задач:
наставник (`purpose='tutor'`) и судья ИИ-авторства (`purpose='code_review'`).

**Почему таблица, а не лог.** По логу нельзя ответить «сколько потратил вот этот
ученик за месяц» — а именно этот вопрос задаст тарифная модель. Порядок нагрузки
скромный: ~1500 вызовов/мес у судьи + ~160 диалогов/мес у наставника.

**Неуспешные вызовы пишутся тоже** и это не избыточность: по ним видно, как часто
отбивает квота ключа и куда уходят деньги без результата. `outcome` хранит либо
`'ok'`, либо имя класса ошибки.

`student_id` — nullable и БЕЗ внешнего ключа с каскадом на удаление: история
расхода переживает удаление учётной записи (это финансовая летопись, а не часть
профиля). Индекс по `(student_id, created_at)` — под будущий вопрос о лимитах,
по `(purpose, created_at)` — под отчёт «сколько стоит каждая функция».

Rollback: `alembic downgrade tsk231_dep_auto_assign` — таблица удаляется вместе с
накопленной историей. Данные восстановимы только из логов провайдера, поэтому
перед откатом на проде снять дамп:
`pg_dump -t llm_usage_event ...`

Revision ID: tsk572_llm_usage
Revises: tsk231_dep_auto_assign
Create Date: 2026-08-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk572_llm_usage"
down_revision: Union[str, None] = "tsk231_dep_auto_assign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False,
                  comment="Кто потратил: tutor | code_review | ..."),
        sa.Column("student_id", sa.Integer(), nullable=True,
                  comment="Для подписных лимитов; NULL у системных задач"),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(length=48), nullable=False,
                  comment="'ok' или имя класса ошибки (LLMQuotaExceeded, ...)"),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment="Свободные детали: текст ошибки, длина ответа"),
        comment="tsk-572: расход LLM по вызовам — основа тарифов и отчётности",
    )
    op.create_index("ix_llm_usage_student_time", "llm_usage_event",
                    ["student_id", "created_at"])
    op.create_index("ix_llm_usage_purpose_time", "llm_usage_event",
                    ["purpose", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_purpose_time", table_name="llm_usage_event")
    op.drop_index("ix_llm_usage_student_time", table_name="llm_usage_event")
    op.drop_table("llm_usage_event")
