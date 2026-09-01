"""Integration HTTP-тесты PUT /api/v1/me/timezone/auto — автозахват пояса (tsk-588).

Покрывает правило приоритета из решения оператора 2026-08-08: пояс, снятый с
браузера, заполняет пустое значение и обновляет своё же прежнее, но НЕ трогает
выбор человека.

- пустой пояс → записывается, источник `auto`;
- тот же пояс повторно → записи нет (`applied=false`), эндпоинт идемпотентен;
- сменился пояс устройства → записанное значение НЕ трогается (tsk-753);
- пояс вписан человеком (PATCH /me) → автозахват не трогает, `applied=false`;
- некорректный IANA-идентификатор → 422;
- техническое имя (`Etc/GMT-3`, `UTC`) → 422 (tsk-753);
- без авторизации → 401.

Образец подъёма user+session — как в test_tsk427_profile_extra_fields.py.
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

AUTO_URL = "/api/v1/me/timezone/auto"


async def _setup_user_with_session(db):
    email = f"tsk588_{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name="Иванов Иван", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token


async def _cleanup(db, user_id: int) -> None:
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


async def _read_timezone(db, user_id: int) -> tuple[str | None, str | None]:
    row = (
        await db.execute(
            text("SELECT timezone, timezone_source FROM users WHERE id=:u"),
            {"u": user_id},
        )
    ).one()
    return row[0], row[1]


# ── пустой пояс заполняется автоматически ────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_timezone_fills_empty_profile(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.put(
            AUTO_URL,
            json={"timezone": "Asia/Yekaterinburg"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {
            "timezone": "Asia/Yekaterinburg",
            "source": "auto",
            "applied": True,
        }

        await db.commit()  # увидеть запись, сделанную в сессии эндпоинта
        assert await _read_timezone(db, user_id) == ("Asia/Yekaterinburg", "auto")

        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["timezone"] == "Asia/Yekaterinburg"
        assert me.json()["timezone_source"] == "auto"
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_auto_timezone_is_idempotent(db, client):
    """Повторный вызов с тем же поясом ничего не пишет — applied=false."""
    user_id, token = await _setup_user_with_session(db)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.put(AUTO_URL, json={"timezone": "Europe/Moscow"}, headers=headers)
        assert first.json()["applied"] is True

        second = await client.put(AUTO_URL, json={"timezone": "Europe/Moscow"}, headers=headers)
        assert second.status_code == 200, second.text
        assert second.json() == {
            "timezone": "Europe/Moscow",
            "source": "auto",
            "applied": False,
        }
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_auto_timezone_does_not_overwrite_recorded_value(db, client):
    """tsk-753: другое устройство НЕ переписывает уже записанный пояс.

    Прежнее поведение (автозахват шёл за устройством) и было дефектом: вход с
    рабочего или чужого компьютера молча уводил пояс, а человек узнавал об этом
    из подсказки «(у вас 16:00)» рядом со временем занятия. Что делать с
    расхождением, решает человек в кабинете — его ответ приходит `PATCH /me`.
    """
    user_id, token = await _setup_user_with_session(db)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        await client.put(AUTO_URL, json={"timezone": "Europe/Moscow"}, headers=headers)

        moved = await client.put(
            AUTO_URL, json={"timezone": "Asia/Novosibirsk"}, headers=headers
        )
        assert moved.status_code == 200, moved.text
        assert moved.json() == {
            "timezone": "Europe/Moscow",
            "source": "auto",
            "applied": False,
        }

        await db.commit()
        assert await _read_timezone(db, user_id) == ("Europe/Moscow", "auto")
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_manual_answer_replaces_auto_value(db, client):
    """Ответ человека на расхождение проходит: `PATCH /me` меняет пояс и источник."""
    user_id, token = await _setup_user_with_session(db)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        await client.put(AUTO_URL, json={"timezone": "Asia/Yekaterinburg"}, headers=headers)

        answered = await client.patch(
            "/api/v1/me", json={"timezone": "Asia/Krasnoyarsk"}, headers=headers
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["timezone"] == "Asia/Krasnoyarsk"
        assert answered.json()["timezone_source"] == "manual"

        await db.commit()
        assert await _read_timezone(db, user_id) == ("Asia/Krasnoyarsk", "manual")
    finally:
        await _cleanup(db, user_id)


# ── ручной выбор сильнее автозахвата ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_timezone_is_not_overwritten(db, client):
    """Человек выбрал пояс в профиле — браузер его не перебивает (решение 2026-08-08)."""
    user_id, token = await _setup_user_with_session(db)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        manual = await client.patch(
            "/api/v1/me", json={"timezone": "Asia/Yekaterinburg"}, headers=headers
        )
        assert manual.status_code == 200, manual.text
        assert manual.json()["timezone_source"] == "manual"

        auto = await client.put(
            AUTO_URL, json={"timezone": "Europe/Moscow"}, headers=headers
        )
        assert auto.status_code == 200, auto.text
        assert auto.json() == {
            "timezone": "Asia/Yekaterinburg",
            "source": "manual",
            "applied": False,
        }

        await db.commit()
        assert await _read_timezone(db, user_id) == ("Asia/Yekaterinburg", "manual")
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_manual_edit_wins_over_earlier_auto_value(db, client):
    """Ручная правка поверх автоматического значения переводит его в manual."""
    user_id, token = await _setup_user_with_session(db)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        await client.put(AUTO_URL, json={"timezone": "Europe/Moscow"}, headers=headers)

        manual = await client.patch(
            "/api/v1/me", json={"timezone": "Asia/Yekaterinburg"}, headers=headers
        )
        assert manual.status_code == 200, manual.text
        assert manual.json()["timezone_source"] == "manual"

        again = await client.put(
            AUTO_URL, json={"timezone": "Europe/Moscow"}, headers=headers
        )
        assert again.json()["applied"] is False
        assert again.json()["timezone"] == "Asia/Yekaterinburg"
    finally:
        await _cleanup(db, user_id)


# ── валидация и доступ ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_timezone_rejects_garbage(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.put(
            AUTO_URL,
            json={"timezone": "Moscow/NotAZone"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["Etc/GMT-3", "UTC", "Factory"])
async def test_auto_timezone_rejects_technical_names(db, client, value):
    """tsk-753: `Etc/GMT-3` знает и ZoneInfo, но как «пояс человека» он ловушка.

    В POSIX знак у этих имён обратный (`Etc/GMT-3` — это UTC+3), и первый же,
    кто сверит значение глазами, прочтёт его наоборот. У одного ученика школы
    так и было записано.
    """
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.put(
            AUTO_URL,
            json={"timezone": value},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text

        patched = await client.patch(
            "/api/v1/me",
            json={"timezone": value},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert patched.status_code == 422, patched.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_auto_timezone_requires_auth(client):
    resp = await client.put(AUTO_URL, json={"timezone": "Europe/Moscow"})
    assert resp.status_code == 401, resp.text
