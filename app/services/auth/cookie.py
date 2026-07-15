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

# tsk-224: refresh-cookie. TTL обязан совпадать с `_REFRESH_TTL_DAYS` (30 дней)
# в session_service — источник правды по сроку refresh-токена; здесь дублируется
# как и access-TTL выше (осознанный DRY-компромисс ради изоляции слоя cookie от
# сервиса сессий). При смене `_REFRESH_TTL_DAYS` — синхронизировать это значение.
DEFAULT_REFRESH_MAX_AGE_SECONDS = 30 * 86400  # 30 дней

# Узкий path: браузер шлёт refresh-cookie ТОЛЬКО на сам эндпоинт refresh,
# а не на каждый запрос к API — минимизирует поверхность утечки/CSRF-риска
# (в отличие от access-cookie `session`, которая нужна на всех защищённых роутах).
REFRESH_COOKIE_PATH = "/api/v1/auth/session/refresh"


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


def set_refresh_cookie(
    response: Response,
    refresh_token: str,
    *,
    max_age: int = DEFAULT_REFRESH_MAX_AGE_SECONDS,
    secure: bool = True,
) -> None:
    """Выставить httpOnly refresh-cookie с узким path-скоупом (tsk-224).

    Refresh-токен живёт дольше access (`session`): 30 дней против 24 часов, —
    поэтому web-сессия переживает истечение access-cookie. `httponly` + `secure`
    + `samesite=lax` + узкий `path` держат её вне досягаемости JS и лишних роутов.
    `secure=True` по умолчанию — как и у `set_session_cookie` (на localhost
    браузер считает контекст secure, так что dev-логин не ломается).
    """
    response.set_cookie(
        "refresh", refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=max_age,
        domain=_settings.cookie_domain,
        path=REFRESH_COOKIE_PATH,
    )


def clear_session_cookie(response: Response) -> None:
    """Удалить сессионную cookie (logout)."""
    response.delete_cookie("session", domain=_settings.cookie_domain)


def clear_refresh_cookie(response: Response) -> None:
    """Удалить refresh-cookie (logout). `path` обязан совпадать с `set_refresh_cookie`,
    иначе браузер не сматчит cookie и не удалит её."""
    response.delete_cookie(
        "refresh", domain=_settings.cookie_domain, path=REFRESH_COOKIE_PATH,
    )
