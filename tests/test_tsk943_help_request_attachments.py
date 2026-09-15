"""tsk-943: вложения к заявке помощи + заявка «Я не понял» по материалу.

Три вещи из одного запроса оператора:

1. Вложение (скрин ошибки, файл, код) к запросу помощи по заданию —
   загрузка ДО создания заявки (`POST /learning/help-requests/attachments`),
   затем `attachment_id` едет в теле `request-help` и попадает в строку
   `help_requests`; преподаватель его видит в списке/карточке и скачивает
   по ACL заявки (`can_access_help_request`), сам ученик — как автор.
2. Заявка «Я не понял» по МАТЕРИАЛУ (не заданию) — `task_id` стал nullable,
   `material_id` занял его место; пустой текст запрещён на уровне схемы
   (тот же принцип, что tsk-261 A10 для заданий).
3. ACL скачивания: автор заявки — да, чужой ученик — нет, преподаватель по
   `student_teacher_links` — да, посторонний преподаватель — нет.
"""
from __future__ import annotations

import io
import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_TAG = "tsk943"


async def _user(db, name: str) -> tuple[int, str]:
    """Пользователь с живой сессией (bearer-токен)."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG} {name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _course(db) -> int:
    res = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
        {"t": f"{_TAG}-course-{random.randint(10**8, 10**10)}"},
    )
    return res.scalar_one()


async def _task(db, course_id: int) -> int:
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id, is_active) "
            "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true) RETURNING id"
        ),
        {
            "e": f"{_TAG}-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"type": "SA", "stem": "x"}),
            "r": json.dumps({"max_score": 10}),
            "cid": course_id,
            "d": difficulty_id,
        },
    )
    return res.scalar_one()


async def _material(db, course_id: int, *, is_active: bool = True) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES (:t, 'text', CAST(:c AS jsonb), :cid, :act) RETURNING id"
        ),
        {
            "t": f"{_TAG}-mat-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"text": "материал"}),
            "cid": course_id,
            "act": is_active,
        },
    )
    return res.scalar_one()


async def _link_teacher(db, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id, linked_at) "
            "VALUES (:s, :t, now())"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _upload(client, token: str, *, filename: str = "screenshot.png", content: bytes = b"png-bytes") -> dict:
    resp = await client.post(
        "/api/v1/learning/help-requests/attachments",
        headers=_bearer(token),
        files={"file": (filename, io.BytesIO(content), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ───────────────────────── Вложение к заявке по заданию ─────────────────────


async def test_upload_attachment_returns_metadata(db, client) -> None:
    _sid, token = await _user(db, "stud")
    data = await _upload(client, token)
    assert data["filename"] == "screenshot.png"
    assert data["content_type"].startswith("image/")
    assert data["size_bytes"] == len(b"png-bytes")
    assert data["attachment_id"]


async def test_request_help_with_attachment_stores_and_exposes_it(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, s_token = await _user(db, "stud")
    teacher_id, t_token = await _user(db, "tch")
    await _link_teacher(db, student_id, teacher_id)
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    att = await _upload(client, s_token, filename="error.png")
    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(s_token),
        json={
            "student_id": student_id,
            "message": "не работает, вот скрин",
            "attachment_id": att["attachment_id"],
            "attachment_filename": att["filename"],
            "attachment_content_type": att["content_type"],
            "attachment_size_bytes": att["size_bytes"],
        },
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["request_id"]

    row = (
        await db.execute(
            text(
                "SELECT attachment_id, attachment_filename, task_id, material_id "
                "FROM help_requests WHERE id = :id"
            ),
            {"id": request_id},
        )
    ).fetchone()
    assert row.attachment_id == att["attachment_id"]
    assert row.attachment_filename == "error.png"
    assert row.task_id == task_id
    assert row.material_id is None

    detail = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}?teacher_id={teacher_id}",
        headers=_bearer(t_token),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["attachment_url"] == f"/api/v1/teacher/help-requests/{request_id}/attachment"
    assert body["attachment_filename"] == "error.png"

    listing = await client.get(
        f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=open&limit=50",
        headers=_bearer(t_token),
    )
    assert listing.status_code == 200, listing.text
    items = {it["request_id"]: it for it in listing.json()["items"]}
    assert items[request_id]["attachment_url"] is not None


async def test_request_help_without_attachment_leaves_it_null(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, token = await _user(db, "stud")
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "не понимаю"},
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["request_id"]
    row = (
        await db.execute(
            text("SELECT attachment_id FROM help_requests WHERE id = :id"), {"id": request_id}
        )
    ).fetchone()
    assert row.attachment_id is None


# ───────────────────────── «Я не понял» по материалу ─────────────────────────


async def test_material_request_help_creates_row_without_task(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, token = await _user(db, "stud")
    course_id = await _course(db)
    material_id = await _material(db, course_id)

    resp = await client.post(
        f"/api/v1/learning/materials/{material_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "не понял термин"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["deduplicated"] is False
    request_id = body["request_id"]

    row = (
        await db.execute(
            text("SELECT task_id, material_id, message FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
    ).fetchone()
    assert row.task_id is None
    assert row.material_id == material_id
    assert row.message == "не понял термин"


async def test_material_request_help_rejects_empty_message(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, token = await _user(db, "stud")
    course_id = await _course(db)
    material_id = await _material(db, course_id)

    resp = await client.post(
        f"/api/v1/learning/materials/{material_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "   "},
    )
    assert resp.status_code == 422, resp.text


async def test_material_request_help_dedupes_within_window(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, token = await _user(db, "stud")
    course_id = await _course(db)
    material_id = await _material(db, course_id)

    first = await client.post(
        f"/api/v1/learning/materials/{material_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "не понял"},
    )
    second = await client.post(
        f"/api/v1/learning/materials/{material_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "не понял"},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["request_id"] == second.json()["request_id"]
    assert second.json()["deduplicated"] is True

    count = (
        await db.execute(
            text(
                "SELECT count(*) FROM help_requests WHERE student_id = :s AND material_id = :m"
            ),
            {"s": student_id, "m": material_id},
        )
    ).scalar()
    assert count == 1


async def test_material_request_help_material_not_found(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, token = await _user(db, "stud")
    resp = await client.post(
        "/api/v1/learning/materials/999999999/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "не понял"},
    )
    assert resp.status_code == 404


# ───────────────────────────── ACL скачивания ────────────────────────────────


async def test_download_attachment_acl(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, s_token = await _user(db, "stud")
    other_student_id, other_token = await _user(db, "other-stud")
    teacher_id, t_token = await _user(db, "tch")
    stranger_teacher_id, stranger_token = await _user(db, "stranger-tch")
    await _link_teacher(db, student_id, teacher_id)

    course_id = await _course(db)
    material_id = await _material(db, course_id)
    att = await _upload(client, s_token, filename="hint.png")

    created = await client.post(
        f"/api/v1/learning/materials/{material_id}/request-help",
        headers=_bearer(s_token),
        json={
            "student_id": student_id,
            "message": "не понял",
            "attachment_id": att["attachment_id"],
            "attachment_filename": att["filename"],
            "attachment_content_type": att["content_type"],
            "attachment_size_bytes": att["size_bytes"],
        },
    )
    request_id = created.json()["request_id"]

    # Автор — скачивает.
    own = await client.get(
        f"/api/v1/learning/help-requests/{request_id}/attachment", headers=_bearer(s_token)
    )
    assert own.status_code == 200, own.text
    assert own.content == b"png-bytes"

    # Чужой ученик — 403.
    foreign = await client.get(
        f"/api/v1/learning/help-requests/{request_id}/attachment", headers=_bearer(other_token)
    )
    assert foreign.status_code == 403

    # Назначенный преподаватель — скачивает.
    teacher_ok = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}/attachment?teacher_id={teacher_id}",
        headers=_bearer(t_token),
    )
    assert teacher_ok.status_code == 200, teacher_ok.text

    # Посторонний преподаватель — 403.
    teacher_forbidden = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}/attachment?teacher_id={stranger_teacher_id}",
        headers=_bearer(stranger_token),
    )
    assert teacher_forbidden.status_code == 403


async def test_download_attachment_404_when_none(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, token = await _user(db, "stud")
    course_id = await _course(db)
    material_id = await _material(db, course_id)

    created = await client.post(
        f"/api/v1/learning/materials/{material_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "без вложения"},
    )
    request_id = created.json()["request_id"]

    resp = await client.get(
        f"/api/v1/learning/help-requests/{request_id}/attachment", headers=_bearer(token)
    )
    assert resp.status_code == 404
