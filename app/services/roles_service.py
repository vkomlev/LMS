# app/services/roles_service.py

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.roles import Roles
from app.repos.roles_repo import RolesRepository
from app.services.base import BaseService


class RolesService(BaseService[Roles]):
    """
    Сервис для ролей пользователей.
    """
    def __init__(self, repo: RolesRepository = RolesRepository()):
        super().__init__(repo)

    # TODO: get_by_name


async def get_user_role_names(db: AsyncSession, user_id: int) -> list[str]:
    """Вернуть отсортированный список имён ролей пользователя (user_roles M2M).

    Единый источник правды по ролям для API-слоя (tsk-298, Фаза 0):
    используется и в `GET /me` (поле `roles`), и в dependency `require_role`.
    Запрос через `text()` (без ORM-импорта association-таблицы) — чтобы
    сервис можно было импортировать в `app.api.deps` на раннем этапе загрузки
    приложения без циклического импорта моделей.

    :param db: асинхронная сессия SQLAlchemy.
    :param user_id: ID пользователя.
    :return: отсортированные по алфавиту имена ролей (может быть пустым).
    """
    result = await db.execute(
        text(
            "SELECT r.name FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :uid "
            "ORDER BY r.name"
        ),
        {"uid": user_id},
    )
    return [row[0] for row in result.fetchall()]
