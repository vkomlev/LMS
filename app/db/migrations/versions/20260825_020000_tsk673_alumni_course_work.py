"""tsk-673: признак «тариф даёт работать в курсе».

Перевод на «Выпускника» должен закрывать сдачу заданий, оставляя материалы на
чтение (решение оператора 2026-08-25). Существующими полями это не выражается:
`content` отвечает только за уровень материалов ('full' | 'demo') и для вошедшего
ученика ни на что не влияет, а `lessons` — признак для расписания и денег, и он
false у Self и AI, где сдавать задания как раз можно.

Почему признак в данных, а не набором кодов в коде — тот же довод, что и у
`billing_exempt` (tsk-610): следующий тариф-архив не должен требовать релиза, а
проверка `code = 'alumni'` в сервисе и есть та самая копия правила, которая
разъезжается с справочником при первом же новом тарифе.

Значение по умолчанию — true: тариф разрешает работать, пока явно не сказано
обратное. Обратная сторона (по умолчанию запрещено) закрыла бы задания всем, у
кого тариф ещё не размечен, — то есть всей школе на время выката.

Rollback: `alembic downgrade tsk674_schedule_preference` — колонка снимается,
гейт сдачи перестаёт находить признак и пропускает всех (см.
`graduation_service.assert_course_work_allowed`: отсутствие колонки роняет
запрос, поэтому откат схемы делается вместе с откатом кода).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk673_alumni_course_work"
down_revision: Union[str, None] = "tsk674_schedule_preference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription_plan",
        sa.Column(
            "course_work",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment=(
                "Можно ли работать в курсе: начинать попытку и отправлять ответы "
                "(tsk-673). Материалы этим признаком НЕ закрываются — выпускник "
                "перечитывает курс, но новых ответов у него не принимают"
            ),
        ),
    )
    op.execute("UPDATE subscription_plan SET course_work = false WHERE code = 'alumni'")


def downgrade() -> None:
    op.drop_column("subscription_plan", "course_work")
