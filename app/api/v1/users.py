# app/api/v1/users.py
from typing import List, Optional
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import asc, desc
import logging

from app.core.logger import setup_logging
from app.api.deps import get_db
from app.api.v1.crud import create_crud_router
from app.services.users_service import UsersService
from app.schemas.users import UserID, UserRead, UserCreate, UserUpdate
from app.models.users import Users
from app.utils.pagination import Page, build_page

router = APIRouter(prefix="/users", tags=["users"])
service = UsersService()

setup_logging()
logger = logging.getLogger(__name__)


class SortByField(str, Enum):
    """Поля для сортировки"""
    full_name = "full_name"
    email = "email"
    created_at = "created_at"


class SortOrder(str, Enum):
    """Направление сортировки"""
    asc = "asc"
    desc = "desc"

@router.get(
    "/search",
    response_model=List[UserRead],
    summary="Поиск пользователей по фрагменту имени (full_name)",
    description=(
        "Поиск пользователей по фрагменту имени (поля `full_name`).\n\n"
        "**Особенности:**\n"
        "- Поиск нечувствителен к регистру (case-insensitive)\n"
        "- Поиск выполняется по шаблону `ILIKE %q%` (содержит подстроку)\n"
        "- Результаты автоматически сортируются по `full_name` ASC\n"
        "- Минимальная длина запроса: 2 символа\n\n"
        "**Примеры:**\n"
        "- `GET /api/v1/users/search?q=Иван` - найдет всех пользователей с 'Иван' в имени\n"
        "- `GET /api/v1/users/search?q=test&limit=10` - поиск с ограничением результатов"
    ),
    responses={
        200: {
            "description": "Поиск выполнен успешно",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 13,
                            "email": "test_student_1@example.com",
                            "full_name": "Студент Тестовый 1",
                            "tg_id": None,
                            "created_at": "2026-01-26T14:21:50.221Z"
                        }
                    ]
                }
            }
        },
        400: {"description": "Запрос слишком короткий (менее 2 символов)"},
        403: {"description": "Invalid or missing API Key"},
        422: {"description": "Ошибка валидации параметров"},
    },
)
async def search_users_by_name(
    q: str = Query(
        ..., 
        min_length=2, 
        description="Фрагмент имени для поиска (минимум 2 символа)",
        examples=["Иван", "Петр", "test"]
    ),
    limit: int = Query(
        20, 
        ge=1, 
        le=200,
        description="Максимум результатов (от 1 до 200)",
        examples=[10, 20, 50]
    ),
    offset: int = Query(
        0, 
        ge=0,
        description="Смещение для пагинации",
        examples=[0, 10, 20]
    ),
    db: AsyncSession = Depends(get_db),
) -> List[UserRead]:
    """
    Ищет пользователей по фрагменту имени в поле `full_name`.
    
    **Параметры запроса:**
    - `q` (string, обязательный): Фрагмент имени для поиска. Минимум 2 символа
    - `limit` (int, опционально): Максимум результатов. По умолчанию: 20, максимум: 200
    - `offset` (int, опционально): Смещение для пагинации. По умолчанию: 0
    
    **Ответ:**
    Возвращает массив объектов `UserRead`, отсортированных по `full_name` ASC.
    
    **Коды ответов:**
    - `200` - Поиск выполнен успешно
    - `400` - Запрос слишком короткий (менее 2 символов)
    - `403` - Неверный или отсутствующий API ключ
    - `422` - Ошибка валидации параметров
    """
    logger.info("users.search q=%r limit=%s offset=%s", q, limit, offset)
    items = await service.search_text(
        db,
        field="full_name",
        query=q,
        mode="contains",
        case_insensitive=True,
        limit=limit,
        offset=offset,
        order_by=Users.full_name,  # опционально сортируем по имени
    )
    logger.debug("users.search -> %d rows", len(items))
    return items


