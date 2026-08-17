import logging
import os
from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import Settings

logger = logging.getLogger(__name__)

settings = Settings()


def _pool_setting(name: str, default: int) -> int:
    """Прочитать размер пула из окружения, отбросив мусорное значение (tsk-621).

    Опечатка в `.env` не должна ронять запуск сервиса и не должна молча
    превращаться в ноль подключений — в обоих случаях берём значение по
    умолчанию.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r — не число, беру значение по умолчанию %d", name, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%d — меньше единицы, беру значение по умолчанию %d", name, value, default)
        return default
    return value

# tsk-624: сколько секунд запрос ждёт свободное подключение, прежде чем
# сдаться. Значение совпадает с умолчанием SQLAlchemy, но вынесено наружу
# намеренно: от него считается пауза `Retry-After` в ответе 503 при
# исчерпании пула (`app/api/error_handlers.py`). Держать эту связь в коде
# честнее, чем подбирать паузу «на глаз».
DB_POOL_TIMEOUT_SECONDS: int = _pool_setting("DB_POOL_TIMEOUT", 30)

# Асинхронный движок
engine = create_async_engine(
    settings.database_url,
    echo=False,  # SQL-вывод можно включить через LOG_LEVEL=DEBUG
    future=True,
    # tsk-621: пул раньше не настраивался, то есть работал на значениях по
    # умолчанию — 5 постоянных подключений плюс 10 сверх лимита. Пятнадцати
    # мало: кабинет открывает дерево курса десятками параллельных запросов, и
    # любая задержка выстраивает очередь, в которой запросы ждут подключение
    # по 30 секунд и падают с 500.
    #
    # Потолок здесь не «сколько влезет»: сервер БД разрешает 200 подключений
    # ВСЕГО, и этот лимит общий — рядом живут три полигонных сервиса
    # (poligon_dev/stage/test) с тем же кодом. 30 на процесс дают 120 на
    # четверых и оставляют запас на обслуживание и диагностику. Поднять на
    # конкретной установке можно через окружение, не трогая код.
    pool_size=_pool_setting("DB_POOL_SIZE", 10),
    max_overflow=_pool_setting("DB_MAX_OVERFLOW", 20),
    pool_timeout=DB_POOL_TIMEOUT_SECONDS,
    # Подключение могло умереть, пока лежало в пуле (перезапуск БД, разрыв
    # сети). Без проверки это всплывает ошибкой у случайного пользователя.
    pool_pre_ping=True,
    # Не держать подключение дольше получаса: страхует от «протухших» сокетов
    # на промежуточном сетевом оборудовании.
    pool_recycle=1800,
)

# Фабрика сессий
async_session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Зависимость FastAPI: открывает сессию и гарантированно её закрывает.
    """
    async with async_session_factory() as session:
        yield session
