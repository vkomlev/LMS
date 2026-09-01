"""tsk-741 фаза 1: вопрос ученику «в каком ты классе».

Покрывает то, из-за чего вопрос может оказаться бесполезным или назойливым:

- флаг `school_grade_pending` в `GET /me` — кому горит, кому молчит;
- аудитория: выпускник (`alumni`) и демо (`demo`) вопроса не видят, не-ученик
  тоже; тестовый тариф (`test`) видит — на нём проверяют кабинет;
- не-школьника не спрашиваем: категория уже стоит и она не школьная;
- ответ гасит вопрос — и в самом ответе `PATCH /me`, а не только при следующей
  загрузке профиля (иначе вопрос повисит до перезагрузки страницы);
- отказ гасит вопрос, идемпотентен и переживает перезаход (серверная отметка,
  не браузерная);
- отказ НЕ метит того, кто уже ответил: отказавшихся оператор добирает лично,
  и смешать их с ответившими нельзя.

Образец подъёма ученика с тарифом — как в test_schedule_preference_tsk674.py.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


# ============================== Helpers ==============================


async def _student_with_session(db, *, role: str | None = "student") -> tuple[int, str]:
    """Ученик с сессией. `role=None` — человек без роли ученика."""
    email = f"tsk741-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name="Иванов Иван", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    if role:
        role_id = (
            await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "r": role_id},
        )
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token


async def _assign_plan(db, student_id: int, code: str) -> None:
    """Дать ученику действующий тариф. Планы сетки уже есть в базе (tsk-301)."""
    plan_id = (
        await db.execute(
            text("SELECT id FROM subscription_plan WHERE code = :c"), {"c": code}
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "VALUES (:s, :p, CURRENT_DATE)"
        ),
        {"s": student_id, "p": plan_id},
    )
    await db.commit()


async def _cleanup(db, user_id: int) -> None:
    await db.execute(
        text("DELETE FROM student_subscription WHERE student_id=:u"), {"u": user_id}
    )
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _pending(client, token: str) -> bool:
    resp = await client.get("/api/v1/me", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["school_grade_pending"]


# ============================== Кому горит ==============================


@pytest.mark.asyncio
async def test_pending_for_student_without_grade(db, client):
    """Ученик без класса и без категории — тот самый, ради кого всё затевалось."""
    user_id, token = await _student_with_session(db)
    try:
        assert await _pending(client, token) is True
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_not_pending_for_non_student(db, client):
    """Человек без роли ученика в аудиторию не входит."""
    user_id, token = await _student_with_session(db, role=None)
    try:
        assert await _pending(client, token) is False
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_code", ["alumni", "demo"])
async def test_not_pending_for_excluded_plans(db, client, plan_code):
    """Выпускник отучился, демо ещё не ученик — вопрос не их."""
    user_id, token = await _student_with_session(db)
    try:
        await _assign_plan(db, user_id, plan_code)
        assert await _pending(client, token) is False
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_pending_for_test_plan(db, client):
    """Тестовая учётка вопрос видит — иначе кабинет не на чем проверять (tsk-712)."""
    user_id, token = await _student_with_session(db)
    try:
        await _assign_plan(db, user_id, "test")
        assert await _pending(client, token) is True
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category", ["university_student", "college_student", "applicant", "adult"]
)
async def test_not_pending_for_non_school_category(db, client, category):
    """Категория уже не школьная — «в каком ты классе» читалось бы как поломка."""
    user_id, token = await _student_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me", json={"category": category}, headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        # Тот же ответ PATCH уже гасит вопрос, без второго запроса.
        assert resp.json()["school_grade_pending"] is False
        assert await _pending(client, token) is False
    finally:
        await _cleanup(db, user_id)


# ============================== Ответ гасит ==============================


@pytest.mark.asyncio
async def test_answer_closes_question_in_patch_response(db, client):
    """Ответ «11 класс» гасит вопрос прямо в ответе PATCH.

    Клиент кладёт ответ PATCH в кэш профиля: посчитай флаг только в GET — и
    вопрос повисел бы до следующей загрузки страницы (ровно та ошибка, на
    которой обожглись tsk-674 и tsk-744).
    """
    user_id, token = await _student_with_session(db)
    try:
        assert await _pending(client, token) is True
        resp = await client.patch(
            "/api/v1/me",
            json={"category": "school_student", "school_grade": 11},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["school_grade"] == 11
        assert resp.json()["school_grade_pending"] is False
        assert await _pending(client, token) is False
    finally:
        await _cleanup(db, user_id)


# ============================== Отказ гасит ==============================


@pytest.mark.asyncio
async def test_decline_closes_question_and_survives_reload(db, client):
    """Отказ закрывает вопрос насовсем и хранится на сервере, а не в браузере."""
    user_id, token = await _student_with_session(db)
    try:
        assert await _pending(client, token) is True

        resp = await client.post("/api/v1/me/school-grade/decline", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["school_grade_pending"] is False

        # Отметка именно в базе: следующий заход (в том числе с другого
        # устройства) вопроса уже не увидит.
        declined_at = (
            await db.execute(
                text("SELECT school_grade_declined_at FROM users WHERE id=:u"),
                {"u": user_id},
            )
        ).scalar_one()
        assert declined_at is not None
        assert await _pending(client, token) is False
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_decline_is_idempotent(db, client):
    """Повторный отказ не сдвигает дату: помним первую попытку."""
    user_id, token = await _student_with_session(db)
    try:
        await client.post("/api/v1/me/school-grade/decline", headers=_auth(token))
        first = (
            await db.execute(
                text("SELECT school_grade_declined_at FROM users WHERE id=:u"),
                {"u": user_id},
            )
        ).scalar_one()

        resp = await client.post("/api/v1/me/school-grade/decline", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        second = (
            await db.execute(
                text("SELECT school_grade_declined_at FROM users WHERE id=:u"),
                {"u": user_id},
            )
        ).scalar_one()
        assert first == second
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_decline_does_not_mark_the_one_who_answered(db, client):
    """Ответивший в молчуны не попадает — оператор добирает лично только их."""
    user_id, token = await _student_with_session(db)
    try:
        answer = await client.patch(
            "/api/v1/me",
            json={"category": "school_student", "school_grade": 10},
            headers=_auth(token),
        )
        assert answer.status_code == 200, answer.text

        resp = await client.post("/api/v1/me/school-grade/decline", headers=_auth(token))
        assert resp.status_code == 200, resp.text

        row = (
            await db.execute(
                text(
                    "SELECT school_grade, school_grade_declined_at "
                    "FROM users WHERE id=:u"
                ),
                {"u": user_id},
            )
        ).one()
        assert row.school_grade == 10
        assert row.school_grade_declined_at is None
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_decline_reason_lands_in_audit_log(db, client):
    """«Я не школьник» отличимо от «не сейчас» — по журналу, не по профилю.

    В категорию такой ответ не пишется: среди не-школьников есть и студенты, и
    абитуриенты, и выдуманное «взрослый» испортило бы карточку.
    """
    user_id, token = await _student_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/me/school-grade/decline",
            json={"reason": "not_school"},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text

        details = (
            await db.execute(
                text(
                    "SELECT details FROM audit_event "
                    " WHERE user_id=:u "
                    "   AND event_type='user.profile.school_grade_declined' "
                    " ORDER BY id DESC LIMIT 1"
                ),
                {"u": user_id},
            )
        ).scalar_one()
        assert details["reason"] == "not_school"

        category = (
            await db.execute(
                text("SELECT category FROM users WHERE id=:u"), {"u": user_id}
            )
        ).scalar_one()
        assert category is None
    finally:
        # audit_event не чистим: UPDATE/DELETE там запрещены триггером — журнал
        # событий на то и журнал.
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_decline_requires_auth(client):
    """Без токена — 401, отметка чужого отказа невозможна."""
    resp = await client.post("/api/v1/me/school-grade/decline")
    assert resp.status_code == 401, resp.text
