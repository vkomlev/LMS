# app/services/social_posts_service.py

from app.models.social_posts import SocialPosts
from app.repos.social_posts_repo import SocialPostsRepository
from app.services.base import BaseService


class SocialPostsService(BaseService[SocialPosts]):
    """
    Сервис для социальных постов.
    """
    def __init__(self, repo: SocialPostsRepository = SocialPostsRepository()):
        super().__init__(repo)

    # TODO: list_by_course
