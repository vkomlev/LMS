"""Единая точка выставления/удаления сессионной cookie (tsk-161, Фаза 1).

До рефакторинга `response.set_cookie("session", ...)` дублировался идентично
в 5 auth-эндпоинтах (session.py, vk.py, tg.py, magic_link.py, test_session.py)
— риск рассинхронизации при будущей смене cookie-схемы (Фаза 2 плана tsk-161:
переход на host-only cookie вместо widescoped `Domain=victor-komlev.ru`).

Поведение сохранено байт-в-байт: `secure`/`max_age` параметризованы с теми же
значениями по умолчанию, что были у каждого из 5 мест раньше (см. git-историю
`session.py`/`vk.py`/`tg.py`/`magic_link.py`/`test_session.py` до этого коммита).
"""
from __future__ import annotations

from fastapi import Response

from app.core.config import Settings

_settings = Settings()

# Y-5.2: max_age 24ч — было захардкожено одинаково в 4 из 5 мест.
DEFAULT_SESSION_MAX_AGE_SECONDS = 86400


def set_session_cookie(
    response: Response,
    access_token: str,
    *,
    max_age: int = DEFAULT_SESSION_MAX_AGE_SECONDS,
    secure: bool = True,
) -> None:
    """Выставить сессионную cookie с едиными параметрами безопасности.

    `secure=True` по умолчанию — сохраняет прежнее поведение session.py/
    vk.py/tg.py/magic_link.py (было захардкожено). `test_session.py`
    передаёт `secure=_settings.cookie_secure` явно — там это было
    параметризовано ещё до рефакторинга, поведение сохранено как есть.
    """
    response.set_cookie(
        "session", access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=max_age,
        domain=_settings.cookie_domain,
    )


def clear_session_cookie(response: Response) -> None:
    """Удалить сессионную cookie (logout)."""
    response.delete_cookie("session", domain=_settings.cookie_domain)
