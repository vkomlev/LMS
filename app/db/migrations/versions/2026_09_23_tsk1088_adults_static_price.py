"""tsk-1088: тариф взрослых — статичная цена 8000 ₽/мес, старая сетка за legacy-планом.

Оператор 23.09: «Обучение взрослых» отвязываем от периодичности, цена 8000 ₽ в
месяц. Старую сетку (3500/7000 по частоте) сохраняем — на ней будет работать
один ученик. Образец раздвоения — tsk-301 (`base` → «Базовый 2026»,
`base_legacy` → «Базовый»).

Что делает:
- группа «Обучение взрослых 2026» с ОДНОЙ ступенью без оси (`match_kind IS NULL`)
  — движок берёт её как единственную (`pricing_service._resolve_group_price`),
  так же устроены Self/AI;
- план `adults` → новая группа. Уже открытые подписки держат СВОЮ копию
  `pricing_group_id` и не меняются (на проде активных `adults` нет);
- новый план `adults_legacy` «Обучение взрослых (старая цена)» → старая группа,
  её ступени не трогаются;
- курсы, продающиеся по старой группе (`course_pricing`), переводятся на новую:
  отсюда публичная цена лендингов (tsk-1070). Начисления ученикам с подпиской
  от этого не меняются — группа подписки главнее курса.

Все строки ищутся ПО ИМЕНИ/КОДУ, не по id: dev и прод расходятся в id.

Rollback: `alembic downgrade tsk1042_absence_followup_reasons` — вернёт план и
курсы на старую группу, удалит `adults_legacy` (если на нём нет подписок) и
новую группу (если на неё никто не ссылается).

Revision ID: tsk1088_adults_static_price
Revises: tsk1042_absence_followup_reasons
Create Date: 2026-09-23
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1088_adults_static_price"
down_revision: Union[str, None] = "tsk1042_absence_followup_reasons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_OLD_GROUP = "Обучение взрослых"
_NEW_GROUP = "Обучение взрослых 2026"
_NEW_GROUP_DESCRIPTION = "Статичная цена для взрослых (tsk-1088: план adults)"
_NEW_TARIFF = "В месяц"
_NEW_PRICE_MINOR = 800_000
_LEGACY_CODE = "adults_legacy"
_LEGACY_NAME = "Обучение взрослых (старая цена)"


def _group_id(conn: sa.engine.Connection, name: str) -> int | None:
    """id тарифной группы по имени или None."""
    return conn.execute(
        sa.text("SELECT id FROM pricing_group WHERE name = :n"), {"n": name}
    ).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    old_id = _group_id(conn, _OLD_GROUP)
    if old_id is None:
        # Без старой группы нечего раздваивать; молчать нельзя — иначе план
        # adults_legacy повис бы без цены незаметно.
        logger.warning("tsk-1088: группы «%s» нет — миграция ничего не меняет", _OLD_GROUP)
        return

    new_id = _group_id(conn, _NEW_GROUP)
    if new_id is None:
        new_id = conn.execute(
            sa.text(
                "INSERT INTO pricing_group (name, description) VALUES (:n, :d) RETURNING id"
            ),
            {"n": _NEW_GROUP, "d": _NEW_GROUP_DESCRIPTION},
        ).scalar()
    has_tariff = conn.execute(
        sa.text("SELECT 1 FROM pricing_tariff WHERE group_id = :g AND name = :n"),
        {"g": new_id, "n": _NEW_TARIFF},
    ).scalar()
    if not has_tariff:
        conn.execute(
            sa.text(
                "INSERT INTO pricing_tariff "
                "  (group_id, name, price_minor, match_kind, match_value, sort_order) "
                "VALUES (:g, :n, :p, NULL, NULL, 1)"
            ),
            {"g": new_id, "n": _NEW_TARIFF, "p": _NEW_PRICE_MINOR},
        )

    # Legacy-план — копия прав `adults`, но со старой группой.
    conn.execute(
        sa.text(
            "INSERT INTO subscription_plan "
            "  (code, name, ai_tutor_limit, code_review, teacher_escalation, "
            "   lessons, content, pricing_group_id, upgrade_hint, sort_order) "
            "SELECT :c, :n, ai_tutor_limit, code_review, teacher_escalation, "
            "       lessons, content, :g, upgrade_hint, 10 "
            "  FROM subscription_plan WHERE code = 'adults' "
            "ON CONFLICT (code) DO NOTHING"
        ),
        {"c": _LEGACY_CODE, "n": _LEGACY_NAME, "g": old_id},
    )
    conn.execute(
        sa.text("UPDATE subscription_plan SET pricing_group_id = :new WHERE code = 'adults'"),
        {"new": new_id},
    )
    conn.execute(
        sa.text("UPDATE course_pricing SET group_id = :new WHERE group_id = :old"),
        {"new": new_id, "old": old_id},
    )


def downgrade() -> None:
    conn = op.get_bind()
    old_id = _group_id(conn, _OLD_GROUP)
    new_id = _group_id(conn, _NEW_GROUP)
    if old_id is None or new_id is None:
        return
    conn.execute(
        sa.text("UPDATE course_pricing SET group_id = :old WHERE group_id = :new"),
        {"new": new_id, "old": old_id},
    )
    conn.execute(
        sa.text("UPDATE subscription_plan SET pricing_group_id = :old WHERE code = 'adults'"),
        {"old": old_id},
    )
    conn.execute(
        sa.text(
            "DELETE FROM subscription_plan p WHERE p.code = :c "
            "   AND NOT EXISTS (SELECT 1 FROM student_subscription s WHERE s.plan_id = p.id)"
        ),
        {"c": _LEGACY_CODE},
    )
    # Группу сносим, только если на неё больше никто не ссылается (подписки,
    # начисления, корректировки держат её через RESTRICT).
    conn.execute(
        sa.text(
            "DELETE FROM pricing_group g WHERE g.id = :g "
            "   AND NOT EXISTS (SELECT 1 FROM subscription_plan x WHERE x.pricing_group_id = g.id) "
            "   AND NOT EXISTS (SELECT 1 FROM student_subscription x WHERE x.pricing_group_id = g.id) "
            "   AND NOT EXISTS (SELECT 1 FROM student_monthly_charge x WHERE x.group_id = g.id) "
            "   AND NOT EXISTS (SELECT 1 FROM charge_adjustment x WHERE x.group_id = g.id) "
            "   AND NOT EXISTS (SELECT 1 FROM course_pricing x WHERE x.group_id = g.id)"
        ),
        {"g": new_id},
    )
