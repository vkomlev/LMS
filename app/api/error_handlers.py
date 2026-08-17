"""Ответ сервиса на перегрузку: 503 вместо 500 (tsk-624).

Разбор аварии 17.08.2026 (`reviews/2026-08-17-prod-pool-exhaustion-diagnosis.md`):
когда все подключения к базе заняты, запрос ждёт свободное подключение
`pool_timeout` секунд и падает. До этой правки такой отказ доходил до клиента
как 500 — «ошибка на нашей стороне без объяснений». Кабинеты и боты на 500
отвечают немедленными повторами, повторы ложатся в тот же затор, и волна
затягивается: из 320 отказов заметная часть была именно повторами.

503 с заголовком `Retry-After` говорит другое: «живы, но заняты, приди через
N секунд». Это стандартный ответ на перегрузку, и клиент, который его
понимает, отступает вместо долбления.

Отличаем перегрузку от прочих отказов базы **по типу исключения**, а не по
тексту сообщения: `sqlalchemy.exc.TimeoutError` возбуждается ровно в одном
месте — когда пул не дождался свободного подключения. Это отдельный класс,
он НЕ наследует встроенный `TimeoutError`, поэтому обычные таймауты (сетевые,
`asyncio`) сюда не попадают. Остальные ошибки базы по-прежнему уходят в общий
обработчик и отдают 500.
"""
from __future__ import annotations

import logging
import random

from fastapi import Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeout

from app.db.session import DB_POOL_TIMEOUT_SECONDS

logger = logging.getLogger("api.error_handlers")

#: Текст для клиента. Внутреннюю причину (какой именно ресурс кончился)
#: наружу не выносим — она есть в логе.
_OVERLOAD_DETAIL = "Сервис временно перегружен. Повторите запрос позже."


def retry_after_seconds(pool_timeout: float = DB_POOL_TIMEOUT_SECONDS) -> int:
    """Сколько секунд просить клиента подождать перед повтором.

    Отсчёт идёт от времени ожидания свободного подключения, а не от круглого
    числа «на глаз»: запрос, получивший этот ответ, уже прождал `pool_timeout`
    секунд, значит затор в разгаре и возвращаться через секунду бессмысленно.

    Нижняя граница — половина ожидания, верхняя — полное ожидание. Разброс
    внутри этого окна обязателен: без него все клиенты, получившие отказ в
    одну секунду, вернутся тоже в одну секунду и устроят вторую волну
    (клиентов у нас четыре бота плюс кабинеты, повторы у них синхронные).

    :param pool_timeout: время ожидания свободного подключения, секунды.
    :return: пауза в секундах, не меньше 1.
    """
    base = max(1, round(pool_timeout / 2))
    return random.randint(base, base * 2)


async def db_pool_timeout_handler(
    request: Request,
    exc: SQLAlchemyPoolTimeout,
) -> JSONResponse:
    """Отдать 503 с `Retry-After`, когда не осталось свободных подключений.

    В лог пишем текст исключения целиком — в нём есть подстрока `QueuePool`,
    по которой в разборе аварии считали масштаб и время волны
    (`grep -c QueuePool /opt/lms/logs/app.log`). Этот след трогать нельзя:
    следующая диагностика пойдёт по нему же.
    """
    retry_after = retry_after_seconds()
    logger.error(
        "db pool exhausted: %s | path=%s method=%s retry_after=%ds",
        exc,
        request.url.path,
        request.method,
        retry_after,
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "service_unavailable",
            "detail": _OVERLOAD_DETAIL,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )
