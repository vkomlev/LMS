"""tsk-1440: недоступный Valkey не должен вешать запросы дольше таймаута сокета."""
import time

import pytest

from app.services import rate_limit_service


@pytest.mark.asyncio
async def test_unreachable_redis_fails_open_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Немаршрутизируемый адрес (SYN без ответа) → fail-open за секунды, не минуты."""
    monkeypatch.setattr(rate_limit_service, "_pool", None)
    redis = rate_limit_service.get_redis("redis://10.255.255.1:6379/2")
    started = time.monotonic()
    limited = await rate_limit_service.is_rate_limited(redis, "tsk1440", 5, 60)
    elapsed = time.monotonic() - started
    monkeypatch.setattr(rate_limit_service, "_pool", None)
    assert limited is False
    assert elapsed < 5
