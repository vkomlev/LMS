"""tsk-755: раздел «попытки входа на ничьи адреса» — доступ и содержимое.

Работает с dev-БД (Learn.public), как и соседние интеграционные тесты.
Записи журнала за собой не убирает: `audit_event` append-only по построению,
UPDATE/DELETE там запрещены триггером. Адреса проверки помечены случайной
строкой, чтобы не смешиваться с настоящими.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.main import app
from app.auth.current_user import CurrentUser

pytestmark = pytest.mark.asyncio

_ADMIN_ID = 2       # единственная учётка с ролью admin в dev-БД
_STUDENT_ID = 142   # только роль student (у учётки 3 в dev-БД есть teacher)

_MARK = os.urandom(4).hex()
_TYPO_EMAIL = f"arttur{_MARK}@example.com"
_KNOWN_EMAIL = f"regular{_MARK}@example.com"


def _as(user_id: int):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, is_service=False
    )


def _logout():
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def journal_rows(db):
    """Две попытки на ничей адрес и одна на известный."""
    for known in ("false", "false", "true"):
        email = _KNOWN_EMAIL if known == "true" else _TYPO_EMAIL
        await db.execute(
            text(
                "INSERT INTO audit_event (event_type, ip, details) "
                "VALUES ('magic_link_sent', '127.0.0.1', "
                "CAST(:d AS jsonb))"
            ),
            {"d": f'{{"email": "{email}", "link_mode": false, '
                   f'"recipient_known": {known}}}'},
        )
    await db.commit()
    yield
    # Убирать за собой нечем и не нужно: журнал append-only (UPDATE/DELETE
    # запрещены триггером — это его смысл). Адреса помечены случайной строкой,
    # поэтому строки проверки ни с чьими не путаются.


async def test_ученик_раздел_не_видит(client, journal_rows):
    _as(_STUDENT_ID)
    try:
        resp = await client.get("/api/v1/auth/signals/unknown-recipients")
    finally:
        _logout()
    assert resp.status_code == 403, resp.text


async def test_персонал_видит_адрес_целиком_и_число_попыток(client, journal_rows):
    _as(_ADMIN_ID)
    try:
        resp = await client.get("/api/v1/auth/signals/unknown-recipients?days=1")
    finally:
        _logout()

    assert resp.status_code == 200, resp.text
    items = {row["email"]: row for row in resp.json()["items"]}
    assert _TYPO_EMAIL in items, "попытка на ничей адрес должна быть видна"
    assert items[_TYPO_EMAIL]["attempts"] == 2, "повторы схлопнуты в одну строку"
    assert _KNOWN_EMAIL not in items, "свой ученик в этот список не попадает"


async def test_окно_ограничивает_выборку(client, journal_rows):
    """Слишком широкое окно отклоняется — иначе выборка растёт неограниченно."""
    _as(_ADMIN_ID)
    try:
        resp = await client.get("/api/v1/auth/signals/unknown-recipients?days=365")
    finally:
        _logout()
    assert resp.status_code == 422