@router.get(
    "/",
    response_model=Page[UserRead],  # type: ignore[name-defined]
    summary="Список пользователей с пагинацией, сортировкой и фильтрацией",
    description=(
        "Получить список пользователей с поддержкой пагинации, сортировки и фильтрации по роли.\n\n"
        "**Особенности:**\n"
        "- Все параметры опциональны (используются значения по умолчанию)\n"
        "- Сортировка по умолчанию: `full_name ASC` (алфавитный порядок)\n"
        "- NULL значения в полях сортировки всегда идут в конец списка\n"
        "- Фильтрация по роли выполняется по имени роли (регистр не важен)\n"
        "- Если указана несуществующая роль, возвращается пустой список\n\n"
        "**Примеры использования:**\n"
        "- `GET /api/v1/users/` - все пользователи, сортировка по full_name ASC\n"
        "- `GET /api/v1/users/?role=student` - только студенты\n"
        "- `GET /api/v1/users/?sort_by=email&order=desc` - сортировка по email по убыванию\n"
        "- `GET /api/v1/users/?skip=0&limit=50&sort_by=full_name&order=asc&role=student` - полный пример"
    ),
    responses={
        200: {
            "description": "Список пользователей успешно получен",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "id": 13,
                                "email": "test_student_1@example.com",
                                "full_name": "Студент Тестовый 1",
                                "tg_id": None,
                                "created_at": "2026-01-26T14:21:50.221Z"
                            }
                        ],
                        "meta": {
                            "total": 3,
                            "limit": 50,
                            "offset": 0
                        }
                    }
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        422: {"description": "Ошибка валидации параметров (неверный тип или значение)"},
    },
)
async def list_users(
    skip: int = Query(
        0, 
        ge=0, 
        description="Смещение для пагинации (сколько записей пропустить)",
        examples=[0, 10, 50]
    ),
    limit: int = Query(
        100, 
        ge=1, 
        le=1000, 
        description="Максимум результатов на странице (от 1 до 1000)",
        examples=[10, 50, 100]
    ),
    sort_by: Optional[SortByField] = Query(
        SortByField.full_name,
        description="Поле для сортировки: `full_name` (ФИО), `email` (email), `created_at` (дата регистрации)",
        examples=["full_name", "email", "created_at"]
    ),
    order: SortOrder = Query(
        SortOrder.asc,
        description="Направление сортировки: `asc` (по возрастанию) или `desc` (по убыванию)",
        examples=["asc", "desc"]
    ),
    role: Optional[str] = Query(
        None,
        description=(
            "Фильтр по роли по имени. Примеры: 'student', 'teacher', 'Администратор'.\n"
            "Если роль не найдена, возвращается пустой список."
        ),
        examples=["student", "teacher", "Администратор"]
    ),
    db: AsyncSession = Depends(get_db),
) -> Page[UserRead]:
    """
    Получить список пользователей с пагинацией, сортировкой и фильтрацией по роли.
    
    **Параметры запроса:**
    - `skip` (int, опционально): Смещение для пагинации. По умолчанию: 0
    - `limit` (int, опционально): Максимум результатов на странице. По умолчанию: 100, максимум: 1000
    - `sort_by` (enum, опционально): Поле для сортировки. По умолчанию: `full_name`
      - `full_name` - сортировка по ФИО
      - `email` - сортировка по email
      - `created_at` - сортировка по дате регистрации
    - `order` (enum, опционально): Направление сортировки. По умолчанию: `asc`
      - `asc` - по возрастанию
      - `desc` - по убыванию
    - `role` (string, опционально): Фильтр по роли по имени. Примеры: 'student', 'teacher'
    
    **Ответ:**
    Возвращает объект `Page[UserRead]` с полями:
    - `items` - массив пользователей
    - `meta` - метаданные пагинации:
      - `total` - общее количество записей (без учета limit/offset)
      - `limit` - лимит на страницу
      - `offset` - смещение
    
    **Примеры запросов:**
    - Получить всех студентов, отсортированных по ФИО: `GET /api/v1/users/?role=student&sort_by=full_name&order=asc`
    - Получить последних зарегистрированных пользователей: `GET /api/v1/users/?sort_by=created_at&order=desc&limit=10`
    - Получить вторую страницу (по 50 записей): `GET /api/v1/users/?skip=50&limit=50`
    """
    logger.info(
        "users.list skip=%s limit=%s sort_by=%s order=%s role=%s",
        skip, limit, sort_by, order, role
    )
    
    # Определяем поле для сортировки
    sort_field = getattr(Users, sort_by.value)
    order_func = asc if order == SortOrder.asc else desc
    order_by = [order_func(sort_field)]
    
    # Получаем данные через сервис
    items, total = await service.list_with_role_filter(
        db,
        role_name=role,
        limit=limit,
        offset=skip,
        order_by=order_by,
    )
    
    logger.debug("users.list -> %d items (total=%d)", len(items), total)
    
    return build_page(items, total=total, limit=limit, offset=skip)


