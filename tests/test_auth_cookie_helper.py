"""tsk-161 (Фаза 1): unit-тесты единого cookie-helper'а.

Покрытие:
  1. set_session_cookie_defaults   — httponly/samesite/domain/max_age/secure по умолчанию
  2. set_session_cookie_custom_max_age_and_secure — test_session.py передаёт свои значения
  3. clear_session_cookie          — delete_cookie вызван с тем же domain
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Response

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.services.auth.cookie import (
    DEFAULT_REFRESH_MAX_AGE_SECONDS,
    SESSION_HINT_COOKIE,
    DEFAULT_SESSION_MAX_AGE_SECONDS,
    REFRESH_COOKIE_PATH,
    clear_refresh_cookie,
    clear_session_cookie,
    set_refresh_cookie,
    set_session_cookie,
)


def _parse_set_cookie(response: Response) -> str:
    raw = response.headers.get("set-cookie", "")
    assert raw, "Set-Cookie заголовок отсутствует"
    return raw


def test_set_session_cookie_defaults(monkeypatch):
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    set_session_cookie(response, "test-access-token")
    raw = _parse_set_cookie(response)

    assert "session=test-access-token" in raw
    assert "HttpOnly" in raw
    assert "Secure" in raw  # default secure=True
    assert "samesite=lax" in raw.lower()
    assert f"Max-Age={DEFAULT_SESSION_MAX_AGE_SECONDS}" in raw
    assert "Domain=victor-komlev.ru" in raw


def test_set_session_cookie_custom_max_age_and_secure(monkeypatch):
    """test_session.py передаёт свой TTL и secure=cookie_secure (может быть False в dev)."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    set_session_cookie(response, "tok", max_age=3600, secure=False)
    raw = _parse_set_cookie(response)

    assert "Max-Age=3600" in raw
    assert "Secure" not in raw


def test_clear_session_cookie_uses_same_domain(monkeypatch):
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    clear_session_cookie(response)
    raw = _parse_set_cookie(response)

    assert "session=" in raw
    assert "Domain=victor-komlev.ru" in raw
    # delete_cookie ставит истёкшую дату / Max-Age=0
    assert "Max-Age=0" in raw or "expires=" in raw.lower()


# ── tsk-224: refresh-cookie ───────────────────────────────────────────────────

def test_set_refresh_cookie_defaults(monkeypatch):
    """refresh-cookie: httpOnly + Secure + samesite=lax + узкий path + 30д TTL."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    set_refresh_cookie(response, "test-refresh-token")
    raw = _parse_set_cookie(response)

    assert "refresh=test-refresh-token" in raw
    assert "HttpOnly" in raw
    assert "Secure" in raw  # default secure=True
    assert "samesite=lax" in raw.lower()
    assert f"Max-Age={DEFAULT_REFRESH_MAX_AGE_SECONDS}" in raw
    assert "Domain=victor-komlev.ru" in raw
    # Узкий path — cookie шлётся только на сам эндпоинт refresh.
    assert f"Path={REFRESH_COOKIE_PATH}" in raw


def test_set_refresh_cookie_ttl_is_30_days():
    """TTL refresh-cookie строго 30 дней (переживает истечение access 24ч)."""
    assert DEFAULT_REFRESH_MAX_AGE_SECONDS == 30 * 86400


def test_set_refresh_cookie_custom_secure_false(monkeypatch):
    """secure=False (dev over HTTP) — Secure-флаг отсутствует."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", None)

    response = Response()
    set_refresh_cookie(response, "tok", secure=False)
    raw = _parse_set_cookie(response)

    assert "refresh=tok" in raw
    assert "Secure" not in raw


def test_clear_refresh_cookie_matches_path(monkeypatch):
    """clear обязан ставить тот же path, иначе браузер не удалит cookie."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    clear_refresh_cookie(response)
    raw = _parse_set_cookie(response)

    assert "refresh=" in raw
    assert f"Path={REFRESH_COOKIE_PATH}" in raw
    assert "Domain=victor-komlev.ru" in raw
    assert "Max-Age=0" in raw or "expires=" in raw.lower()


# ---------------------------------------------------------------------------
# tsk-755: метка «у этого браузера была наша сессия».
# ---------------------------------------------------------------------------

def _all_set_cookies(response: Response) -> list[str]:
    """Все Set-Cookie ответа: один вызов теперь ставит две cookie."""
    return [
        value.decode()
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    ]


def test_hint_cookie_is_set_next_to_refresh(monkeypatch):
    """Метка ставится тем же ответом, что и refresh-токен, и живёт столько же."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    set_refresh_cookie(response, "tok")
    hint = [c for c in _all_set_cookies(response) if c.startswith(f"{SESSION_HINT_COOKIE}=")]

    assert len(hint) == 1, "метка должна ставиться ровно один раз"
    raw = hint[0]
    assert f"{SESSION_HINT_COOKIE}=1" in raw
    assert f"Max-Age={DEFAULT_REFRESH_MAX_AGE_SECONDS}" in raw
    assert "Path=/;" in raw or raw.rstrip().endswith("Path=/")
    assert "HttpOnly" in raw and "Secure" in raw


def test_hint_cookie_carries_no_secret(monkeypatch):
    """В метке нет ни токена, ни идентификатора — только «был вход»."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", None)

    response = Response()
    set_refresh_cookie(response, "super-secret-refresh-token")
    hint = [c for c in _all_set_cookies(response) if c.startswith(f"{SESSION_HINT_COOKIE}=")][0]

    assert "super-secret-refresh-token" not in hint


def test_clear_refresh_cookie_also_clears_hint(monkeypatch):
    """При выходе метка снимается — иначе кабинет пробовал бы продлить отозванное."""
    from app.services.auth import cookie as cookie_module
    monkeypatch.setattr(cookie_module._settings, "cookie_domain", "victor-komlev.ru")

    response = Response()
    clear_refresh_cookie(response)
    hint = [c for c in _all_set_cookies(response) if c.startswith(f"{SESSION_HINT_COOKIE}=")]

    assert len(hint) == 1
    assert "Max-Age=0" in hint[0] or "expires=" in hint[0].lower()
