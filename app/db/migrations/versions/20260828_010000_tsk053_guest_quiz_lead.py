"""Лид-магнит-квиз в гостевом контуре: шкалы у гостевой попытки и связь заявки с квизом (tsk-053, фаза 1).

Три изменения, все аддитивные:

1. ``guest_attempt.scale_scores`` (JSONB, nullable) — баллы по шкалам квиз-ответа
   гостя. У авторизованного ученика такое поле уже есть (``task_results.scale_scores``,
   tsk-122/ADR-0003), а гостевая попытка хранила только ``is_correct``. Для
   квиз-вопроса ``is_correct`` бессмысленно (верного варианта нет), и без нового
   поля накопить шкалы по гостевой сессии нечем — то есть рекомендацию посчитать
   не из чего.

2. ``leads.guest_session_id`` / ``leads.quiz_course_id`` (nullable) — какая гостевая
   сессия и какой квиз привели заявку. Без них воронка «прошли квиз → оставили
   контакт» не считается: лид и прохождение лежали бы в базе как два несвязанных
   события. ON DELETE SET NULL у обоих: заявка не должна исчезать вслед за
   служебной сессией или снятым с публикации квизом — контакт человека ценнее
   привязки к источнику.

3. Канал привлечения ``quiz`` в справочнике ``lead_source``. Канал именно
   отдельный, а не ``website``: смысл задачи — узнать, сколько людей приводит
   квиз, а не сайт вообще.

Rollback-note: ``alembic downgrade tsk674_schedule_slot_request`` снимает оба поля
у ``leads`` (привязка заявок к квизу теряется безвозвратно — сами заявки остаются),
поле ``scale_scores`` у ``guest_attempt`` и строку канала ``quiz``. Лиды, заведённые
с этим каналом, откат бы осиротил, поэтому канал удаляется только если на него не
ссылается ни один лид; иначе он остаётся в справочнике выключенным (``is_active=false``).

Revision ID: tsk053_guest_quiz_lead
Revises: tsk674_schedule_slot_request
Create Date: 2026-08-28

Примечание о порядке: соседняя задача tsk-721 в тот же час создала миграцию от этой
же головы. Ветвление уронило бы выкат — `deploy/vps/deploy.sh` зовёт `alembic upgrade
head` в единственном числе. Сводить линию здесь нельзя: файл соседа ещё не в
репозитории, и ссылка на его ревизию сделала бы выкат невозможным вовсе («Can't
locate revision»). Поэтому опираемся на ревизию, которая есть и в репозитории, и на
проде; свести линию — ход того, кто коммитит вторым.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk053_guest_quiz_lead"
down_revision: Union[str, None] = "tsk674_schedule_slot_request"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guest_attempt",
        sa.Column(
            "scale_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Баллы по шкалам квиз-ответа гостя (SC_Qw/MC_Qw, tsk-053)",
        ),
    )

    op.add_column(
        "leads",
        sa.Column(
            "guest_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Гостевая сессия, из которой пришла заявка (tsk-053)",
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "quiz_course_id",
            sa.Integer(),
            nullable=True,
            comment="Курс-квиз, после которого оставлен контакт (tsk-053)",
        ),
    )
    op.create_foreign_key(
        "leads_guest_session_id_fkey",
        "leads",
        "guest_session",
        ["guest_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "leads_quiz_course_id_fkey",
        "leads",
        "courses",
        ["quiz_course_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Воронка считается «сколько заявок у этого квиза», то есть выборкой по
    # курсу-квизу; без индекса это seq scan по всем лидам.
    op.create_index(
        "ix_leads_quiz_course_id",
        "leads",
        ["quiz_course_id"],
        postgresql_where=sa.text("quiz_course_id IS NOT NULL"),
    )

    # Канал привлечения «квиз». sort_order 70 — после «Сайт» (60) и до «Другое» (100).
    op.execute(
        """
        INSERT INTO lead_source (code, name, is_active, sort_order)
        VALUES ('quiz', 'Квиз на сайте', true, 70)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    # Канал сносим только если на него никто не ссылается: иначе лид остался бы
    # без источника (FK RESTRICT всё равно не даст удалить). Занятый канал
    # выключаем — из справочника он пропадёт, у существующих лидов останется.
    op.execute(
        """
        UPDATE lead_source SET is_active = false
        WHERE code = 'quiz' AND EXISTS (SELECT 1 FROM leads WHERE source_id = lead_source.id)
        """
    )
    op.execute(
        """
        DELETE FROM lead_source
        WHERE code = 'quiz' AND NOT EXISTS (SELECT 1 FROM leads WHERE source_id = lead_source.id)
        """
    )

    op.drop_index("ix_leads_quiz_course_id", table_name="leads")
    op.drop_constraint("leads_quiz_course_id_fkey", "leads", type_="foreignkey")
    op.drop_constraint("leads_guest_session_id_fkey", "leads", type_="foreignkey")
    op.drop_column("leads", "quiz_course_id")
    op.drop_column("leads", "guest_session_id")

    op.drop_column("guest_attempt", "scale_scores")
