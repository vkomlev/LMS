"""tsk-301 Фаза 1: схема подписной модели и досев тарифной сетки.

Четыре таблицы (`subscription_plan`, `student_subscription`, `student_ai_quota`,
`student_ai_grant`) и сид девяти планов. Ни одна точка кода к ним ещё не
обращается — гейт появляется только в Фазе 2 и включается в Фазе 6. Поэтому
миграция поведения не меняет вообще.

**Что досеивается в тарифную сетку и почему это безопасно.** Проверено на проде
2026-08-08 (`/db-check` до миграции): максимум занятий в неделю у действующих
учеников — **два** (23 человека по 2, 10 по 1, 18 без расписания). Значит
добавление ступени «3 раза в неделю» не сдвигает ничью цену: ни через точное
совпадение (таких нет), ни через `fallback_lower` (он берёт ближайшую МЕНЬШУЮ
ступень, а новая — больше всех существующих). Новые группы (Self, AI, Базовый
2026) на существующих учеников не влияют вовсе: на них никто не ссылается, пока
Фаза 2 не научит расчёт брать группу из подписки.

**Существующая группа «Базовый» (id=1) не переименовывается и не трогается** —
на ней 37 живых начислений. В новой сетке она играет роль «Base (старая цена)»,
новые клиенты пойдут в отдельную группу «Базовый 2026».

Сид идемпотентен и ищет группы **по имени**, а не по жёстко вписанному id:
на чистой dev-базе групп может не быть вовсе, и такой сид отработает и там.

Rollback: `alembic downgrade tsk578_task_opened_idx` — четыре таблицы удаляются
вместе с присвоенными тарифами. Данные на момент отката справочные (планы) либо
ещё не используемые (подписки появятся в Фазе 5), ручной труд в них не хранится.

Откат по сетке **намеренно несимметричен досеву**: удаляются только заведомо
новые группы («Базовый 2026», «Self», «AI») и ступень «3 раза в неделю» в
«Базовом». Строки, существующие на проде до миграции, не трогаются никогда —
даже если на dev их создал наш же досев: там это выравнивание с продом, и снос
вернул бы расхождение. Группа, на которую успели повесить курс, тоже остаётся:
это уже рабочая настройка, а не наша строка.

Revision ID: tsk301_subscription
Revises: tsk578_task_opened_idx
Create Date: 2026-08-08
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "tsk301_subscription"
down_revision: Union[str, None] = "tsk578_task_opened_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: ПОЛНЫЙ ожидаемый состав сетки: (имя группы, описание,
#: [(имя варианта, ₽, ось, значение, порядок)]).
#:
#: Объявляется целиком, а не «чего не хватает на проде»: dev и прод разошлись
#: (на dev нет группы «Обучение взрослых» вовсе), и сид, написанный от прод-снимка,
#: создал бы там пустую группу. Существующие строки ищутся ПО ИМЕНИ и не трогаются —
#: досев только добавляет недостающее и тем сводит базы к одному состоянию.
_GRID: tuple[tuple[str, str, tuple[tuple[str, int, str | None, str | None, int], ...]], ...] = (
    (
        "Базовый",
        "Старая цена для давних клиентов (tsk-301: план base_legacy)",
        (
            ("1 раз в неделю", 2750, "attendance_frequency", "1", 1),
            ("2 раза в неделю", 5500, "attendance_frequency", "2", 2),
            ("3 раза в неделю", 7750, "attendance_frequency", "3", 3),
        ),
    ),
    (
        "Базовый 2026",
        "Цена для новых клиентов (tsk-301: план base)",
        (
            ("1 раз в неделю", 3000, "attendance_frequency", "1", 1),
            ("2 раза в неделю", 6000, "attendance_frequency", "2", 2),
            ("3 раза в неделю", 9000, "attendance_frequency", "3", 3),
        ),
    ),
    (
        "Обучение взрослых",
        "Взрослая группа (tsk-301: план adults)",
        (
            ("1 раз в неделю", 3500, "attendance_frequency", "1", 1),
            ("2 раза в неделю", 7000, "attendance_frequency", "2", 2),
        ),
    ),
    (
        "ИИ-предприниматель",
        "Флагман (tsk-301: план flagship)",
        (
            ("Для своих", 10000, "segment", "insider", 1),
            ("Улица", 20000, "segment", "street", 2),
        ),
    ),
    (
        "Self",
        "Самостоятельное прохождение без занятий и без ИИ (tsk-301)",
        (("Самостоятельно", 1000, None, None, 1),),
    ),
    (
        "AI",
        "Самостоятельное прохождение с ИИ-наставником (tsk-301)",
        (("С наставником", 1500, None, None, 1),),
    ),
)

#: Что откат удаляет. **Только заведомо наше.** Группы и ступени, живущие на
#: проде до этой миграции («Базовый» 1-2 раза, «ИИ-предприниматель», «Обучение
#: взрослых»), не удаляются никогда — даже если на dev их создал наш же досев:
#: там это выравнивание с продом, и снос вернул бы расхождение.
_DOWNGRADE_GROUPS: tuple[str, ...] = ("Базовый 2026", "Self", "AI")
_DOWNGRADE_TARIFFS: tuple[tuple[str, str], ...] = (("Базовый", "3 раза в неделю"),)

#: Девять планов из матрицы контракта §2.
#: (code, name, ai_tutor_limit, code_review, teacher_escalation, lessons, content,
#:  имя тарифной группы или None, подсказка апгрейда, порядок)
_PLANS: tuple[
    tuple[str, str, int | None, bool, bool, bool, str, str | None, str | None, int], ...
] = (
    ("test", "Test", None, True, True, False, "full", None,
     None, 1),
    ("demo", "Demo", 0, False, False, False, "demo", None,
     "Полный доступ к курсу, без лимита заданий — от 1000 ₽ в месяц", 2),
    ("self", "Self", 0, False, False, False, "full", "Self",
     "ИИ-наставник и проверка кода — на тарифе AI, 1500 ₽ в месяц", 3),
    ("ai", "AI", 40, True, False, False, "full", "AI",
     "Занятия с преподавателем и разбор заданий — на тарифе Base", 4),
    ("base", "Base", 100, True, True, True, "full", "Базовый 2026",
     None, 5),
    ("base_legacy", "Base (старая цена)", 100, True, True, True, "full", "Базовый",
     None, 6),
    ("adults", "Обучение взрослых", 100, True, True, True, "full", "Обучение взрослых",
     None, 7),
    ("flagship", "Флагман", None, True, True, True, "full", "ИИ-предприниматель",
     None, 8),
    ("alumni", "Выпускник", 0, False, False, False, "full", None,
     "Вернуться к занятиям и наставнику — на любом тарифе с обучением", 9),
)


def _seed_grid(conn: sa.engine.Connection) -> None:
    """Досеять группы и недостающие ступени. Существующие строки не трогаются."""
    for group_name, description, tariffs in _GRID:
        group_id = conn.execute(
            sa.text("SELECT id FROM pricing_group WHERE name = :n"), {"n": group_name}
        ).scalar()
        if group_id is None:
            group_id = conn.execute(
                sa.text(
                    "INSERT INTO pricing_group (name, description) "
                    "VALUES (:n, :d) RETURNING id"
                ),
                {"n": group_name, "d": description},
            ).scalar()
        for name, rub, kind, value, order in tariffs:
            # Ступень уже есть (например, при повторном прогоне) — пропускаем.
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM pricing_tariff "
                    " WHERE group_id = :g AND name = :n"
                ),
                {"g": group_id, "n": name},
            ).scalar()
            if exists:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO pricing_tariff "
                    "  (group_id, name, price_minor, match_kind, match_value, sort_order) "
                    "VALUES (:g, :n, :p, :k, :v, :o)"
                ),
                {"g": group_id, "n": name, "p": rub * 100,
                 "k": kind, "v": value, "o": order},
            )


def _seed_plans(conn: sa.engine.Connection) -> None:
    """Засеять девять планов, связав их с группами по имени."""
    for (code, name, limit, review, escalation, lessons, content,
         group_name, hint, order) in _PLANS:
        group_id = None
        if group_name is not None:
            group_id = conn.execute(
                sa.text("SELECT id FROM pricing_group WHERE name = :n"),
                {"n": group_name},
            ).scalar()
            if group_id is None:
                # План засеивается без денежной привязки, чтобы миграция не падала
                # на нестандартной базе. Молчать нельзя: `pricing_group_id IS NULL`
                # — валидное состояние для Test/Demo/Выпускника, и без предупреждения
                # опечатка в имени группы неотличима от замысла.
                logger.warning(
                    "tsk-301: группа «%s» не найдена — план %s остался без денежной "
                    "привязки", group_name, code,
                )
        conn.execute(
            sa.text(
                "INSERT INTO subscription_plan "
                "  (code, name, ai_tutor_limit, code_review, teacher_escalation, "
                "   lessons, content, pricing_group_id, upgrade_hint, sort_order) "
                "VALUES (:c, :n, :l, :r, :e, :s, :ct, :g, :h, :o) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"c": code, "n": name, "l": limit, "r": review, "e": escalation,
             "s": lessons, "ct": content, "g": group_id, "h": hint, "o": order},
        )


def upgrade() -> None:
    op.create_table(
        "subscription_plan",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.Text(), nullable=False,
                  comment="Машинный код плана: test | demo | self | ai | …"),
        sa.Column("name", sa.Text(), nullable=False, comment="Имя для человека"),
        sa.Column("ai_tutor_limit", sa.Integer(), nullable=True,
                  comment="NULL — безлимит, 0 — нет доступа, N — N обращений в месяц"),
        sa.Column("code_review", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("teacher_escalation", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("lessons", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("content", sa.Text(), server_default=sa.text("'full'"),
                  nullable=False),
        sa.Column("pricing_group_id", sa.Integer(), nullable=True,
                  comment="Группа для расчёта месяца. NULL = начисления не создаются"),
        sa.Column("upgrade_hint", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"),
                  nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="subscription_plan_pkey"),
        sa.UniqueConstraint("code", name="uq_subscription_plan_code"),
        sa.ForeignKeyConstraint(["pricing_group_id"], ["pricing_group.id"],
                                ondelete="RESTRICT",
                                name="subscription_plan_pricing_group_id_fkey"),
        sa.CheckConstraint("ai_tutor_limit IS NULL OR ai_tutor_limit >= 0",
                           name="ck_subscription_plan_ai_limit_non_negative"),
        sa.CheckConstraint("content IN ('full', 'demo')",
                           name="ck_subscription_plan_content"),
        # Непустая строка в PG — `~ '\S'`. `length(btrim(x)) > 0` пропускает
        # табуляцию и перевод строки (урок tsk-303).
        sa.CheckConstraint("code ~ '\\S'", name="ck_subscription_plan_code_not_blank"),
        sa.CheckConstraint("name ~ '\\S'", name="ck_subscription_plan_name_not_blank"),
        comment="Тарифы как наборы прав; цена — в тарифной группе (tsk-301)",
    )

    op.create_table(
        "student_subscription",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("pricing_group_id", sa.Integer(), nullable=True,
                  comment="Перекрывает группу курса. NULL = начислений нет"),
        sa.Column("starts_on", sa.Date(), server_default=sa.text("CURRENT_DATE"),
                  nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True,
                  comment="NULL — строка действующая"),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="student_subscription_pkey"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE",
                                name="student_subscription_student_id_fkey"),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plan.id"],
                                ondelete="RESTRICT",
                                name="student_subscription_plan_id_fkey"),
        sa.ForeignKeyConstraint(["pricing_group_id"], ["pricing_group.id"],
                                ondelete="RESTRICT",
                                name="student_subscription_pricing_group_id_fkey"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL",
                                name="student_subscription_changed_by_fkey"),
        sa.CheckConstraint("ends_on IS NULL OR ends_on >= starts_on",
                           name="ck_student_subscription_period_order"),
        comment="Действующий и прошлые тарифы ученика (tsk-301)",
    )
    # Инвариант «не более одной действующей подписки на ученика» держит БД:
    # код проверял бы его гонкой между SELECT и INSERT.
    op.create_index(
        "uq_student_subscription_active", "student_subscription", ["student_id"],
        unique=True, postgresql_where=sa.text("ends_on IS NULL"),
    )
    op.create_index(
        "ix_student_subscription_student", "student_subscription",
        ["student_id", "starts_on"],
    )

    op.create_table(
        "student_ai_quota",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False, comment="Первое число месяца"),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="student_ai_quota_pkey"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE",
                                name="student_ai_quota_student_id_fkey"),
        sa.UniqueConstraint("student_id", "period", name="uq_student_ai_quota_period"),
        sa.CheckConstraint("used >= 0", name="ck_student_ai_quota_used_non_negative"),
        comment="Расход месячной квоты ИИ-наставника (tsk-301)",
    )

    op.create_table(
        "student_ai_grant",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("granted", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("purchased_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False,
                  comment="Порядок списания — по возрастанию этой даты (FIFO)"),
        sa.Column("gateway_payment_id", sa.Text(), nullable=True,
                  comment="Номер платежа ЮKassa. NULL — выдан персоналом вручную"),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="student_ai_grant_pkey"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE",
                                name="student_ai_grant_student_id_fkey"),
        sa.UniqueConstraint("gateway_payment_id",
                            name="uq_student_ai_grant_gateway_payment"),
        sa.CheckConstraint("granted > 0", name="ck_student_ai_grant_granted_positive"),
        sa.CheckConstraint("used >= 0 AND used <= granted",
                           name="ck_student_ai_grant_used_in_range"),
        comment="Докупленные пакеты обращений к наставнику (tsk-301)",
    )
    op.create_index(
        "ix_student_ai_grant_fifo", "student_ai_grant", ["student_id", "purchased_at"]
    )

    conn = op.get_bind()
    _seed_grid(conn)
    _seed_plans(conn)


def downgrade() -> None:
    conn = op.get_bind()

    # Таблицы сносятся раньше зачистки сетки: и `subscription_plan`, и
    # `student_subscription` ссылаются на `pricing_group` через RESTRICT.
    op.drop_index("ix_student_ai_grant_fifo", table_name="student_ai_grant")
    op.drop_table("student_ai_grant")
    op.drop_table("student_ai_quota")
    op.drop_index("ix_student_subscription_student", table_name="student_subscription")
    op.drop_index("uq_student_subscription_active", table_name="student_subscription")
    op.drop_table("student_subscription")
    op.drop_table("subscription_plan")

    # Точечные ступени, добавленные в уже существовавшие группы.
    for group_name, tariff_name in _DOWNGRADE_TARIFFS:
        conn.execute(
            sa.text(
                "DELETE FROM pricing_tariff t "
                " USING pricing_group g "
                " WHERE g.id = t.group_id AND g.name = :g AND t.name = :n"
            ),
            {"g": group_name, "n": tariff_name},
        )

    # Целиком новые группы — вместе с их вариантами. Группа, на которую успели
    # повесить курс, не удаляется: это уже не наша строка, а рабочая настройка.
    for group_name in _DOWNGRADE_GROUPS:
        conn.execute(
            sa.text(
                "DELETE FROM pricing_tariff t "
                " USING pricing_group g "
                " WHERE g.id = t.group_id AND g.name = :g "
                "   AND NOT EXISTS (SELECT 1 FROM course_pricing c WHERE c.group_id = g.id)"
            ),
            {"g": group_name},
        )
    conn.execute(
        sa.text(
            "DELETE FROM pricing_group pg "
            " WHERE pg.name = ANY(:names) "
            "   AND NOT EXISTS (SELECT 1 FROM pricing_tariff t WHERE t.group_id = pg.id) "
            "   AND NOT EXISTS (SELECT 1 FROM course_pricing c WHERE c.group_id = pg.id)"
        ),
        {"names": list(_DOWNGRADE_GROUPS)},
    )
