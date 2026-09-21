"""tsk-1022: новый статус ручного платежа — «сброшен».

Оператор 21.09: «Нужно иметь возможность сбросить ручную оплату в меню
начисления маркетолога». Отметить ручной платёж (`record_staff_payment`)
можно, а отменить было нечем: он сразу пишется `confirmed`, обратного пути
в коде не было.

Почему не переиспользовать `rejected`. Он уже занят другим смыслом — «чек не
прошёл проверку маркетолога» (`reject_payment`, вызывается из очереди
`pending`-платежей). Сброс своей же ручной отметки — другое действие с другим
поводом, и смешение исказило бы историю: тот же бейдж «Отклонён» на экране
означал бы то забракованный чек, то отменённую самим маркетологом запись.
`review_note` у сброса обязателен и объясняет причину, но статус должен быть
виден отдельно, а не восстановлен из текста примечания.

Условие `(status = 'pending') = (reviewed_at IS NULL)` новый статус не задевает:
`reversed` — не `pending`, и функция сброса пишет `reviewed_at = now()`, как и
у `confirmed`/`rejected`.

Rollback: `alembic downgrade tsk1006_homework_item_tier` — статус убирается из
CHECK. Если к тому моменту есть строки со статусом `reversed`, откат упрётся в
ограничение; такие строки надо перевести в `rejected` вручную до отката (тот
же тег «сброшено», просто без отдельного статуса).

Revision ID: tsk1022_manual_payment_reversed
Revises: tsk1006_homework_item_tier
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "tsk1022_manual_payment_reversed"
down_revision: Union[str, None] = "tsk1006_homework_item_tier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_student_payment_status", "student_payment", type_="check")
    op.create_check_constraint(
        "ck_student_payment_status",
        "student_payment",
        "status IN ('pending', 'confirmed', 'rejected', 'reversed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_student_payment_status", "student_payment", type_="check")
    op.create_check_constraint(
        "ck_student_payment_status",
        "student_payment",
        "status IN ('pending', 'confirmed', 'rejected')",
    )
