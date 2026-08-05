"""tsk-427: доп. поля профиля ученика — категория, класс, город, часовой пояс.

Запрос оператора: профиль ученика должен нести категорию (школьник + класс,
студент вуза, студент суза, абитуриент, взрослый), город и часовой пояс —
всё заполняется позже в кабинете, не при регистрации (все поля nullable).

Решения оператора (см. tsk-427 «История движения» 2026-08-05):
- «Класс» — отдельное nullable-поле `school_grade` (число 1-11), НЕ часть
  строки категории. NULL для не-школьников.
- «Город» — свободный текст, без справочника/автокомплита.
- «Часовой пояс» — вводится вручную (IANA-идентификатор), не выводится из
  города автоматически.

Пересечение с tsk-021 (Календарь LMS) проверено и отсутствует: таймзона
календаря — глобальное значение школы (`operating_hours.timezone`,
Europe/Moscow), не поле пользователя. `users.timezone` здесь — независимый
источник истины с нуля.

`category` — String + CHECK (не native Postgres ENUM): актуальный паттерн
проекта после baseline-миграции, см. `tsk505_pricing_and_leads`
(sale_status/match_kind). Значения — латинские snake_case-коды, тексты для
UI переводятся на стороне клиента (SPW).

Revision ID: tsk427_profile_extra_fields
Revises: tsk423_demo_task_limit
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk427_profile_extra_fields"
down_revision: Union[str, None] = "tsk423_demo_task_limit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATEGORY_VALUES = (
    "school_student",
    "university_student",
    "college_student",
    "applicant",
    "adult",
)


def upgrade() -> None:
    """Добавляет nullable-колонки category/school_grade/city/timezone в users."""
    op.add_column(
        "users",
        sa.Column(
            "category",
            sa.String(32),
            nullable=True,
            comment=(
                "tsk-427: категория ученика — school_student/"
                "university_student/college_student/applicant/adult. "
                "NULL = не заполнено."
            ),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "school_grade",
            sa.Integer(),
            nullable=True,
            comment="tsk-427: класс (1-11), только для category=school_student.",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "city",
            sa.String(255),
            nullable=True,
            comment="tsk-427: город, свободный текст без справочника.",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=True,
            comment="tsk-427: часовой пояс, IANA-идентификатор (напр. Europe/Moscow). Вводится вручную.",
        ),
    )

    category_list = ", ".join(f"'{v}'" for v in _CATEGORY_VALUES)
    op.create_check_constraint(
        "ck_users_category",
        "users",
        f"category IS NULL OR category IN ({category_list})",
    )
    op.create_check_constraint(
        "ck_users_school_grade_range",
        "users",
        "school_grade IS NULL OR (school_grade BETWEEN 1 AND 11)",
    )
    # Класс имеет смысл только у школьника — некорректная связка (студент
    # вуза с проставленным классом) должна быть невозможна на уровне БД, а
    # не только валидацией в API.
    op.create_check_constraint(
        "ck_users_school_grade_only_for_school_student",
        "users",
        "category = 'school_student' OR school_grade IS NULL",
    )


def downgrade() -> None:
    """Убирает constraints и колонки category/school_grade/city/timezone.

    Rollback-note: чисто аддитивная миграция без backfill и без данных,
    затрагивающих другие таблицы — откат безопасен на любом объёме, теряются
    только значения этих 4 колонок (nullable, заполнялись пользователями
    самостоятельно в профиле).
    """
    op.drop_constraint("ck_users_school_grade_only_for_school_student", "users", type_="check")
    op.drop_constraint("ck_users_school_grade_range", "users", type_="check")
    op.drop_constraint("ck_users_category", "users", type_="check")
    op.drop_column("users", "timezone")
    op.drop_column("users", "city")
    op.drop_column("users", "school_grade")
    op.drop_column("users", "category")
