"""tsk-498 (родительская ссылка на дашборд без регистрации).

Проверяем на НАСТОЯЩЕЙ БД (не на моках), по образцу
test_tsk478_parent_portal.py.

Покрывает:
- Выдача/список/отзыв: только methodist/admin (403 остальным), несколько
  активных ссылок на одного ученика, идемпотентный отзыв.
- Публичный дашборд по токену: работает БЕЗ единого auth-заголовка (в этом
  весь смысл задачи), работает без параметров периода («голая» ссылка),
  отозванный/несуществующий/кривой токен → 404 (неотличимы).
- Ссылка привязана к СВОЕМУ ученику (токен ученика А не показывает Б).
- Токен не утекает в список ссылок (в базе только хеш).
- Минимизация данных: сырой JSON без solution_rules/переписки.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk498"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": title},
        )
    ).scalar()


async def _enroll_student(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _new_task(db, *, course_id: int, uid: str) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — граф не собрать"
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} условие {uid}"}),
                "sr": json.dumps({"max_score": 10, "accepted_answers": [f"{_TAG}-secret-{uid}"]}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


async def _insert_help_request(
    db, *, student_id: int, task_id: int, course_id: int, teacher_id: int
) -> None:
    await db.execute(
        text(
            "INSERT INTO help_requests "
            "(status, request_type, student_id, task_id, course_id, assigned_teacher_id, "
            " message, created_at, updated_at) "
            "VALUES ('open', 'manual_help', :s, :t, :c, :teach, :msg, now(), now())"
        ),
        {
            "s": student_id, "t": task_id, "c": course_id, "teach": teacher_id,
            "msg": f"{_TAG} секретный текст переписки",
        },
    )
    await db.commit()


async def _create_link(client, *, admin_token: str, student_id: int, label: str | None = None):
    return await client.post(
        f"/api/v1/students/{student_id}/access-links",
        json={"label": label},
        headers={"Authorization": f"Bearer {admin_token}"},
    )


# ============================== Выдача: гейт ролей ==============================


@pytest.mark.asyncio
async def test_create_link_forbidden_for_non_privileged_roles(db, client):
    student_id, student_token = await _new_user(db, role="student", name="stud")
    _teacher_id, teacher_token = await _new_user(db, role="teacher", name="teach")
    _parent_id, parent_token = await _new_user(db, role="parent", name="parent")

    for token in (student_token, teacher_token, parent_token):
        resp = await _create_link(client, admin_token=token, student_id=student_id)
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_create_link_by_methodist_returns_token_and_url_once(db, client):
    _methodist_id, methodist_token = await _new_user(db, role="methodist", name="meth")
    student_id, _ = await _new_user(db, role="student", name="stud")

    resp = await _create_link(
        client, admin_token=methodist_token, student_id=student_id, label="мама"
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["student_id"] == student_id
    assert body["label"] == "мама"
    assert body["is_active"] is True
    assert body["revoked_at"] is None
    assert len(body["token"]) == 64  # 32 байта в hex
    assert body["url"].endswith(f"/p/{body['token']}")

    # В списке ссылок самого токена быть не должно — в базе только хеш.
    list_resp = await client.get(
        f"/api/v1/students/{student_id}/access-links",
        headers={"Authorization": f"Bearer {methodist_token}"},
    )
    assert list_resp.status_code == 200, list_resp.text
    assert body["token"] not in list_resp.text
    assert [x["id"] for x in list_resp.json()] == [body["id"]]


@pytest.mark.asyncio
async def test_create_link_404_for_unknown_student(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    resp = await _create_link(client, admin_token=admin_token, student_id=999_999_999)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_multiple_active_links_per_student(db, client):
    """Решение оператора: маме и папе — разные ссылки, отзываются независимо."""
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")

    mom = (await _create_link(client, admin_token=admin_token, student_id=student_id, label="мама")).json()
    dad = (await _create_link(client, admin_token=admin_token, student_id=student_id, label="папа")).json()
    assert mom["token"] != dad["token"]

    revoke = await client.delete(
        f"/api/v1/parent-access-links/{mom['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revoke.status_code == 200, revoke.text

    # Мамина погашена, папина продолжает работать.
    assert (await client.get(f"/api/v1/public/parent-dashboard/{mom['token']}")).status_code == 404
    assert (await client.get(f"/api/v1/public/parent-dashboard/{dad['token']}")).status_code == 200


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_keeps_first_time(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")
    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()

    first = await client.delete(
        f"/api/v1/parent-access-links/{link['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["is_active"] is False
    revoked_at = first.json()["revoked_at"]

    second = await client.delete(
        f"/api/v1/parent-access-links/{link['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["revoked_at"] == revoked_at


# ============================== Публичный дашборд по ссылке ==============================


@pytest.mark.asyncio
async def test_public_dashboard_opens_without_any_auth(db, client):
    """Смысл всей задачи: НИ ОДНОГО auth-заголовка, ни cookie — и дашборд открыт."""
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _enroll_student(db, student_id=student_id, course_id=course_id)

    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()

    resp = await client.get(f"/api/v1/public/parent-dashboard/{link['token']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["student_id"] == student_id
    assert body["student_full_name"] == f"{_TAG}-stud"
    assert [c["course_id"] for c in body["courses"]] == [course_id]
    assert "period_total" in body and "attendance" in body


@pytest.mark.asyncio
async def test_public_dashboard_accepts_explicit_period(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")
    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/public/parent-dashboard/{link['token']}",
        params={"from": (now - timedelta(days=7)).isoformat(), "to": now.isoformat()},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_public_dashboard_422_when_period_inverted(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")
    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/public/parent-dashboard/{link['token']}",
        params={"from": now.isoformat(), "to": (now - timedelta(days=1)).isoformat()},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_revoked_and_unknown_and_garbage_tokens_are_indistinguishable_404(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")
    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()
    await client.delete(
        f"/api/v1/parent-access-links/{link['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    revoked = await client.get(f"/api/v1/public/parent-dashboard/{link['token']}")
    unknown = await client.get(f"/api/v1/public/parent-dashboard/{'ab' * 32}")
    # Не hex вовсе — не должно валиться 500.
    garbage = await client.get(f"/api/v1/public/parent-dashboard/{'zz' * 32}")

    assert revoked.status_code == 404, revoked.text
    assert unknown.status_code == 404, unknown.text
    assert garbage.status_code == 404, garbage.text
    assert revoked.json()["detail"] == unknown.json()["detail"] == garbage.json()["detail"]


@pytest.mark.asyncio
async def test_link_is_bound_to_its_own_student(db, client):
    """Токен ученика А не показывает ученика Б (ссылка параметризована)."""
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_a, _ = await _new_user(db, role="student", name="studA")
    student_b, _ = await _new_user(db, role="student", name="studB")

    link_a = (await _create_link(client, admin_token=admin_token, student_id=student_a)).json()
    resp = await client.get(f"/api/v1/public/parent-dashboard/{link_a['token']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["student_id"] == student_a
    assert resp.json()["student_id"] != student_b


@pytest.mark.asyncio
async def test_public_dashboard_excludes_solution_rules_and_help_request_text(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course-min")
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    task_id = await _new_task(db, course_id=course_id, uid="a")
    await _insert_help_request(
        db, student_id=student_id, task_id=task_id, course_id=course_id, teacher_id=teacher_id
    )

    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()
    resp = await client.get(f"/api/v1/public/parent-dashboard/{link['token']}")
    assert resp.status_code == 200, resp.text
    raw = resp.text
    assert "solution_rules" not in raw
    assert "resolution_comment" not in raw
    assert f"{_TAG} секретный текст переписки" not in raw
    assert f"{_TAG}-secret-a" not in raw


@pytest.mark.asyncio
async def test_opening_link_records_last_used(db, client):
    _admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")
    link = (await _create_link(client, admin_token=admin_token, student_id=student_id)).json()
    assert link["last_used_at"] is None

    await client.get(f"/api/v1/public/parent-dashboard/{link['token']}")

    list_resp = await client.get(
        f"/api/v1/students/{student_id}/access-links",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.json()[0]["last_used_at"] is not None


# ============================== Токен не утекает в access-лог ==============================


def test_access_log_redacts_token_in_url_path():
    """Токен едет в ПУТИ, а не в query — фильтр tsk-496 сам по себе его не
    ловил. Для magic-link лог был почти безобиден (токен одноразовый, 15 мин),
    здесь ссылка бессрочная и многоразовая: строка в app.log = рабочий пропуск
    навсегда для всех, у кого есть доступ к логам."""
    import logging

    from app.core.logger import AccessLogRedactingFilter

    token = "b" * 64
    flt = AccessLogRedactingFilter()
    record = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d', args=(
            "127.0.0.1:1", "GET", f"/api/v1/public/parent-dashboard/{token}", "1.1", 200,
        ),
        exc_info=None,
    )

    assert flt.filter(record) is True
    line = record.getMessage()
    assert token not in line
    assert "***REDACTED***" in line
    # Сам маршрут должен остаться читаемым — иначе лог бесполезен для разбора.
    assert "/api/v1/public/parent-dashboard/" in line