@router.get(
    "/by-tg/{tg_id}",
    response_model=UserID,
    status_code=status.HTTP_200_OK,
    summary="Получить ID пользователя по Telegram ID",
    description=(
        "Найти пользователя по его Telegram ID и вернуть только его ID в системе.\n\n"
        "**Использование:**\n"
        "Полезно для получения внутреннего ID пользователя по его Telegram ID при работе с ботом."
    ),
    responses={
        200: {
            "description": "Пользователь найден",
            "content": {
                "application/json": {
                    "example": {"id": 10}
                }
            }
        },
        404: {
            "description": "Пользователь с указанным Telegram ID не найден",
            "content": {
                "application/json": {
                    "example": {"detail": "User with tg_id=123456789 not found"}
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def get_user_id_by_tg(
    tg_id: int,
    db: AsyncSession = Depends(get_db),
) -> UserID:
    """
    Ищет пользователя по его Telegram ID и возвращает только поле `id`.
    
    **Параметры пути:**
    - `tg_id` (int, обязательный): Telegram ID пользователя
    
    **Ответ:**
    Возвращает объект с полем `id` - внутренний ID пользователя в системе.
    
    **Коды ответов:**
    - `200` - Пользователь найден
    - `404` - Пользователь с указанным Telegram ID не найден
    - `403` - Неверный или отсутствующий API ключ
    """
    user = await service.get_id_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with tg_id={tg_id} not found",
        )
    return {"id": user}

# Переопределяем PATCH эндпойнт для более подробного описания
@router.patch(
    "/{id}",
    response_model=UserRead,
    summary="Частичное обновление пользователя",
    description=(
        "Частично обновить данные пользователя. Можно обновить одно или несколько полей.\n\n"
        "**Особенности:**\n"
        "- Все поля опциональны (частичное обновление)\n"
        "- Обновляются только переданные поля\n"
        "- Поля, которые не переданы, остаются без изменений\n"
        "- Email должен быть уникальным в системе (если обновляется)\n"
        "- Email должен быть валидным (формат проверяется автоматически)\n\n"
        "**Использование:**\n"
        "Используется для редактирования данных студента из интерфейса управления студентами."
    ),
    responses={
        200: {
            "description": "Пользователь успешно обновлен",
            "content": {
                "application/json": {
                    "example": {
                        "id": 13,
                        "email": "newemail@example.com",
                        "full_name": "Обновленное Имя",
                        "tg_id": 987654321,
                        "created_at": "2026-01-26T14:21:50.221Z"
                    }
                }
            }
        },
        404: {
            "description": "Пользователь не найден",
            "content": {
                "application/json": {
                    "example": {"detail": "Not found"}
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        422: {
            "description": "Ошибка валидации данных",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "loc": ["body", "email"],
                                "msg": "value is not a valid email address",
                                "type": "value_error.email"
                            }
                        ]
                    }
                }
            }
        },
    },
)
async def patch_user(
    id: int,
    obj_in: UserUpdate = Body(..., description="Данные для обновления пользователя (все поля опциональны)"),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    """
    Частично обновить данные пользователя.
    
    **Параметры пути:**
    - `id` (int, обязательный): ID пользователя
    
    **Тело запроса:**
    Все поля опциональны. Обновляются только переданные поля:
    - `email` (string, опционально): Email пользователя (валидный email)
    - `full_name` (string, опционально): Полное имя пользователя
    - `tg_id` (int, опционально): Telegram ID пользователя
    
    **Ответ:**
    Возвращает объект `UserRead` с обновленными данными.
    
    **Коды ответов:**
    - `200` - Пользователь успешно обновлен
    - `404` - Пользователь с указанным ID не найден
    - `403` - Неверный или отсутствующий API ключ
    - `422` - Ошибка валидации данных (неверный формат email и т.д.)
    
    **Примеры:**
    - Обновить только имя: `{"full_name": "Новое Имя"}`
    - Обновить email и имя: `{"email": "new@example.com", "full_name": "Обновленное Имя"}`
    - Обновить только Telegram ID: `{"tg_id": 987654321}`
    """
    db_obj = await service.get_by_id(db, id)
    if not db_obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    
    payload = obj_in.model_dump(exclude_unset=True)
    updated = await service.update(db, db_obj, payload)
    return updated

crud_router = create_crud_router(
    prefix="",              # <--- ВАЖНО: пусто, т.к. сам router уже с prefix="/users"
    tags=["users"],
    service=service,        # ваш UsersService(), созданный выше
    create_schema=UserCreate,
    read_schema=UserRead,
    update_schema=UserUpdate,
    pk_type=int,
)
router.include_router(crud_router)