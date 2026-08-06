"""tsk-302 этап 0: отдельное поле под машинную оценку работы ученика.

**Почему не `task_results.metrics`, где отчёт лежал изначально.** Два независимых
дефекта, найденных 2026-08-06:

1. `POST /task-results/{id}/manual-check` передаёт `metrics` в `TaskResultUpdate`
   явно, поэтому `model_dump(exclude_unset=True)` его не отбрасывает — первая же
   ручная проверка преподавателя ОБНУЛЯЕТ содержимое поля. Отчёт, который никто
   не восстановит, живёт до ближайшего клика преподавателя.
2. `metrics` уже несёт чужую семантику: на проде 13.8K записей с ключами
   `comment` (комментарий преподавателя), `manual_grant`, `escalated_at`,
   `completion_escalated_at`. Дописывать туда третью сущность — гарантированный
   спор за одно поле между ручной проверкой и машинной оценкой.

Отдельная колонка снимает оба вопроса разом: у ручной оценки своё поле, у
машинной — своё, и перезапись одной не задевает другую.

**Структура значения** (JSONB, форма задаётся приложением, не БД):
`{"code_quality": {...}, "ai_authorship": {...}, "timing": {...}}` — секции
необязательные и появляются по мере готовности источников. Схему намеренно не
фиксируем constraint'ом: состав сигналов будет меняться (сейчас pylint/radon по
Python, дальше — ИИ-оценка любого языка и время решения), а миграция ради
каждого нового ключа — цена без выгоды.

**Видимость.** Поле не показывается ученику: оно не входит ни в `CheckResult`,
который эхом уходит в ответ на сдачу, ни в какие ученические схемы. Читают его
преподаватель и методист (`ReviewClaimItem`) — решение оператора 2026-08-06.

Rollback: `alembic downgrade tsk303_feedback_reports` — колонка удаляется вместе
с накопленными оценками. Данные машинные и пересчитываемые, ручной труд
преподавателей в ней не хранится, поэтому потеря не критична.

Revision ID: tsk302_code_review
Revises: tsk303_feedback_reports
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk302_code_review"
down_revision: Union[str, None] = "tsk303_feedback_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_results",
        sa.Column(
            "code_review",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "tsk-302: машинная оценка работы (чистота кода, признак ИИ-авторства, "
                "время решения). Видна преподавателю и методисту, не ученику. "
                "Отдельно от metrics, которое несёт ручную проверку."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("task_results", "code_review")
