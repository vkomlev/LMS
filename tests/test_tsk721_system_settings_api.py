"""tsk-721: раздел настроек школы через HTTP — гейт, границы, след, применение.

Работает с dev-БД (Learn.public), как и соседние интеграционные тесты. За
собой убирает: значение, сохранённое проверкой, снимается тем же способом,
каким его снимает кнопка «вернуть как было».
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.main import app
from app.auth.current_user import CurrentUser
from app.core import settings_store

pytestmark = pytest.mark.asyncio

_ADMIN_ID = 2       # единственная учётка с ролью admin в dev-БД
_STUDENT_ID = 3     # роль student — раздел видеть не должен

_KEY = "lesson_idle_threshold_minutes"


@pytest_asyncio.fixture
async def clean_setting(db):
    """Начинаем и заканчиваем без сохранённого значения по проверяемому ключу."""
    async def _wipe():
        await db.execute(text("DELETE FROM system_setting WHERE key = :k"), {"k": _KEY})
        await db.commit()
        settings_store.forget_local(_KEY)

    await _wipe()
    yield
    await _wipe()


def _as(user_id: int):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, is_service=False
    )


def _logout():
    app.dependency_overrides.pop(get_current_user, None)


async def test_ученик_раздел_настроек_не_видит(client):
    _as(_STUDENT_ID)
    try:
        resp = await client.get("/api/v1/system-settings")
    finally:
        _logout()
    assert resp.status_code == 403, resp.text


async def test_админ_видит_настройки_с_русскими_именами(client):
    _as(_ADMIN_ID)
    try:
        resp = await client.get("/api/v1/system-settings")
    finally:
        _logout()

    assert resp.status_code == 200, resp.text
    groups = resp.json()["groups"]
    assert groups, "раздел не должен быть пустым"

    items = [item for g in groups for item in g["items"]]
    keys = {item["key"] for item in items}
    assert _KEY in keys

    idle = next(item for item in items if item["key"] == _KEY)
    assert idle["title"] == (
        "Через сколько минут молчания на занятии звать преподавателя"
    )
    assert idle["unit"] == "минуты"
    assert idle["min_value"] == 3 and idle["max_value"] == 60
    assert idle["description"]

    # Прямой запрет задачи: ключей доступа в разделе быть не может.
    forbidden = {"secret", "token", "password", "api_key", "database_url"}
    for key in keys:
        assert not (set(key.lower().split("_")) & forbidden), key


async def test_значение_за_границей_отклонено_понятным_текстом(client, clean_setting):
    _as(_ADMIN_ID)
    try:
        resp = await client.put(f"/api/v1/system-settings/{_KEY}", json={"value": 0})
    finally:
        _logout()

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "не меньше" in detail and "минуты" in detail, detail
    # Отказ ничего не сохранил: настройка осталась прежней.
    assert settings_store.source(_KEY) != "cabinet"


async def test_правка_действует_сразу_и_оставляет_след(client, db, clean_setting):
    before = settings_store.get_int(_KEY)

    _as(_ADMIN_ID)
    try:
        resp = await client.put(f"/api/v1/system-settings/{_KEY}", json={"value": 25})
    finally:
        _logout()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["value"] == 25
    assert body["source"] == "cabinet"
    assert body["updated_by"] == _ADMIN_ID

    # Главное обязательство задачи: новое значение действует без перезапуска.
    assert settings_store.get_int(_KEY) == 25

    # И оно доехало до места применения, а не осталось в кабинете.
    from app.services import lesson_idle_cron_service  # noqa: PLC0415

    assert lesson_idle_cron_service.settings_store.get_int(_KEY) == 25

    # След: кто, что и со скольки на сколько.
    row = (
        await db.execute(
            text(
                """
                SELECT user_id, details
                  FROM audit_event
                 WHERE event_type = 'admin.setting.changed'
                   AND details->>'key' = :k
                 ORDER BY id DESC LIMIT 1
                """
            ),
            {"k": _KEY},
        )
    ).first()
    assert row is not None, "правка настройки обязана оставлять запись в журнале"
    user_id, details = row
    assert user_id == _ADMIN_ID
    assert int(details["new_value"]) == 25
    assert int(details["old_value"]) == before


async def test_вернуть_как_было_снимает_выбор(client, db, clean_setting):
    _as(_ADMIN_ID)
    try:
        await client.put(f"/api/v1/system-settings/{_KEY}", json={"value": 25})
        assert settings_store.get_int(_KEY) == 25

        resp = await client.delete(f"/api/v1/system-settings/{_KEY}")
    finally:
        _logout()

    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] != "cabinet"
    assert settings_store.source(_KEY) != "cabinet"

    left = (
        await db.execute(
            text("SELECT count(*) FROM system_setting WHERE key = :k"), {"k": _KEY}
        )
    ).scalar()
    assert left == 0, "сброс удаляет строку, а не переписывает её прежним числом"


async def test_неизвестный_ключ_даёт_404(client):
    _as(_ADMIN_ID)
    try:
        resp = await client.put(
            "/api/v1/system-settings/s3_secret_key", json={"value": "х"}
        )
    finally:
        _logout()
    assert resp.status_code == 404, resp.text
