"""tsk-1147: способ помощи у ответа на заявку (`help_request_replies.reply_kind`).

Решение оператора 28.09: у ответа поле типа — text / voice / video / telemost,
система предвыбирает, преподаватель правит. Прошлые ответы размечаются той же
догадкой, что применяет сервер (`app/services/help_reply_kind.py`): ссылка
Телемоста → telemost, ссылка на видео → video, иначе text. Шаблоны ниже —
перевод регулярок модуля на диалект PostgreSQL; менять вместе.

`voice` задним числом не ставится: «разобрал устно и закрыл без текста» строки
ответа не оставляет вовсе и выводится при подсчёте из закрытия без ответа.

Rollback: `alembic downgrade tsk1139_quiz_funnel_spec` — колонка снимается,
выбранные преподавателями типы теряются (текст ответов не трогается).

Revision ID: tsk1147_reply_kind
Revises: tsk1139_quiz_funnel_spec
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1147_reply_kind"
down_revision: Union[str, None] = "tsk1139_quiz_funnel_spec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TELEMOST = r"telemost\.(yandex|360\.yandex)\."
_VIDEO = (
    r"(youtube\.com/|youtu\.be/|rutube\.ru/|vk\.com/video|vkvideo\.ru/"
    r"|disk\.yandex\.[a-z]+/|yadi\.sk/|\.mp4\M|\.mov\M|\.webm\M)"
)


def upgrade() -> None:
    """Колонка с DEFAULT 'text', CHECK и разметка истории догадкой."""
    op.add_column(
        "help_request_replies",
        sa.Column(
            "reply_kind",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'text'"),
            comment="Способ помощи: text | voice | video | telemost (tsk-1147)",
        ),
    )
    op.create_check_constraint(
        "help_request_replies_reply_kind_check",
        "help_request_replies",
        "reply_kind IN ('text', 'voice', 'video', 'telemost')",
    )
    op.execute(
        sa.text(
            "UPDATE help_request_replies SET reply_kind = CASE "
            " WHEN body ~* :telemost THEN 'telemost' "
            " WHEN body ~* :video THEN 'video' "
            " ELSE 'text' END "
            "WHERE reply_kind = 'text'"
        ).bindparams(telemost=_TELEMOST, video=_VIDEO)
    )


def downgrade() -> None:
    """Снять колонку вместе с ограничением."""
    op.drop_constraint(
        "help_request_replies_reply_kind_check", "help_request_replies", type_="check"
    )
    op.drop_column("help_request_replies", "reply_kind")
