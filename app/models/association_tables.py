from sqlalchemy import (
    Table, Column, Integer, SmallInteger, ForeignKeyConstraint,
    PrimaryKeyConstraint, text, DateTime
)
from app.db.base import Base

t_course_dependencies = Table(
    "course_dependencies",
    Base.metadata,
    Column(
        "course_id", Integer, primary_key=True, nullable=False,
        comment=(
            "ID курса, требующего завершения другого. "
            "⚠️ ВАЖНО: Предотвращение самоссылок реализовано в БД через CHECK CONSTRAINT "
            "(check_no_self_dependency). Не дублировать логику в коде! "
            "См. docs/database-triggers-contract.md"
        )
    ),
    Column("required_course_id", Integer, primary_key=True, nullable=False, comment="ID обязательного курса"),
    ForeignKeyConstraint(
        ["course_id"], ["courses.id"],
        ondelete="CASCADE", name="course_dependencies_course_id_fkey"
    ),
    ForeignKeyConstraint(
        ["required_course_id"], ["courses.id"],
        ondelete="CASCADE", name="course_dependencies_required_course_id_fkey"
    ),
    PrimaryKeyConstraint("course_id", "required_course_id", name="course_dependencies_pkey"),
    comment="Зависимости между курсами"
)

t_user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, primary_key=True, nullable=False, comment="ID пользователя"),
    Column("role_id", Integer, primary_key=True, nullable=False, comment="ID роли"),
    ForeignKeyConstraint(
        ["user_id"], ["users.id"],
        ondelete="CASCADE", name="user_roles_user_id_fkey"
    ),
    ForeignKeyConstraint(
        ["role_id"], ["roles.id"],
        name="user_roles_role_id_fkey"
    ),
    PrimaryKeyConstraint("user_id", "role_id", name="user_roles_pkey"),
    comment="Связь пользователей с ролями"
)

t_student_teacher_links = Table(
    "student_teacher_links",
    Base.metadata,
    Column("student_id", Integer, primary_key=True, nullable=False, comment="ID студента"),
    Column("teacher_id", Integer, primary_key=True, nullable=False, comment="ID преподавателя"),
    Column("linked_at",  DateTime(timezone=True), server_default=text("now()"), nullable=False, comment="Когда добавлен"),
    ForeignKeyConstraint(
        ["student_id"], ["users.id"],
        ondelete="CASCADE", name="student_teacher_links_student_id_fkey"
    ),
    ForeignKeyConstraint(
        ["teacher_id"], ["users.id"],
        ondelete="CASCADE", name="student_teacher_links_teacher_id_fkey"
    ),
    PrimaryKeyConstraint("student_id", "teacher_id", name="student_teacher_links_pkey"),
    comment="Привязка студентов к преподавателям"
)

t_course_parents = Table(
    "course_parents",
    Base.metadata,
    Column(
        "course_id", Integer, primary_key=True, nullable=False,
        comment=(
            "ID дочернего курса. "
            "⚠️ ВАЖНО: Предотвращение самоссылок и циклов реализовано в БД через триггер "
            "(trg_check_course_hierarchy_cycle). Не дублировать логику в коде! "
            "См. docs/database-triggers-contract.md"
        )
    ),
    Column("parent_course_id", Integer, primary_key=True, nullable=False, comment="ID родительского курса"),
    Column(
        "order_number",
        SmallInteger,
        nullable=True,
        comment=(
            "Порядковый номер подкурса внутри родительского курса. "
            "⚠️ ВАЖНО: Автоматически устанавливается и пересчитывается триггером БД "
            "(trg_set_course_parent_order_number). "
            "Не дублировать логику в коде приложения! "
            "См. docs/database-triggers-contract.md"
        )
    ),
    ForeignKeyConstraint(
        ["course_id"], ["courses.id"],
        ondelete="CASCADE", name="course_parents_course_id_fkey"
    ),
    ForeignKeyConstraint(
        ["parent_course_id"], ["courses.id"],
        ondelete="CASCADE", name="course_parents_parent_course_id_fkey"
    ),
    PrimaryKeyConstraint("course_id", "parent_course_id", name="course_parents_pkey"),
    comment="Иерархия курсов: связь многие-ко-многим (курс может иметь несколько родителей)"
)