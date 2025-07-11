# app/api/v1/user_achievements.py

from fastapi import APIRouter, Depends, HTTPException, Body, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.api.deps import get_db
from app.schemas.user_achievements import (
    UserAchievementCreate,
    UserAchievementRead,
    UserAchievementUpdate,
)
from app.services.user_achievements_service import UserAchievementsService

router = APIRouter(prefix="/user-achievements", tags=["user_achievements"])
service = UserAchievementsService()

@router.post(
    "/", response_model=UserAchievementRead, status_code=status.HTTP_201_CREATED
)
async def create_user_achievement(
    obj_in: UserAchievementCreate = Body(...),
    db: AsyncSession = Depends(get_db),
):
    return await service.create(db, obj_in.dict())

@router.get(
    "/{user_id}/{achievement_id}", response_model=UserAchievementRead
)
async def read_user_achievement(
    user_id: int,
    achievement_id: int,
    db: AsyncSession = Depends(get_db),
):
    obj = await service.get_by_keys(db, {"user_id": user_id, "achievement_id": achievement_id})
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return obj

@router.put(
    "/{user_id}/{achievement_id}", response_model=UserAchievementRead
)
async def update_user_achievement(
    user_id: int,
    achievement_id: int,
    obj_in: UserAchievementUpdate = Body(...),
    db: AsyncSession = Depends(get_db),
):
    updated = await service.update_by_keys(
        db,
        {"user_id": user_id, "achievement_id": achievement_id},
        obj_in.dict(exclude_unset=True),
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return updated

@router.delete(
    "/{user_id}/{achievement_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_user_achievement(
    user_id: int,
    achievement_id: int,
    db: AsyncSession = Depends(get_db),
):
    deleted = await service.delete_by_keys(
        db, {"user_id": user_id, "achievement_id": achievement_id}
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
