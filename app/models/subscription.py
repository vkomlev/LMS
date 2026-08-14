"""Подписка ученика: права, квота наставника, купленные пакеты (tsk-301).

**Несущий принцип, который нельзя «починить»: тариф даёт ПРАВА, расписание
порождает ДЕНЬГИ.** Подписка счётом не является. Строка `student_subscription`
указывает лишь, ПО КАКОЙ тарифной группе считать месяц (`pricing_group_id`);
сколько именно насчитать — по-прежнему решают занятия (`charge_service`).
`pricing_group_id IS NULL` означает «денег нет вовсе» — так устроены Test, Demo
и Выпускник.

Обычно подписка и есть счёт, поэтому соблазн добавить сюда цену велик. Данные
прода говорят обратное: у 15 из 52 активных учеников ноль занятий и ноль
начислений, при этом права на материалы они сохраняют (закончил обучение —
перестал платить, доступ остался). Подробности и отклонённые альтернативы —
[ADR-0006](../../docs/ai/adr/0006-subscription-rights-vs-money.md).

Контракт: `docs/specs/2026-08-08-contract-entitlements.md`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Уровни доступа к материалам. `demo` — курс с лимитом заданий (tsk-423).
CONTENT_LEVELS = ("full", "demo")

#: Коды девяти планов сетки. Порядок — как в матрице контракта §2.
PLAN_CODES = (
    "test", "demo", "self", "ai", "base", "base_legacy", "adults", "flagship", "alumni",
)


class SubscriptionPlan(Base):
    """Тариф как набор ПРАВ. Цены здесь нет — она живёт в тарифной группе.

    `ai_tutor_limit`: NULL — безлимит, 0 — наставник недоступен, N — N обращений
    в календарный месяц. Единица лимита — **обращение к модели (реплика)**, а не
    разговор целиком: именно так бриф считал экономику (40 × 2 194 токена, где
    2 194 — средний размер одного вызова), и только при поштучном счёте вообще
    возможна ситуация «лимит исчерпан посреди диалога», ради которой принято
    решение «дать закончить начатый разговор».
    """

    __tablename__ = "subscription_plan"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="subscription_plan_pkey"),
        UniqueConstraint("code", name="uq_subscription_plan_code"),
        ForeignKeyConstraint(
            ["pricing_group_id"], ["pricing_group.id"], ondelete="RESTRICT",
            name="subscription_plan_pricing_group_id_fkey",
        ),
        CheckConstraint(
            "ai_tutor_limit IS NULL OR ai_tutor_limit >= 0",
            name="ck_subscription_plan_ai_limit_non_negative",
        ),
        CheckConstraint(
            "content IN ('full', 'demo')", name="ck_subscription_plan_content",
        ),
        # «Непустая строка» в PG — это `~ '\\S'`, а не `length(btrim(x)) > 0`:
        # btrim без второго аргумента срезает только пробелы и пропускает
        # табуляцию с переводом строки (ошибка tsk-303 от 2026-08-06).
        CheckConstraint("code ~ '\\S'", name="ck_subscription_plan_code_not_blank"),
        CheckConstraint("name ~ '\\S'", name="ck_subscription_plan_name_not_blank"),
        {"comment": "Тарифы как наборы прав; цена — в тарифной группе (tsk-301)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Машинный код плана: test | demo | self | ai | …"
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, comment="Имя для человека")
    ai_tutor_limit: Mapped[Optional[int]] = mapped_column(
        Integer, comment="NULL — безлимит, 0 — нет доступа, N — N обращений в месяц"
    )
    code_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="ИИ-оценка кода. Без лимита: себестоимость ~6 ₽/ученик/мес (замер брифа)",
    )
    teacher_escalation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="Ручной запрос помощи преподавателю. Авто-заявка blocked_limit не гейтится",
    )
    lessons: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="Есть ли занятия. Признак для расписания и денег, доступ им не гейтится",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'full'"), comment="full | demo"
    )
    pricing_group_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="Группа для расчёта месяца. NULL = начисления не создаются вовсе",
    )
    billing_exempt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment=(
            "Денег не берут ОСОЗНАННО: страж «ходит, но не выставлен» молчит "
            "про таких (tsk-610). Не то же, что pricing_group_id IS NULL — "
            "у demo группы тоже нет, но ученик на нём как раз аномалия"
        ),
    )
    upgrade_hint: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Что даёт апгрейд — текст для заблокированной кнопки вместо «недоступно»",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class StudentSubscription(Base):
    """Присвоение тарифа ученику. История — строками, а не полем «предыдущий».

    Права берутся из строки, действующей СЕГОДНЯ; деньги — из строки,
    действовавшей на ПЕРВОЕ ЧИСЛО расчётного месяца. Из этой пары правил само
    собой выходит решение оператора «права при апгрейде сразу, деньги со
    следующего месяца» — отдельного механизма отсрочки не требуется.

    `pricing_group_id` — снимок на момент присвоения: он перекрывает группу
    курса (`course_pricing`). Курс остаётся источником по умолчанию для тех, у
    кого подписки ещё нет. Причина, по которой группы курса не хватает: «Базовый
    3000», «Базовый 2750», «Self 1000» и «AI 1500» продают ОДИН И ТОТ ЖЕ курс.
    """

    __tablename__ = "student_subscription"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="student_subscription_pkey"),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_subscription_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["plan_id"], ["subscription_plan.id"], ondelete="RESTRICT",
            name="student_subscription_plan_id_fkey",
        ),
        ForeignKeyConstraint(
            ["pricing_group_id"], ["pricing_group.id"], ondelete="RESTRICT",
            name="student_subscription_pricing_group_id_fkey",
        ),
        ForeignKeyConstraint(
            ["changed_by"], ["users.id"], ondelete="SET NULL",
            name="student_subscription_changed_by_fkey",
        ),
        CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on",
            name="ck_student_subscription_period_order",
        ),
        # Инвариант «не более одной действующей подписки» держит БД, а не код:
        # код проверял бы его гонкой между SELECT и INSERT.
        Index(
            "uq_student_subscription_active",
            "student_id",
            unique=True,
            postgresql_where=text("ends_on IS NULL"),
        ),
        Index("ix_student_subscription_student", "student_id", "starts_on"),
        {"comment": "Действующий и прошлые тарифы ученика (tsk-301)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_group_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Перекрывает группу курса. NULL = начислений нет"
    )
    starts_on: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )
    ends_on: Mapped[Optional[date]] = mapped_column(
        Date, comment="NULL — строка действующая"
    )
    changed_by: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Кто присвоил: admin или marketer (преподавателю нельзя)"
    )
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class StudentAiQuota(Base):
    """Расход месячной квоты наставника. Одна строка на ученика и месяц.

    Отдельная таблица, а не подсчёт по `llm_usage_event`: списание обязано быть
    атомарным (`UPDATE … WHERE used < limit RETURNING`), иначе две вкладки
    ученика съедают одну единицу дважды. По журналу расхода такое не выразить.

    Квота **не переносится** между месяцами — в отличие от купленного пакета.
    """

    __tablename__ = "student_ai_quota"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="student_ai_quota_pkey"),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_ai_quota_student_id_fkey",
        ),
        UniqueConstraint("student_id", "period", name="uq_student_ai_quota_period"),
        CheckConstraint("used >= 0", name="ck_student_ai_quota_used_non_negative"),
        {"comment": "Расход месячной квоты ИИ-наставника (tsk-301)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[date] = mapped_column(
        Date, nullable=False, comment="Первое число месяца"
    )
    used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class StudentAiGrant(Base):
    """Купленный пакет обращений к наставнику. Остаток переносится бессрочно.

    Порядок списания: **сначала месячная квота, потом пакеты** (FIFO по дате
    покупки). Иначе оплаченный пакет сгорает раньше бесплатной квоты, и человек
    теряет деньги в месяц, когда мог не тратить ничего.

    `gateway_payment_id` уникален: повторная доставка уведомления от ЮKassa не
    должна удваивать пакет. Нарушение уникальности маппится на 409, а не на 500
    (урок tsk-574) — и ранняя проверка существования этот перехват не отменяет,
    между ними гонка.
    """

    __tablename__ = "student_ai_grant"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="student_ai_grant_pkey"),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_ai_grant_student_id_fkey",
        ),
        UniqueConstraint(
            "gateway_payment_id", name="uq_student_ai_grant_gateway_payment"
        ),
        CheckConstraint("granted > 0", name="ck_student_ai_grant_granted_positive"),
        CheckConstraint(
            "used >= 0 AND used <= granted", name="ck_student_ai_grant_used_in_range"
        ),
        Index("ix_student_ai_grant_fifo", "student_id", "purchased_at"),
        {"comment": "Докупленные пакеты обращений к наставнику (tsk-301)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    granted: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Сколько обращений куплено"
    )
    used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
        comment="Порядок списания — по возрастанию этой даты (FIFO)",
    )
    gateway_payment_id: Mapped[Optional[str]] = mapped_column(
        Text, comment="Номер платежа ЮKassa. NULL — пакет выдан персоналом вручную"
    )
    granted_by: Mapped[Optional[int]] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text)
