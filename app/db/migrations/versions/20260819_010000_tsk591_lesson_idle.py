"""tsk-591: пульс присутствия ученика и эпизоды простоя на занятии.

Запрос оператора 2026-08-08: на групповом занятии преподаватель не видит, что
кто-то завис или ушёл, — узнаёт об этом в конце урока. Порог решён оператором
09.08: 10 минут, сигнал только преподавателю.

**Две таблицы, потому что это две разные по природе записи.**

`student_presence` — ЖИВОЕ состояние, одна строка на ученика, переписывается
каждым пульсом (раз в 2 минуты, пока вкладка кабинета открыта). История здесь
не нужна и вредна: 30 учеников × 30 пульсов в час дали бы ~20 тыс. строк за
занятие ни для чего. Поэтому не `learning_events`, а собственная таблица
с UPSERT по первичному ключу — блокируется ровно одна строка, своя у каждого
ученика, и параллельные писатели друг друга не задевают (урок tsk-621: запись
на каждый запрос в общую строку — мина).

`lesson_idle_episode` — ФАКТ простоя, живёт вечно и попадает в ленту
преподавателя. Один эпизод на «затих → вернулся», а не запись на каждый тик:
иначе лента забилась бы повторами одного и того же простоя. Это удерживает не
код, а частичный уникальный индекс `uq_lesson_idle_episode_open` — при гонке
двух тиков вторая вставка отваливается на уровне БД.

**Почему `last_interaction_at` отдельно от `last_seen_at`.** Без него нельзя
отличить «читает материал и листает» от «отошёл, а вкладка осталась открытой»:
в обоих случаях пульс идёт. Ученик, который читает и скроллит, для нас активен —
именно на этом различии держится защита от ложных тревог, из-за которых
преподаватель перестал бы читать сигнал через неделю.

Rollback: `alembic downgrade tsk615_payment_purpose` — обе таблицы удаляются
целиком. Данные пульса эфемерны (перезапишутся за 2 минуты), эпизоды простоя
после отката теряются; отдельного экспорта не предусмотрено, потому что вне
ленты преподавателя они ни на что не влияют — ни на оценки, ни на деньги.

Revision ID: tsk591_lesson_idle
Revises: tsk615_payment_purpose
Create Date: 2026-08-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk591_lesson_idle"
down_revision: Union[str, None] = "tsk615_payment_purpose"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Пульс присутствия ────────────────────────────────────────────────
    op.create_table(
        "student_presence",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Последний пульс: вкладка кабинета открыта и видима",
        ),
        sa.Column(
            "last_interaction_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Последний пульс, в котором ученик что-то делал руками "
            "(ввод, касание, прокрутка) — отличает чтение от ухода от экрана",
        ),
        sa.Column(
            "context",
            sa.Text(),
            nullable=True,
            comment="Что открыто в момент пульса: task | material | course | other",
        ),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="student_presence_student_id_fkey",
        ),
        sa.PrimaryKeyConstraint("student_id", name="student_presence_pkey"),
        sa.CheckConstraint(
            "context IS NULL OR context IN ('task', 'material', 'course', 'other')",
            name="student_presence_context_check",
        ),
        comment="Живое присутствие ученика в кабинете: одна строка на ученика (tsk-591)",
    )
    # Строка ученика переписывается каждые 2 минуты, а читается — фоновым
    # тиком по идущим занятиям. Запас под обновление на месте (HOT-update)
    # избавляет от раздувания таблицы из 50 строк до мегабайтов мусора между
    # проходами автоочистки.
    op.execute("ALTER TABLE student_presence SET (fillfactor = 70)")

    # ── Эпизоды простоя ──────────────────────────────────────────────────
    op.create_table(
        "lesson_idle_episode",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("occurrence_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Text(),
            nullable=False,
            comment="away = пульса нет (ушёл из кабинета); "
            "idle = в кабинете, но без действий",
        ),
        sa.Column(
            "silent_since",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Момент последнего признака жизни — от него считается простой",
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда тик поднял тревогу (silent_since + порог, с точностью до тика)",
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда ученик вернулся; NULL = простой продолжается",
        ),
        sa.Column(
            "context",
            sa.Text(),
            nullable=True,
            comment="Что было открыто в момент тишины (для текста события)",
        ),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["lesson_occurrence.id"],
            ondelete="CASCADE",
            name="lesson_idle_episode_occurrence_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="lesson_idle_episode_student_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="lesson_idle_episode_pkey"),
        sa.CheckConstraint(
            "kind IN ('away', 'idle')", name="lesson_idle_episode_kind_check"
        ),
        comment="Простой ученика на занятии: один эпизод на «затих → вернулся» (tsk-591)",
    )
    # Главный инвариант задачи «событие создаётся ОДИН раз на простой»:
    # у ученика в занятии не может быть двух незакрытых эпизодов сразу.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lesson_idle_episode_open
        ON lesson_idle_episode (occurrence_id, student_id)
        WHERE resolved_at IS NULL
        """
    )
    # Чтение ленты: последние эпизоды по времени обнаружения.
    op.create_index(
        "idx_lesson_idle_episode_detected",
        "lesson_idle_episode",
        ["detected_at"],
        unique=False,
    )
    op.create_index(
        "idx_lesson_idle_episode_student",
        "lesson_idle_episode",
        ["student_id", "detected_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_lesson_idle_episode_student", table_name="lesson_idle_episode")
    op.drop_index("idx_lesson_idle_episode_detected", table_name="lesson_idle_episode")
    op.execute("DROP INDEX IF EXISTS uq_lesson_idle_episode_open")
    op.drop_table("lesson_idle_episode")
    op.drop_table("student_presence")
