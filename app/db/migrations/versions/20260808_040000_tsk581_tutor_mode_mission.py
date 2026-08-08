"""tsk-581: режим `mission` в CHECK-ограничении сессий ИИ-наставника.

**Живой прод-дефект, а не улучшение.** `prompt.pick_mode` с коммита `2f289b2`
возвращает режим `mission` для заданий-миссий, а ограничение
`ck_ai_tutor_session_mode` (миграция `tsk572_ai_tutor`) знало только
`concept | debug | deepen | thin`. Вставка сессии падала `CheckViolationError`,
и ученик получал 500 при открытии наставника на миссии
(`GET /api/v1/ai-tutor/tasks/6341`, курс 1095 — воспроизведено на проде
2026-08-08). Дефект не всплывал раньше только потому, что миссии есть в одном
курсе, где учеников мало.

**Ограничение пересоздаётся под тем же именем** (drop + add), а не заводится
второе рядом: два одноимённых ограничения PostgreSQL не даст, а разные имена
развели бы «настоящее» и «дополняющее» ограничение — прецедент
`help_requests_request_type_check` (tsk-303) решался так же.

**Про откат.** Старое ограничение не примет строки с `mission`, поэтому
`downgrade` сначала переводит их в `concept`. Это осознанная потеря точности:
после отката код всё равно не умеет `mission`, а падение отката на боевых
данных хуже, чем менее точная метка режима в семи исторических строках.

Revision ID: tsk581_tutor_mission
Revises: tsk301_subscription
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "tsk581_tutor_mission"
down_revision: Union[str, None] = "tsk301_subscription"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MODES_NEW = "'concept','debug','deepen','thin','mission'"
_MODES_OLD = "'concept','debug','deepen','thin'"

_COMMENT_NEW = (
    "concept | debug | deepen | thin | mission — режим методики, выбран по заданию"
)
_COMMENT_OLD = "concept | debug | deepen | thin — режим методики, выбран по заданию"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_tutor_session DROP CONSTRAINT IF EXISTS ck_ai_tutor_session_mode"
    )
    op.execute(
        "ALTER TABLE ai_tutor_session ADD CONSTRAINT ck_ai_tutor_session_mode "
        f"CHECK (mode IN ({_MODES_NEW}))"
    )
    op.execute(f"COMMENT ON COLUMN ai_tutor_session.mode IS '{_COMMENT_NEW}'")


def downgrade() -> None:
    # Строки с `mission` иначе не пройдут старое ограничение и уронят откат.
    op.execute("UPDATE ai_tutor_session SET mode = 'concept' WHERE mode = 'mission'")
    op.execute(
        "ALTER TABLE ai_tutor_session DROP CONSTRAINT IF EXISTS ck_ai_tutor_session_mode"
    )
    op.execute(
        "ALTER TABLE ai_tutor_session ADD CONSTRAINT ck_ai_tutor_session_mode "
        f"CHECK (mode IN ({_MODES_OLD}))"
    )
    op.execute(f"COMMENT ON COLUMN ai_tutor_session.mode IS '{_COMMENT_OLD}'")
