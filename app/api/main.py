# app/api/main.py

import logging
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.logger import setup_logging
from app.api.v1.crud import create_crud_router
from app.schemas.users import UserCreate, UserRead, UserUpdate
from app.services.users_service import UsersService
from app.schemas.achievements import (
    AchievementCreate, AchievementRead, AchievementUpdate
)
from app.services.achievements_service import AchievementsService

# … аналогично для других сущностей: courses, tasks и т.п.

# Настраиваем логи (файлы + консоль)
setup_logging()
logger = logging.getLogger("api.main")

app = FastAPI(title="LMS Core API")

# Пример CORS (если нужно на будущее)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Валидационные ошибки Pydantic → 422, возвращаем список ошибок
    """
    logger.warning("Validation error at %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Все прочие исключения → 500, общий ответ, скрываем детали от клиента.
    """
    logger.error("Unhandled exception at %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )

# 3. (Опционально) health-check
@app.get("/health", tags=["health"])
async def health_check():
    """
    Простая проверка работоспособности сервиса.
    """
    return {"status": "ok"}

# Users
app.include_router(
    create_crud_router(
        prefix="/users",
        tags=["users"],
        service=UsersService(),
        create_schema=UserCreate,
        read_schema=UserRead,
        update_schema=UserUpdate,
    ),
    prefix="/api/v1",
)

# Achievements
app.include_router(
    create_crud_router(
        prefix="/achievements",
        tags=["achievements"],
        service=AchievementsService(),
        create_schema=AchievementCreate,
        read_schema=AchievementRead,
        update_schema=AchievementUpdate,
    ),
    prefix="/api/v1",
)

# …добавьте остальные по аналогии
