# app/repos/social_posts_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.social_posts import SocialPosts
from app.repos.base import BaseRepository


class SocialPostsRepository(BaseRepository[SocialPosts]):
    """
    Репозиторий для социальных постов.
    """
    def __init__(self) -> None:
        super().__init__(SocialPosts)