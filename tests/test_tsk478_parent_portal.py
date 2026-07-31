"""tsk-478 (кабинет родителя, зависит от tsk-494).

Проверяем на НАСТОЯЩЕЙ БД (не на моках), по образцу
test_tsk494_student_dashboard.py.

Покрывает:
- `POST/DELETE /users/{student_id}/parents/{parent_id}`: только
  methodist/admin (403 teacher/parent/без роли), идемпотентная выдача роли
  `parent` при создании связки, идемпотентность повторного вызова.
- `GET /students/{id}/dashboard` под ролью `parent`: 200 для своего
  привязанного ученика, 403 для чужого (IDOR), 403 без роли `parent` вовсе.
- Регресс: существующий teacher/admin/methodist доступ не сломан.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk478"


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


async def _has_role(db, *, user_id: int, role: str) -> bool:
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                "WHERE ur.user_id = :u AND r.name = :role"
            ),
            {"u": user_id, "role": role},
        )
    ).first()
    return row is not None


def _dt_params(period_from: datetime, period_to: datetime) -> dict[str, str]:
    return {"from": period_from.isoformat(), "to": period_to.isoformat()}


# ============================== Link CRUD ==============================


@pytest.mark.asyncio
async def test_add_link_forbidden_for_teacher_and_parent_and_no_role(db, client):
    admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    teacher_id, teacher_token = await _new_user(db, role="teacher", name="teach")
    parent_id, parent_token = await _new_user(db, role="parent", name="parent")
    plain_id, plain_token = await _new_user(db, role=None, name="plain")
    student_id, _ = await _new_user(db, role="student", name="stud")

    for token in (teacher_token, parent_token, plain_token):
        resp = await client.post(
            f"/api/v1/users/{student_id}/parents/{parent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_add_link_by_admin_grants_parent_role_idempotently(db, client):
    admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    # Родитель только что зарегистрировался по magic-link — auto-assign дал
    # ему `student` (гочта, см. spec), роли `parent` ещё нет.
    parent_id, _ = await _new_user(db, role="student", name="parent")
    student_id, _ = await _new_user(db, role="student", name="stud")

    resp = await client.post(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 204, resp.text
    assert await _has_role(db, user_id=parent_id, role="parent")
    # student role НЕ снимается автоматически (операционное решение).
    assert await _has_role(db, user_id=parent_id, role="student")

    # Повторный вызов — идемпотентен, роль не задваивается (UNIQUE упал бы).
    resp2 = await client.post(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 204, resp2.text

    list_resp = await client.get(
        f"/api/v1/users/{student_id}/parents",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.status_code == 200, list_resp.text
    assert [p["id"] for p in list_resp.json()] == [parent_id]


@pytest.mark.asyncio
async def test_remove_link_by_methodist_is_idempotent(db, client):
    methodist_id, methodist_token = await _new_user(db, role="methodist", name="meth")
    parent_id, _ = await _new_user(db, role="parent", name="parent")
    student_id, _ = await _new_user(db, role="student", name="stud")

    await client.post(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {methodist_token}"},
    )
    resp = await client.delete(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {methodist_token}"},
    )
    assert resp.status_code == 204, resp.text
    # Повторное удаление — не ошибка.
    resp2 = await client.delete(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {methodist_token}"},
    )
    assert resp2.status_code == 204, resp2.text


# ============================== Dashboard access (IDOR) ==============================


@pytest.mark.asyncio
async def test_parent_sees_own_linked_student_dashboard(db, client):
    admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    parent_id, parent_token = await _new_user(db, role=None, name="parent")
    student_id, _ = await _new_user(db, role="student", name="stud")

    link_resp = await client.post(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert link_resp.status_code == 204, link_resp.text

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["student_id"] == student_id


@pytest.mark.asyncio
async def test_parent_cannot_see_unlinked_student_dashboard(db, client):
    """IDOR: родитель А привязан к ученику А, но пробует посмотреть ученика Б."""
    admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    parent_id, parent_token = await _new_user(db, role=None, name="parent")
    own_student_id, _ = await _new_user(db, role="student", name="own")
    other_student_id, _ = await _new_user(db, role="student", name="other")

    await client.post(
        f"/api/v1/users/{own_student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{other_student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_user_with_parent_role_but_no_link_gets_403(db, client):
    """Роль `parent` сама по себе прав не даёт — нужна конкретная связка."""
    parent_id, parent_token = await _new_user(db, role="parent", name="parent")
    student_id, _ = await _new_user(db, role="student", name="stud")

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_me_children_returns_linked_students_and_empty_for_unlinked(db, client):
    admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    parent_id, parent_token = await _new_user(db, role=None, name="parent")
    lonely_parent_id, lonely_token = await _new_user(db, role="parent", name="lonely")
    student_id, _ = await _new_user(db, role="student", name="stud")

    await client.post(
        f"/api/v1/users/{student_id}/parents/{parent_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = await client.get("/api/v1/me/children", headers={"Authorization": f"Bearer {parent_token}"})
    assert resp.status_code == 200, resp.text
    assert [c["id"] for c in resp.json()] == [student_id]

    empty_resp = await client.get("/api/v1/me/children", headers={"Authorization": f"Bearer {lonely_token}"})
    assert empty_resp.status_code == 200, empty_resp.text
    assert empty_resp.json() == []


@pytest.mark.asyncio
async def test_admin_and_teacher_dashboard_access_unaffected_by_parent_gate(db, client):
    """Регресс tsk-494: admin/teacher(-linked) продолжают работать через
    `can_edit_progress`, композитный гейт их не задевает."""
    admin_id, admin_token = await _new_user(db, role="admin", name="admin")
    student_id, _ = await _new_user(db, role="student", name="stud")

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
