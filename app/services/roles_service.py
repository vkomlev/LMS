# app/services/roles_service.py

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
