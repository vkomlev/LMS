from sqlalchemy import (
    Table, Column, Integer, ForeignKeyConstraint,
    PrimaryKeyConstraint
)
from app.db.base import Base

t_course_dependencies = Table(
    "course_dependencies",
    Base.metadata,
    Column("course_id", Integer, primary_key=True, nullable=False, comment="ID курса, требующего завершения другого"),
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
