"""tsk-516: ACL для GET /materials/files/{file_id} и POST /materials/upload.

До правки скачивание файла материала не проверяло ничего: ни авторизации,
ни зачисления. Прямая ссылка открывалась анониму и отчисленному ученику,
в отличие от соседнего `GET /materials/{id}` (Y-5.1). Загрузка была открыта
тем же образом.

Связи «файл → курс» в БД нет: `upload` кладёт файл на диск под именем
`{uuid4hex}_{оригинал}` и возвращает url, который клиент отдельным PATCH
вписывает в `content` материала. Поэтому курс ищется обратным поиском по
`content`, и тесты создают материал именно с таким `content`.

Сценарии:
- 401 анониму (главный сценарий задачи)
- 403 ученику, зачисленному в чужой курс
- 200 ученику, зачисленному в курс материала (файл реально отдан)
- 200 сервисному ключу (TG_LMS не сломан)
- 200 преподавателю без зачисления (extended-role bypass)
- 403 на файл-сироту, на который не ссылается ни один материал
- 400 на file_id с разделителем пути (traversal)
- порядок проверок: аноним получает 401, а не 404 (существование не палится)
- 401/403 на загрузку (аноним / ученик)
"""
from __future__ import annotations

import json
import os
import random
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

settings = Settings()


def _service_api_key() -> str:
    raw = os.environ.get("VALID_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        pytest.skip("VALID_API_KEYS пуст в .env")
    return keys[0]


async def _create_user_with_session(db, *, role_name: str | None = None) -> tuple[int, str]:
    u = Users(
        email=f"tsk516-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk516", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role_name:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name = :rn "
                "ON CONFLICT (user_id, role_id) DO NOTHING"
            ),
            {"u": u.id, "rn": role_name},
        )
    await db.commit()
    return u.id, token


async def _create_material_with_file(db, *, course_id: int, file_id: str) -> int:
    """Материал с url файла в content — ровно так его пишет клиент после upload."""
    content = {
        "sources": [{"url": f"/api/v1/materials/files/{file_id}", "type": "file"}],
        "default_source": 0,
    }
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES (:t, 'image', CAST(:c AS jsonb), :cid, true) RETURNING id"
        ),
        {"t": f"tsk516-mat-{random.randint(10**8, 10**10)}", "c": json.dumps(content), "cid": course_id},
    )
    mid = res.scalar_one()
    await db.commit()
    return mid


async def _enroll(db, *, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active, order_number) "
            "VALUES (:u, :c, true, 1) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _pick_two_roots(db) -> tuple[int, int]:
    rows = (
        await db.execute(
            text(
                "SELECT id FROM courses "
                "WHERE id NOT IN (SELECT course_id FROM course_parents) LIMIT 2"
            )
        )
    ).fetchall()
    if len(rows) < 2:
        pytest.skip("Нужно минимум два корневых курса")
    return int(rows[0][0]), int(rows[1][0])


def _make_file() -> str:
    """Положить реальный файл в каталог загрузок; вернуть его file_id."""
    settings.materials_upload_dir.mkdir(parents=True, exist_ok=True)
    file_id = f"{uuid4().hex}_tsk516.txt"
    (settings.materials_upload_dir / file_id).write_text("tsk-516 fixture", encoding="utf-8")
    return file_id


def _drop_file(file_id: str) -> None:
    path = settings.materials_upload_dir / file_id
    if path.exists():
        path.unlink()


async def _cleanup(db, *, user_ids: list[int], material_ids: list[int]) -> None:
    if material_ids:
        await db.execute(text("DELETE FROM materials WHERE id = ANY(:m)"), {"m": material_ids})
    if user_ids:
        await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
        await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.commit()


# ─── главный сценарий задачи: аноним ────────────────────────────────────────

@pytest.mark.asyncio
async def test_anonymous_denied(db, client):
    """Аноним не скачивает файл материала (был 200 — прямая ссылка работала)."""
    root_id, _ = await _pick_two_roots(db)
    file_id = _make_file()
    mid = await _create_material_with_file(db, course_id=root_id, file_id=file_id)
    try:
        resp = await client.get(f"/api/v1/materials/files/{file_id}")
        assert resp.status_code == 401, resp.text
    finally:
        _drop_file(file_id)
        await _cleanup(db, user_ids=[], material_ids=[mid])


