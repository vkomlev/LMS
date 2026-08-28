"""tsk-721: настройки школы, выбранные администратором в кабинете.

Пороги и правила работы школы жили в переменных окружения и в константах
кода: поменять «через сколько дней молчания звать преподавателя» мог только
тот, кто ходит на сервер, а часть значений нельзя было тронуть без выката.
Таблица `system_setting` — место, куда кабинет складывает выбор администратора.

Что важно про пустую таблицу. После накатки в ней НЕТ ни одной строки, и это
штатное состояние: пока администратор ничего не менял, каждое значение
берётся оттуда же, откуда бралось вчера — из переменной окружения или из
умолчания в коде. Поэтому миграция не меняет поведение системы ни на сервере,
ни на стенде: она только открывает место, где решение можно записать.

Строка появляется на первом сохранении и исчезает по кнопке «вернуть как
было». Именно удаление, а не запись прежнего числа: после удаления настройка
снова следует за окружением, как если бы её в кабинете никогда не трогали.

`value` — текст при любом типе настройки. Тип, границы и русское название
живут в реестре `app/core/settings_registry.py` рядом с кодом, который
настройку применяет; в базе лежит только выбранное значение.

Rollback: `alembic downgrade tsk674_schedule_slot_request`. Таблица снимается
вместе с сохранёнными значениями — школа возвращается к переменным окружения.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk721_system_setting"
down_revision: Union[str, None] = "tsk674_schedule_slot_request"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column(
            "key",
            sa.Text(),
            nullable=False,
            comment="Ключ настройки из реестра app/core/settings_registry.py",
        ),
        sa.Column(
            "value",
            sa.Text(),
            nullable=False,
            comment="Значение текстом; тип и границы проверяются по реестру",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда значение изменили в последний раз",
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            nullable=True,
            comment="Кто изменил. NULL — учётку удалили после правки",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_system_setting"),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_system_setting_updated_by_users",
            ondelete="SET NULL",
        ),
        comment=(
            "Настройки школы, выбранные администратором в кабинете (tsk-721). "
            "Пустая таблица — штатное состояние: значения берутся из окружения."
        ),
    )


def downgrade() -> None:
    op.drop_table("system_setting")
