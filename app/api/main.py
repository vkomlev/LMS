# app/api/main.py

import logging
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.logger import setup_logging

# Роутеры
from app.api.v1.crud import create_crud_router, create_composite_router
from app.api.v1.user_achievements import router as user_achievements_router
from app.api.v1.study_plan_courses import router as study_plan_courses_router

# Схемы и сервисы
from app.schemas.users import UserCreate, UserRead, UserUpdate
from app.services.users_service import UsersService

from app.schemas.achievements import (
    AchievementCreate, AchievementRead, AchievementUpdate
)
from app.services.achievements_service import AchievementsService

from app.schemas.courses import CourseCreate, CourseRead, CourseUpdate
from app.services.courses_service import CoursesService

from app.schemas.difficulty_levels import (
    DifficultyLevelCreate, DifficultyLevelRead, DifficultyLevelUpdate
)
from app.services.difficulty_levels_service import DifficultyLevelsService

from app.schemas.roles import RoleCreate, RoleRead, RoleUpdate
from app.services.roles_service import RolesService

from app.schemas.materials import MaterialCreate, MaterialRead, MaterialUpdate
from app.services.materials_service import MaterialsService

from app.schemas.messages import MessageCreate, MessageRead, MessageUpdate
from app.services.messages_service import MessagesService

from app.schemas.notifications import (
    NotificationCreate, NotificationRead, NotificationUpdate
)
from app.services.notifications_service import NotificationsService

from app.schemas.social_posts import (
    SocialPostCreate, SocialPostRead, SocialPostUpdate
)
from app.services.social_posts_service import SocialPostsService

from app.schemas.study_plans import (
    StudyPlanCreate, StudyPlanRead, StudyPlanUpdate
)
from app.services.study_plans_service import StudyPlansService

from app.schemas.tasks import TaskCreate, TaskRead, TaskUpdate
from app.services.tasks_service import TasksService

from app.schemas.user_achievements import (
    UserAchievementCreate, UserAchievementRead, UserAchievementUpdate
)
from app.services.user_achievements_service import UserAchievementsService

from app.schemas.study_plan_courses import (
    StudyPlanCourseCreate, StudyPlanCourseRead, StudyPlanCourseUpdate
)
from app.services.study_plan_courses_service import StudyPlanCoursesService

from app.schemas.task_results import (
    TaskResultCreate, TaskResultRead, TaskResultUpdate
)
from app.services.task_results_service import TaskResultsService


# Настраиваем логи (файлы + консоль)
setup_logging()
logger = logging.getLogger("api.main")
API_PREFIX = "/api/v1"

app = FastAPI(title="LMS Core API")

# CORS (уберите или сузьте, если не нужно)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Validation error at %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception at %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )

@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}


# Подключаем все CRUD-роутеры:

app.include_router(
    create_crud_router(
        prefix="/users", tags=["users"],
        service=UsersService(),
        create_schema=UserCreate, read_schema=UserRead, update_schema=UserUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/achievements", tags=["achievements"],
        service=AchievementsService(),
        create_schema=AchievementCreate, read_schema=AchievementRead, update_schema=AchievementUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/courses", tags=["courses"],
        service=CoursesService(),
        create_schema=CourseCreate, read_schema=CourseRead, update_schema=CourseUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/difficulty-levels", tags=["difficulty_levels"],
        service=DifficultyLevelsService(),
        create_schema=DifficultyLevelCreate, read_schema=DifficultyLevelRead, update_schema=DifficultyLevelUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/roles", tags=["roles"],
        service=RolesService(),
        create_schema=RoleCreate, read_schema=RoleRead, update_schema=RoleUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/materials", tags=["materials"],
        service=MaterialsService(),
        create_schema=MaterialCreate, read_schema=MaterialRead, update_schema=MaterialUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/messages", tags=["messages"],
        service=MessagesService(),
        create_schema=MessageCreate, read_schema=MessageRead, update_schema=MessageUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/notifications", tags=["notifications"],
        service=NotificationsService(),
        create_schema=NotificationCreate, read_schema=NotificationRead, update_schema=NotificationUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/social-posts", tags=["social_posts"],
        service=SocialPostsService(),
        create_schema=SocialPostCreate, read_schema=SocialPostRead, update_schema=SocialPostUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/study-plans", tags=["study_plans"],
        service=StudyPlansService(),
        create_schema=StudyPlanCreate, read_schema=StudyPlanRead, update_schema=StudyPlanUpdate,
    ),
    prefix=API_PREFIX,
)

app.include_router(
    create_crud_router(
        prefix="/tasks", tags=["tasks"],
        service=TasksService(),
        create_schema=TaskCreate, read_schema=TaskRead, update_schema=TaskUpdate,
    ),
    prefix=API_PREFIX,
)


# UserAchievements (composite PK: user_id + achievement_id)
app.include_router(user_achievements_router, prefix=API_PREFIX)


# StudyPlanCourses (composite PK: study_plan_id + course_id)
app.include_router(study_plan_courses_router, prefix=API_PREFIX)


app.include_router(
    create_crud_router(
        prefix="/task-results", tags=["task_results"],
        service=TaskResultsService(),
        create_schema=TaskResultCreate, read_schema=TaskResultRead, update_schema=TaskResultUpdate,
    ),
    prefix=API_PREFIX,
)
