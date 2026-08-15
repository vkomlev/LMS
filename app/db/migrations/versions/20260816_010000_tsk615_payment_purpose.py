"""tsk-615: платёж не только за месяц — назначение платежа полем.

16.08.2026 прошла первая живая покупка пакета обращений к наставнику (500 ₽,
tsk-301 Фаза 8). Деньги пришли, пакет зачислен, а в учёте платежей их не
оказалось: `student_payment` ссылается на строку помесячного начисления
внешним ключом `(student_id, group_id, period)`, а пакет к месяцу не привязан —
он бессрочный и переносится. Положить такой платёж в таблицу было физически
нельзя, и выручка от него не попадала ни в кабинет ученика, ни в выгрузку для
сверки с ЮKassa.

Решение оператора (2026-08-16): деньги остаются в ОДНОЙ таблице, а различает
их назначение — `purpose`. Месяц и тарифная группа становятся необязательными:
у разовой покупки их нет и быть не может.

**Защита месячных платежей при этом не слабеет.** Составной внешний ключ в
PostgreSQL по умолчанию `MATCH SIMPLE`: если хотя бы одна колонка ключа пуста,
строка проверке не подлежит. То есть разовые покупки проходят мимо ссылки на
начисление, а месячные проверяются ровно как раньше — включая `ON DELETE
RESTRICT`, из-за которого пересчёт не может удалить месяц с принятыми деньгами.
Чтобы «мимо ключа» не стало лазейкой для месячного платежа, пара держится
жёстко: `purpose = 'monthly'` ⟺ месяц и группа заполнены.

Почему список назначений — в CHECK, а не свободной строкой: опечатка в коде
(`ai_pakage`) иначе тихо создала бы третий класс денег, который не показывается
нигде и обнаружился бы расхождением сверки. Новая разовая продажа = миграция на
одну строку — это осознанная плата за то, чтобы деньги нельзя было потерять
опиской.

Rollback: `alembic downgrade tsk610_billing_exempt`. Откат ОСОЗНАННО падает,
если в таблице уже есть разовые покупки: вернуть `NOT NULL` можно только
удалив их, а это удаление денег. Сначала выгрузить такие строки
(`SELECT * FROM student_payment WHERE purpose <> 'monthly'`) и решить, куда их
деть, потом откатывать.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk615_payment_purpose"
down_revision: Union[str, None] = "tsk610_billing_exempt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Назначения платежа. `monthly` — оплата месяца обучения (весь прежний учёт),
#: `ai_package` — разовая покупка пакета обращений к наставнику.
_PURPOSES_SQL = "purpose IN ('monthly', 'ai_package')"


def upgrade() -> None:
    op.add_column(
        "student_payment",
        sa.Column(
            "purpose",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'monthly'"),
            comment=(
                "За что платили: monthly — месяц обучения, ai_package — разовый "
                "пакет обращений к наставнику (tsk-615)"
            ),
        ),
    )
    # Все восемь существующих строк — оплата месяца: другого пути в таблицу до
    # сегодняшнего дня не было. Значение по умолчанию проставило это само.
    op.alter_column("student_payment", "group_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("student_payment", "period", existing_type=sa.Date(), nullable=True)

    op.create_check_constraint(
        "ck_student_payment_purpose",
        "student_payment",
        _PURPOSES_SQL,
    )
    # Ровно тот инвариант, который заменяет утраченный NOT NULL: месячный платёж
    # обязан указывать месяц и группу (и тогда его проверит внешний ключ на
    # начисление), разовый — обязан не указывать НИ ОДНОГО из двух.
    #
    # Условие записано двумя полными ветвями, а не равенством
    # `(purpose = 'monthly') = (group_id IS NOT NULL AND period IS NOT NULL)`:
    # то равенство пропускало разовую покупку с заполненным месяцем, но пустой
    # группой. Внешний ключ такую строку тоже пропускает (при частично пустом
    # ключе `MATCH SIMPLE` не проверяет ничего), и в таблице появился бы месяц,
    # не подтверждённый ни одним начислением.
    op.create_check_constraint(
        "ck_student_payment_purpose_charge_link",
        "student_payment",
        "(purpose =  'monthly' AND group_id IS NOT NULL AND period IS NOT NULL) OR "
        "(purpose <> 'monthly' AND group_id IS NULL     AND period IS NULL)",
    )
    # Разовые покупки ученика — отдельный экран в кабинете, отдельный запрос.
    op.create_index(
        "ix_student_payment_one_off",
        "student_payment",
        ["student_id", "created_at"],
        postgresql_where=sa.text("purpose <> 'monthly'"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    stuck = conn.execute(
        sa.text("SELECT count(*) FROM student_payment WHERE purpose <> 'monthly'")
    ).scalar_one()
    if stuck:
        raise RuntimeError(
            f"Откат остановлен: в student_payment есть разовых покупок: {stuck}. "
            "Вернуть NOT NULL на месяц можно только удалив их, а это удаление "
            "принятых денег. Выгрузите строки "
            "(SELECT * FROM student_payment WHERE purpose <> 'monthly') и решите, "
            "куда их деть, до отката."
        )
    op.drop_index("ix_student_payment_one_off", table_name="student_payment")
    op.drop_constraint("ck_student_payment_purpose_charge_link", "student_payment")
    op.drop_constraint("ck_student_payment_purpose", "student_payment")
    op.alter_column("student_payment", "period", existing_type=sa.Date(), nullable=False)
    op.alter_column("student_payment", "group_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("student_payment", "purpose")
