"""Redis-based rate limiter (скользящее окно)."""
import logging
import time

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_pool: aioredis.Redis | None = None


def get_redis(redis_url: str) -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(redis_url, decode_responses=True)
    return _pool


async def is_rate_limited(
    redis: aioredis.Redis,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> bool:
    """
    Проверить лимит запросов (sliding window via sorted set).
    Возвращает True если лимит превышен.
    Fail-open: при недоступности Redis возвращает False и логирует предупреждение.
    """
    now = time.time()
    window_start = now - window_seconds

    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        count: int = results[2]
        return count > max_requests
    except Exception:
        logger.warning("Redis недоступен, rate limiter отключён (fail-open) для ключа: %s", key)
        return False
