# app/api/v1/study_plan_courses.py

from fastapi import APIRouter, Depends, HTTPException, Body, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.api.deps import get_db
from app.schemas.study_plan_courses import (
    StudyPlanCourseCreate,
    StudyPlanCourseRead,
    StudyPlanCourseUpdate,
)
from app.services.study_plan_courses_service import StudyPlanCoursesService

router = APIRouter(prefix="/study-plan-courses", tags=["study_plan_courses"])
service = StudyPlanCoursesService()


@router.post(
    "/", response_model=StudyPlanCourseRead, status_code=status.HTTP_201_CREATED
)
async def create_study_plan_course(
    obj_in: StudyPlanCourseCreate = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Создать связь курса и учебного плана.
    """
    return await service.create(db, obj_in.dict())


@router.get(
    "/{study_plan_id}/{course_id}", response_model=StudyPlanCourseRead
)
async def read_study_plan_course(
    study_plan_id: int,
    course_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Получить связь учебного плана и курса по составному ключу.
    """
    obj = await service.get_by_keys(
        db, {"study_plan_id": study_plan_id, "course_id": course_id}
    )
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return obj


@router.put(
    "/{study_plan_id}/{course_id}", response_model=StudyPlanCourseRead
)
async def update_study_plan_course(
    study_plan_id: int,
    course_id: int,
    obj_in: StudyPlanCourseUpdate = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Обновить связь учебного плана и курса.
    """
    updated = await service.update_by_keys(
        db,
        {"study_plan_id": study_plan_id, "course_id": course_id},
        obj_in.dict(exclude_unset=True),
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return updated


@router.delete(
    "/{study_plan_id}/{course_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_study_plan_course(
    study_plan_id: int,
    course_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Удалить связь учебного плана и курса.
    """
    deleted = await service.delete_by_keys(
        db, {"study_plan_id": study_plan_id, "course_id": course_id}
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
