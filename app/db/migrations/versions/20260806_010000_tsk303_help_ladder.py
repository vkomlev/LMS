"""tsk-303 Фаза 1: лестница помощи — уровни 2-3 поверх help_requests.

Схема под решения оператора (см. tsk-303 «Открытые вопросы… РЕШЕНЫ» 2026-08-06):
уровень 1 — ответ текстом с авто-закрытием и кнопкой ученика «Вернуть заявку»;
уровень 2 — индивидуальный разбор по вебинар-ссылке с оценкой ученика;
уровень 3 — эскалация методисту, если после разбора всё ещё непонятно.

**Почему история возвратов таблицей, а не счётчиком `reopen_count`.**
Задача разрешает оба варианта («на усмотрение реализации, важна агрегируемость
по преподавателю»), но счётчик не выдерживает своего же назначения:

1. *Атрибуция.* Возврат означает «этот текстовый ответ не помог» и должен
   начисляться тому, кто отвечал. Это не всегда `assigned_teacher_id`: ACL
   `can_access_help_request` пускает к заявке и методиста (bypass по роли), и
   преподавателя по `student_teacher_links`/`teacher_courses`. Счётчик на
   строке заявки таких развилок не различает и повесил бы чужой возврат на
   назначенного преподавателя.
2. *Период.* KPI-панель («возвраты за месяц») невыразима через голый счётчик —
   у него нет времени. Пришлось бы добавлять историю следующей же миграцией.

Строка истории хранит `teacher_id` — того, чей ответ не помог (на момент
возврата это `closed_by` заявки, при системном закрытии — `assigned_teacher_id`).
Счётчик для карточки выводится `COUNT(*)`, второго источника правды нет.

**Вебинар-ссылка.** Простой текст, без интеграции с видео-сервисом (решение
оператора). TTL — время жизни заявки: при закрытии ссылка обнуляется (логика
фазы 3), поэтому CHECK ниже сформулирован так, чтобы обнуление его не нарушало
(`NULL` разрешён всегда). `review_understood` и `escalated_to_methodist_at`
обнуление ссылки переживают — они и есть история разбора. Пустая строка при
этом запрещена наравне с чужим классом заявки — см. комментарий у ограничения
(профилактика класса из `docs/ai/ERRORS.md`, 2026-07-22 tsk-363).

Rollback-note: миграция аддитивна, backfill'а нет, существующие колонки не
меняются. `alembic downgrade tsk427_profile_extra_fields` снимает CHECK, три
колонки и таблицу истории. Теряются только данные уровней 2-3 (вебинар-ссылки,
оценки, отметки эскалации) и история возвратов — то есть KPI. Заявки, ответы и
переписка не затрагиваются вовсе. На момент применения в проде 83 заявки
(54 blocked_limit + 29 manual_help), все закрытые, ни одной строки уровней 2-3
физически не существует — откат безопасен на любом объёме.

Revision ID: tsk303_help_ladder
Revises: tsk427_profile_extra_fields
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk303_help_ladder"
down_revision: Union[str, None] = "tsk427_profile_extra_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Имя ограничения — то, что реально стоит в БД с миграции этапа 3.8
# (авто-имя PostgreSQL). Пересоздаём под тем же именем, чтобы downgrade был
# симметричен, а не оставлял ограничение с другим названием.
_REQUEST_TYPE_CHECK = "help_requests_request_type_check"
_REQUEST_TYPES_OLD = ("manual_help", "blocked_limit")
_REQUEST_TYPES_NEW = ("manual_help", "blocked_limit", "individual_review")


def _in_list(values: Sequence[str]) -> str:
    """SQL-фрагмент `'a', 'b'` для IN-списка ограничения."""
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    """Добавляет поля уровней 2-3 и таблицу истории возвратов заявки."""
    op.add_column(
        "help_requests",
        sa.Column(
            "webinar_link",
            sa.Text(),
            nullable=True,
            comment=(
                "tsk-303: ссылка на комнату индивидуального разбора. Присылает "
                "преподаватель вручную, простой текст. Живёт, пока заявка "
                "открыта — при закрытии обнуляется."
            ),
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "review_understood",
            sa.Boolean(),
            nullable=True,
            comment=(
                "tsk-303: оценка ученика после разбора («всё понятно?»). "
                "NULL — ещё не оценивал; false ведёт к эскалации методисту."
            ),
        ),
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "escalated_to_methodist_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "tsk-303: когда заявка ушла методисту (уровень 3). "
                "NULL — не эскалировалась."
            ),
        ),
    )

    # Новый класс заявки нужен и в БД: ограничение стоит с этапа 3.8 и без
    # пересоздания INSERT/UPDATE с individual_review упёрся бы в него.
    op.drop_constraint(_REQUEST_TYPE_CHECK, "help_requests", type_="check")
    op.create_check_constraint(
        _REQUEST_TYPE_CHECK,
        "help_requests",
        f"request_type IN ({_in_list(_REQUEST_TYPES_NEW)})",
    )

    # Ссылка на разбор: если она есть, то (а) только у заявки этого класса —
    # иначе молча осела бы на blocked_limit-заявке, где ученику её никто не
    # покажет; (б) непустая.
    #
    # Пункт (б) — профилактика класса из реестра ошибок проекта
    # (`docs/ai/ERRORS.md`, 2026-07-22 tsk-363): пустая строка вместо NULL уже
    # роняла прод, и профилактика оттуда требует подстраховки на уровне схемы,
    # а не только нормализации в сервисе. Здесь ссылку вводит руками
    # преподаватель, то есть источник ровно того же рода: `''` прошла бы
    # проверку «не NULL», а ученик получил бы кнопку «Перейти к разбору»,
    # ведущую в пустоту, при формально отвеченной заявке.
    #
    # Формулировка «NULL разрешён всегда» намеренная: закрытие заявки обнуляет
    # ссылку (TTL), и ограничение не должно этому мешать.
    #
    # «Непустая» выражена через `~ '\S'` (есть хоть один непробельный символ), а
    # НЕ через `length(btrim(...)) > 0`: `btrim` без второго аргумента срезает
    # только ПРОБЕЛЫ, и строка из табуляции с переводом строки прошла бы такую
    # проверку. Проверено в самой БД (`SELECT E'\t\n' ~ '\S'` → false,
    # `length(btrim(E'\t\n'))` → 2), как предписывает реестр ошибок для
    # регулярок и SQL-семантики (`docs/ai/ERRORS.md`, 2026-07-17 tsk-262/278).
    op.create_check_constraint(
        "ck_help_requests_webinar_link_type",
        "help_requests",
        r"webinar_link IS NULL OR ("
        r"request_type = 'individual_review' AND webinar_link ~ '\S')",
    )

    op.create_table(
        "help_request_reopens",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "request_id",
            sa.BigInteger,
            sa.ForeignKey("help_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Кому засчитан возврат — тот, чей ответ не помог. NULL допустим:
        # учётку могли удалить, а заявку мог закрыть не человек, а система
        # (`closed_by IS NULL`, см. tsk-339).
        sa.Column(
            "teacher_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reopened_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="tsk-303: история возвратов заявки помощи учеником (KPI преподавателя)",
    )
    # Основной запрос KPI: возвраты преподавателя за период.
    op.create_index(
        "idx_help_request_reopens_teacher_time",
        "help_request_reopens",
        ["teacher_id", "reopened_at"],
    )
    # Сколько раз возвращали эту заявку — для карточки и гейта уровня 2.
    op.create_index(
        "idx_help_request_reopens_request",
        "help_request_reopens",
        ["request_id"],
    )


def downgrade() -> None:
    """Снимает таблицу истории, CHECK'и и три колонки уровней 2-3."""
    op.drop_index("idx_help_request_reopens_request", table_name="help_request_reopens")
    op.drop_index("idx_help_request_reopens_teacher_time", table_name="help_request_reopens")
    op.drop_table("help_request_reopens")

    op.drop_constraint("ck_help_requests_webinar_link_type", "help_requests", type_="check")

    # Возврат к двум классам заявки. Строки с individual_review к этому моменту
    # существовать не должны — если их создали, откат упадёт здесь осознанно,
    # а не потеряет данные молча.
    op.drop_constraint(_REQUEST_TYPE_CHECK, "help_requests", type_="check")
    op.create_check_constraint(
        _REQUEST_TYPE_CHECK,
        "help_requests",
        f"request_type IN ({_in_list(_REQUEST_TYPES_OLD)})",
    )

    op.drop_column("help_requests", "escalated_to_methodist_at")
    op.drop_column("help_requests", "review_understood")
    op.drop_column("help_requests", "webinar_link")
