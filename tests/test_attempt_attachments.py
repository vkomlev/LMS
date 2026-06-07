from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.main import app
from app.auth.current_user import CurrentUser
from app.core.config import Settings


async def _create_attempt(client, db) -> int:
    settings = Settings()
    api_key = settings.valid_api_keys[0]
    user_id = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar()
    course_id = (await db.execute(text("SELECT id FROM courses LIMIT 1"))).scalar()
    assert user_id is not None

    create = await client.post(
        f"/api/v1/attempts?api_key={api_key}",
        json={"user_id": int(user_id), "course_id": int(course_id) if course_id else None, "source_system": "test"},
    )
    assert create.status_code == 201, create.text
    return int(create.json()["id"])


def _delete_attempt_files(attempt_id: int) -> None:
    upload_dir = Settings().attempt_attachments_upload_dir
    for file_path in upload_dir.glob(f"{attempt_id}_*"):
        if file_path.is_file():
            file_path.unlink()


@pytest.mark.asyncio
async def test_upload_attempt_attachment_returns_metadata(db, client):
    settings = Settings()
    api_key = settings.valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    try:
        upload = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("solution.py", b"print(42)", "text/x-python")},
        )
        assert upload.status_code == 201, upload.text
        body = upload.json()
        assert body["filename"] == "solution.py"
        assert body["size_bytes"] == len(b"print(42)")
        assert body["attachment_url"].endswith(f"/attachments/{body['attachment_id']}")

        file_path = Path(settings.attempt_attachments_upload_dir) / body["attachment_id"]
        assert file_path.exists()
    finally:
        _delete_attempt_files(attempt_id)


@pytest.mark.asyncio
async def test_upload_attempt_attachment_downloads_file(db, client):
    settings = Settings()
    api_key = settings.valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    try:
        upload = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("solution.py", b"print(42)", "text/x-python")},
        )
        assert upload.status_code == 201, upload.text
        body = upload.json()

        download = await client.get(f"{body['attachment_url']}?api_key={api_key}")
        assert download.status_code == 200, download.text
        assert download.content == b"print(42)"
    finally:
        _delete_attempt_files(attempt_id)


@pytest.mark.asyncio
async def test_upload_attempt_attachment_replaces_previous_file(db, client):
    settings = Settings()
    api_key = settings.valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    try:
        first = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("first.py", b"print(1)", "text/x-python")},
        )
        assert first.status_code == 201, first.text
        first_id = first.json()["attachment_id"]

        second = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("second.py", b"print(2)", "text/x-python")},
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["attachment_id"]

        assert first_id != second_id
        assert not (settings.attempt_attachments_upload_dir / first_id).exists()
        assert (settings.attempt_attachments_upload_dir / second_id).exists()

        old_download = await client.get(f"/api/v1/attempts/{attempt_id}/attachments/{first_id}?api_key={api_key}")
        assert old_download.status_code == 404
    finally:
        _delete_attempt_files(attempt_id)


@pytest.mark.asyncio
async def test_upload_attempt_attachment_rejects_finished_attempt(db, client):
    api_key = Settings().valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    try:
        finish = await client.post(f"/api/v1/attempts/{attempt_id}/finish?api_key={api_key}")
        assert finish.status_code == 200, finish.text

        upload = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("solution.py", b"print(42)", "text/x-python")},
        )
        assert upload.status_code == 409
    finally:
        _delete_attempt_files(attempt_id)


@pytest.mark.asyncio
async def test_upload_attempt_attachment_rejects_too_large_file(monkeypatch, db, client):
    settings = Settings()
    api_key = settings.valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    monkeypatch.setattr("app.api.v1.attempts.settings.max_attachment_size_bytes", 4)
    try:
        upload = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("solution.py", b"print(42)", "text/x-python")},
        )
        assert upload.status_code == 413
        assert list(settings.attempt_attachments_upload_dir.glob(f"{attempt_id}_*")) == []
    finally:
        _delete_attempt_files(attempt_id)


@pytest.mark.asyncio
async def test_download_attempt_attachment_rejects_invalid_attachment_id(db, client):
    api_key = Settings().valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    invalid_id = f"{attempt_id}_not-a-uuid_solution.py"
    download = await client.get(f"/api/v1/attempts/{attempt_id}/attachments/{invalid_id}?api_key={api_key}")
    assert download.status_code == 404


@pytest.mark.asyncio
async def test_download_attempt_attachment_rejects_foreign_user(db, client):
    settings = Settings()
    api_key = settings.valid_api_keys[0]
    attempt_id = await _create_attempt(client, db)

    try:
        upload = await client.post(
            f"/api/v1/attempts/{attempt_id}/attachments?api_key={api_key}",
            files={"file": ("solution.py", b"print(42)", "text/x-python")},
        )
        assert upload.status_code == 201, upload.text
        attachment_url = upload.json()["attachment_url"]

        async def foreign_user() -> CurrentUser:
            return CurrentUser(id=-1, is_service=False)

        previous_override = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = foreign_user
        try:
            download = await client.get(attachment_url)
        finally:
            if previous_override is None:
                app.dependency_overrides.pop(get_current_user, None)
            else:
                app.dependency_overrides[get_current_user] = previous_override

        assert download.status_code == 403
    finally:
        _delete_attempt_files(attempt_id)
