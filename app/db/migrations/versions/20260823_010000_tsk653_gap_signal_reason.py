"""tsk-653: у сигнала «нужно повторение» появляется причина.

Контекст. До сих пор сигнал был ровно один по смыслу — «ученик много ошибается»
(`learning_gap_signals_service.find_student_gaps`: не меньше 8 сдач, не меньше
50 % неверных). С tsk-646 появился второй повод: у работ ученика машинный
признак ИИ-авторства. Он про другое, и меряется другим — ошибок у такого
ученика может не быть вовсе. Живой проход 2026-08-23 (сигнал #66) показал это
буквально: карточка выехала методисту с бейджем «0% ошибок» в самом низу списка.

Почему колонка, а не поле в `meta`. По причине идёт не только показ, но и
УНИКАЛЬНОСТЬ: два частичных индекса не дают завести второй открытый сигнал по
той же паре «курс + ученик». Без причины в индексе сигнал об авторстве молча
подавился бы открытым сигналом об ошибках — и наоборот. Молча: `upsert_signal`
написан на `ON CONFLICT DO NOTHING`, то есть пропуск выглядит как штатная работа.

Умолчание `error_rate` намеренно: 27 существующих строк заводились единственным
тогда датчиком, и это правда о них, а не заглушка.

Числа причины (сколько работ разобрано, сколько с признаком) лежат в `meta` —
там им и место, они у каждой причины свои. `wrong_rate` остаётся ровно тем, чем
был: долей ОШИБОК. У сигнала об авторстве она честно нулевая, и переиспользовать
её под «долю работ с признаком» нельзя — это то самое поле, которое методист
читает первым.

Rollback: `alembic downgrade tsk644_slow_request` — колонка и индексы с причиной
удаляются, возвращаются прежние индексы без неё. Данные сигналов не теряются;
если к тому моменту существуют открытые сигналы обоих видов по одной паре
«курс + ученик», прежний индекс их не примет — такие строки надо будет закрыть
руками до отката.

Revision ID: tsk653_gap_signal_reason
Revises: tsk644_slow_request
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk653_gap_signal_reason"
down_revision: Union[str, None] = "tsk644_slow_request"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Статусы, при которых сигнал считается ОТКРЫТЫМ. Повторение того, что уже
#: зашито в прежние индексы: список меняется вместе с ними, а не отдельно.
_OPEN = "('new', 'acknowledged')"


def upgrade() -> None:
    # Новый статус `resolved` — «методист разобрал». До сих пор у эскалации не
    # было выхода вовсе: `dismiss` работает только из `new`/`acknowledged`, и
    # 5 сигналов висели в проде с 06.08 просто потому, что закрыть их было
    # нечем. Список статусов держит CHECK, и без правки здесь сервис падал бы
    # на записи — что и поймал тест.
    op.drop_constraint("ck_gap_signal_status", "learning_gap_signal", type_="check")
    op.create_check_constraint(
        "ck_gap_signal_status",
        "learning_gap_signal",
        "status IN ('new', 'acknowledged', 'escalated', 'dismissed', 'resolved')",
    )

    op.add_column(
        "learning_gap_signal",
        sa.Column(
            "reason",
            sa.String(length=32),
            nullable=False,
            server_default="error_rate",
            comment="Повод сигнала: error_rate — доля ошибок; ai_authorship — признак ИИ-авторства работ (tsk-646)",
        ),
    )

    # Индексы пересоздаются, а не дополняются: частичный уникальный индекс нельзя
    # расширить на месте, а держать оба варианта одновременно значит запретить
    # ровно то, ради чего правка и делается.
    op.drop_index("uq_gap_signal_open_topic", table_name="learning_gap_signal")
    op.drop_index("uq_gap_signal_open_student", table_name="learning_gap_signal")

    op.execute(
        "CREATE UNIQUE INDEX uq_gap_signal_open_topic "
        "ON learning_gap_signal (course_id, reason) "
        f"WHERE student_id IS NULL AND status IN {_OPEN}"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_gap_signal_open_student "
        "ON learning_gap_signal (course_id, student_id, reason) "
        f"WHERE student_id IS NOT NULL AND status IN {_OPEN}"
    )


def downgrade() -> None:
    op.drop_index("uq_gap_signal_open_student", table_name="learning_gap_signal")
    op.drop_index("uq_gap_signal_open_topic", table_name="learning_gap_signal")

    op.execute(
        "CREATE UNIQUE INDEX uq_gap_signal_open_topic "
        "ON learning_gap_signal (course_id) "
        f"WHERE student_id IS NULL AND status IN {_OPEN}"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_gap_signal_open_student "
        "ON learning_gap_signal (course_id, student_id) "
        f"WHERE student_id IS NOT NULL AND status IN {_OPEN}"
    )

    op.drop_column("learning_gap_signal", "reason")

    # Закрытые методистом сигналы вернуть в прежний список статусов нечем:
    # `resolved` в нём нет. Переводим их в `dismissed` — ближайший по смыслу
    # «закрыт человеком», чтобы откат не упёрся в CHECK и не потерял строки.
    op.execute(
        "UPDATE learning_gap_signal SET status = 'dismissed' WHERE status = 'resolved'"
    )
    op.drop_constraint("ck_gap_signal_status", "learning_gap_signal", type_="check")
    op.create_check_constraint(
        "ck_gap_signal_status",
        "learning_gap_signal",
        "status IN ('new', 'acknowledged', 'escalated', 'dismissed')",
    )
