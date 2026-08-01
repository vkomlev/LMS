"""tsk-505/tsk-506: тарифы курсов и лиды кабинета маркетолога.

Цена привязана к УЧЕНИКУ, а не к паре ученик×курс: курс задаёт лишь тарифную
группу (решение оператора 2026-08-01 на основании живых данных прода — календарь
не знает о курсах, а пара «Python для ЕГЭ» + «ЕГЭ по информатике» это один
продукт, на который зачислены 24 ученика из 34).

Гибкость тарифов держится на паре `match_kind`/`match_value`, а не на колонках
под конкретные атрибуты — новая ось тарификации не требует миграции.

Лиды (tsk-506) едут той же миграцией: та же роль, тот же кабинет, один откат.
`access_requests` для них не переиспользуется — там `user_id` NOT NULL, то есть
запрос роли уже зарегистрированным человеком, а лид существует ДО регистрации.

Данные: seed справочника каналов + семь продаваемых курсов с их тарифами
(цены от оператора). Курсы ищутся по id и проверяются по названию — если курса
нет (чужая база, dev-слепок), строка просто не создаётся, миграция не падает.

Revision ID: tsk505_pricing_and_leads
Revises: tsk498_parent_access_links
Create Date: 2026-08-01

Rollback: `alembic downgrade tsk498_parent_access_links` — сносит пять таблиц
целиком. Ни одна существующая таблица не изменяется, поэтому откат ничего
чужого не задевает.

ВНИМАНИЕ, откат разрушителен для данных кабинета. Пара `downgrade -1` +
`upgrade head` (именно её гоняют при проверке миграции и при откате релиза):
- **сотрёт всех заведённых лидов** — они живут только здесь;
- **вернёт цены к зашитым ниже значениям августа 2026**, потеряв всё, что
  маркетолог менял через кабинет.
Перед откатом на проде — снять дамп пяти таблиц. Цены сидируются здесь
осознанно: без них кабинет открывается пустым и продаваемость семи курсов
пришлось бы заносить руками.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk505_pricing_and_leads"
down_revision: Union[str, None] = "tsk498_parent_access_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Каналы привлечения. Оператор выбрал справочник + «другое» вместо свободного
#: текста: иначе через полгода в поле будут «авито», «Авито» и «avito».
_LEAD_SOURCES = [
    ("avito", "Авито", 10),
    ("yandex_direct", "Яндекс Директ", 20),
    ("telegram", "Telegram", 30),
    ("vk", "ВКонтакте", 40),
    ("referral", "Сарафан / рекомендация", 50),
    ("website", "Сайт", 60),
    ("other", "Другое", 100),
]

#: Тарифные группы: (имя, описание, [(вариант, цена в копейках, ось, значение, порядок)])
_PRICING_GROUPS = [
    (
        "Базовый",
        "ЕГЭ, ОГЭ, Python, подготовка в вуз, мехатроника, чат-боты",
        [
            ("2 раза в неделю", 550000, "attendance_frequency", "2", 10),
            ("1 раз в неделю", 275000, "attendance_frequency", "1", 20),
        ],
    ),
    (
        "ИИ-предприниматель",
        "Трек 1. Создание IT-продуктов",
        [
            ("Для своих", 1000000, "segment", "insider", 10),
            ("Улица", 2000000, "segment", "street", 20),
        ],
    ),
]

#: Курс → тарифная группа. Названия сверяются, чтобы не назначить цену чужому
#: курсу, если id в другой базе занят другим содержимым.
_COURSE_GROUPS = [
    (88, "Python для ЕГЭ", "Базовый"),
    (112, "ЕГЭ по информатике", "Базовый"),
    (1080, "ОГЭ по информатике", "Базовый"),
    (1248, "Вступительное испытание в вуз: ИТ в профессиональной деятельности", "Базовый"),
    (1404, "Мехатроника: от Python к Arduino", "Базовый"),
    (917, "Создание чат-ботов", "Базовый"),
    (1064, "Трек 1. Создание IT-продуктов", "ИИ-предприниматель"),
]


def upgrade() -> None:
    op.create_table(
        "pricing_group",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "name", sa.Text(), nullable=False,
            comment="Имя группы для маркетолога — «Базовый», «ИИ-предприниматель»",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pricing_group_pkey"),
        sa.UniqueConstraint("name", name="uq_pricing_group_name"),
        comment="Тарифные группы курсов (tsk-505)",
    )

    op.create_table(
        "pricing_tariff",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, comment="«2 раза в неделю», «для своих»"),
        sa.Column(
            "price_minor", sa.Integer(), nullable=False,
            comment="Цена в копейках — деньги целым числом, не float",
        ),
        sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'RUB'")),
        sa.Column(
            "period", sa.Text(), nullable=False, server_default=sa.text("'month'"),
            comment="За какой срок цена",
        ),
        sa.Column(
            "match_kind", sa.Text(), nullable=True,
            comment="Ось тарификации: attendance_frequency | segment | NULL",
        ),
        sa.Column(
            "match_value", sa.Text(), nullable=True,
            comment="Значение оси: '1'/'2' для частоты, 'insider'/'street' для сегмента",
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false"),
            comment="Берётся, когда ни один вариант не подошёл по оси",
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["pricing_group.id"], ondelete="CASCADE",
            name="pricing_tariff_group_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="pricing_tariff_pkey"),
        sa.CheckConstraint("price_minor >= 0", name="ck_pricing_tariff_price_non_negative"),
        sa.CheckConstraint(
            "match_kind IS NULL OR match_kind IN ('attendance_frequency', 'segment')",
            name="ck_pricing_tariff_match_kind",
        ),
        comment="Варианты тарифа внутри тарифной группы (tsk-505)",
    )
    # Два активных варианта на одну точку оси сделали бы выбор тарифа
    # недетерминированным — запрещаем. Погашенные (is_active=false) не мешают.
    op.create_index(
        "uq_pricing_tariff_axis_point",
        "pricing_tariff",
        ["group_id", "match_kind", "match_value"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "course_pricing",
        sa.Column("course_id", sa.Integer(), primary_key=True),
        sa.Column("sale_status", sa.Text(), nullable=False, comment="paid | free | not_for_sale"),
        sa.Column(
            "group_id", sa.Integer(), nullable=True,
            comment="Тарифная группа — обязательна для paid, запрещена для остальных",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE",
            name="course_pricing_course_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["pricing_group.id"], ondelete="RESTRICT",
            name="course_pricing_group_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], ondelete="SET NULL",
            name="course_pricing_updated_by_fkey",
        ),
        sa.PrimaryKeyConstraint("course_id", name="course_pricing_pkey"),
        sa.CheckConstraint(
            "sale_status IN ('paid', 'free', 'not_for_sale')",
            name="ck_course_pricing_sale_status",
        ),
        sa.CheckConstraint(
            "(sale_status = 'paid') = (group_id IS NOT NULL)",
            name="ck_course_pricing_paid_requires_group",
        ),
        comment="Продаваемость курса и его тарифная группа (tsk-505)",
    )

    op.create_table(
        "lead_source",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, comment="Машинный код канала"),
        sa.Column("name", sa.Text(), nullable=False, comment="Название для человека"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id", name="lead_source_pkey"),
        sa.UniqueConstraint("code", name="uq_lead_source_code"),
        comment="Справочник каналов привлечения лидов (tsk-506)",
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_detail", sa.Text(), nullable=True,
            comment="Приписка к каналу — обязательна при канале «другое»",
        ),
        sa.Column("full_name", sa.Text(), nullable=True, comment="Как зовут, если известно"),
        sa.Column(
            "contact", sa.Text(), nullable=False,
            comment="Ссылка/ник/телефон как есть — формат не валидируем, каналы разные",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "linked_student_id", sa.Integer(), nullable=True,
            comment="Проставляется после регистрации — до неё лид ни с кем не связан",
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["lead_source.id"], ondelete="RESTRICT",
            name="leads_source_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["linked_student_id"], ["users.id"], ondelete="SET NULL",
            name="leads_linked_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL",
            name="leads_created_by_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="leads_pkey"),
        comment="Лиды — мини-CRM кабинета маркетолога (tsk-506)",
    )
    # Основной экран — список с фильтром «привязан / не привязан».
    op.create_index("ix_leads_linked_student", "leads", ["linked_student_id"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])

    _seed(op.get_bind())


def _seed(conn: sa.engine.Connection) -> None:
    """Наполнение справочников. Идемпотентно — ON CONFLICT DO NOTHING."""
    for code, name, sort_order in _LEAD_SOURCES:
        conn.execute(
            sa.text(
                "INSERT INTO lead_source (code, name, sort_order) "
                "VALUES (:code, :name, :sort_order) ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "name": name, "sort_order": sort_order},
        )

    group_ids: dict[str, int] = {}
    for name, description, tariffs in _PRICING_GROUPS:
        conn.execute(
            sa.text(
                "INSERT INTO pricing_group (name, description) VALUES (:name, :description) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "description": description},
        )
        group_id = conn.execute(
            sa.text("SELECT id FROM pricing_group WHERE name = :name"), {"name": name}
        ).scalar_one()
        group_ids[name] = group_id

        for tariff_name, price_minor, match_kind, match_value, sort_order in tariffs:
            conn.execute(
                sa.text(
                    "INSERT INTO pricing_tariff "
                    "(group_id, name, price_minor, match_kind, match_value, sort_order) "
                    "VALUES (:gid, :name, :price, :kind, :value, :sort) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "gid": group_id,
                    "name": tariff_name,
                    "price": price_minor,
                    "kind": match_kind,
                    "value": match_value,
                    "sort": sort_order,
                },
            )

    for course_id, expected_title, group_name in _COURSE_GROUPS:
        title = conn.execute(
            sa.text("SELECT title FROM courses WHERE id = :id"), {"id": course_id}
        ).scalar()
        # Чужая база или другой слепок — id может быть занят другим курсом.
        # Пропускаем: цена лучше отсутствует, чем стоит не на том курсе. Но не
        # молча — иначе на другой базе цены просто не появятся без единого следа.
        if title is None or title.strip() != expected_title:
            print(
                f"[tsk-505] курс id={course_id} пропущен: ожидали "
                f"{expected_title!r}, в базе {title!r} — цену не назначаем"
            )
            continue
        conn.execute(
            sa.text(
                "INSERT INTO course_pricing (course_id, sale_status, group_id) "
                "VALUES (:cid, 'paid', :gid) ON CONFLICT (course_id) DO NOTHING"
            ),
            {"cid": course_id, "gid": group_ids[group_name]},
        )


def downgrade() -> None:
    op.drop_index("ix_leads_created_at", table_name="leads")
    op.drop_index("ix_leads_linked_student", table_name="leads")
    op.drop_table("leads")
    op.drop_table("lead_source")
    op.drop_table("course_pricing")
    op.drop_index("uq_pricing_tariff_axis_point", table_name="pricing_tariff")
    op.drop_table("pricing_tariff")
    op.drop_table("pricing_group")
