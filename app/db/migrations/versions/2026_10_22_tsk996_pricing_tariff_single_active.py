"""tsk-996: один действующий вариант тарифа без оси на группу.

`uq_pricing_tariff_axis_point` (tsk-505) — уникальный `(group_id, match_kind,
match_value) WHERE is_active`. Вариант без оси (`match_kind IS NULL`, тогда и
`match_value IS NULL`) им не ловится: NULL с NULL в уникальном индексе не
конфликтуют. Два таких варианта в группе задваивали план на витрине
`GET /payments/plans` (`purchasable_plans`), а при оплате выигрывал самый
дорогой. Аналог для NULL — частичный уникальный индекс по `group_id`.

Перед созданием индекса — проверка: если дубли уже есть, миграция падает с
понятным списком групп, а не чистит данные молча (какой вариант оставить,
решает маркетолог). На проде 22.10 дублей не было.

Данных не меняет. Rollback: `alembic downgrade tsk1398_messenger_link_tg` —
индекс удаляется.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk996_tariff_single_active"
down_revision: Union[str, None] = "tsk1398_messenger_link_tg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_pricing_tariff_single_active"


def upgrade() -> None:
    """Проверить отсутствие дублей и создать индекс."""
    dupes = op.get_bind().execute(
        sa.text(
            "SELECT group_id, array_agg(id ORDER BY id) AS ids "
            "FROM pricing_tariff WHERE is_active AND match_kind IS NULL "
            "GROUP BY group_id HAVING count(*) > 1 ORDER BY group_id"
        )
    ).all()
    if dupes:
        listing = "; ".join(f"группа {r.group_id}: варианты {list(r.ids)}" for r in dupes)
        raise RuntimeError(
            "tsk-996: в тарифных группах больше одного действующего варианта без оси "
            f"({listing}). Оставьте по одному (лишние — is_active = false) и повторите "
            "миграцию."
        )
    op.create_index(
        _INDEX,
        "pricing_tariff",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND match_kind IS NULL"),
    )


def downgrade() -> None:
    """Снять индекс."""
    op.drop_index(_INDEX, table_name="pricing_tariff")