@pytest.mark.asyncio
async def test_anonymous_gets_401_not_404_for_missing_file(db, client):
    """Аноним получает 401 и на несуществующий файл: ACL стоит до обращения к диску.

    Иначе разница 404 / 200 сама по себе выдавала бы, какие файлы существуют.
    """
    resp = await client.get(f"/api/v1/materials/files/{uuid4().hex}_nope.txt")
    assert resp.status_code == 401, resp.text


# ─── ученик: ACL по зачислению ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_student_enrolled_gets_file(db, client):
    root_id, _other = await _pick_two_roots(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll(db, user_id=uid, course_id=root_id)
    file_id = _make_file()
    mid = await _create_material_with_file(db, course_id=root_id, file_id=file_id)
    try:
        resp = await client.get(
            f"/api/v1/materials/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == b"tsk-516 fixture"
    finally:
        _drop_file(file_id)
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


@pytest.mark.asyncio
async def test_student_of_other_course_denied(db, client):
    """Отчисленный / чужой ученик получает 403 — сценарий из постановки задачи."""
    root_id, other_root = await _pick_two_roots(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll(db, user_id=uid, course_id=other_root)
    file_id = _make_file()
    mid = await _create_material_with_file(db, course_id=root_id, file_id=file_id)
    try:
        resp = await client.get(
            f"/api/v1/materials/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        _drop_file(file_id)
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


# ─── bypass'ы: сервис и расширенная роль ────────────────────────────────────

@pytest.mark.asyncio
async def test_service_key_bypass_keeps_tg_lms_working(db, client):
    """TG_LMS ходит с `?api_key=` — эта дорога должна остаться открытой."""
    root_id, _ = await _pick_two_roots(db)
    file_id = _make_file()
    mid = await _create_material_with_file(db, course_id=root_id, file_id=file_id)
    try:
        resp = await client.get(
            f"/api/v1/materials/files/{file_id}",
            params={"api_key": _service_api_key()},
        )
        assert resp.status_code == 200, resp.text
    finally:
        _drop_file(file_id)
        await _cleanup(db, user_ids=[], material_ids=[mid])


@pytest.mark.asyncio
async def test_teacher_bypass_without_enrollment(db, client):
    root_id, _ = await _pick_two_roots(db)
    uid, token = await _create_user_with_session(db, role_name="teacher")
    file_id = _make_file()
    mid = await _create_material_with_file(db, course_id=root_id, file_id=file_id)
    try:
        resp = await client.get(
            f"/api/v1/materials/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        _drop_file(file_id)
        await _cleanup(db, user_ids=[uid], material_ids=[mid])


# ─── файл-сирота и traversal ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orphan_file_denied_for_student(db, client):
    """Файл, не вписанный ни в один материал, ученику закрыт: привязки к курсу нет."""
    root_id, _ = await _pick_two_roots(db)
    uid, token = await _create_user_with_session(db, role_name="student")
    await _enroll(db, user_id=uid, course_id=root_id)
    file_id = _make_file()
    try:
        resp = await client.get(
            f"/api/v1/materials/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        _drop_file(file_id)
        await _cleanup(db, user_ids=[uid], material_ids=[])


@pytest.mark.asyncio
async def test_path_traversal_rejected(db, client):
    uid, token = await _create_user_with_session(db, role_name="teacher")
    try:
        resp = await client.get(
            "/api/v1/materials/files/..%2F..%2F.env",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (400, 404), resp.text
        assert b"env" not in resp.content or resp.status_code != 200
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[])


# ─── загрузка ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_denied_for_anonymous(db, client):
    """Вторая половина той же дыры: аноним не пишет файлы на сервер."""
    resp = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("tsk516.txt", b"anon", "text/plain")},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_upload_denied_for_student(db, client):
    uid, token = await _create_user_with_session(db, role_name="student")
    try:
        resp = await client.post(
            "/api/v1/materials/upload",
            files={"file": ("tsk516.txt", b"student", "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[uid], material_ids=[])
