# app/services/materials_service.py

from app.models.materials import Materials
from app.repos.materials_repo import MaterialsRepository
from app.services.base import BaseService


class MaterialsService(BaseService[Materials]):
    """
    Сервис для учебных материалов.
    """
    def __init__(self, repo: MaterialsRepository = MaterialsRepository()):
        super().__init__(repo)

    # TODO: list_by_course
