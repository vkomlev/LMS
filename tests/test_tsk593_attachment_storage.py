"""tsk-593: вложения, переписка и чеки живут в объектном хранилище, не на диске.

Покрываем то, ради чего задача заведена, и то, что при переезде легко потерять:

- (а) загрузка вложения ответа кладёт файл в бакет, а не на диск приложения;
- (б) скачивание идёт ПОТОКОМ через приложение — прямой ссылки на бакет
      наружу не выдаётся (иначе проверка прав обходится);
- (в) у каждого вида файлов СВОЁ пространство ключей: чек не лежит рядом с
      учебным вложением;
- (г) тип содержимого выводится из имени, а не берётся общим `octet-stream`
      (урок tsk-536: картинка с общим типом не рисуется у человека);
- (д) отказ хранилища — это 503 и НЕзаписанное вложение, а не молчаливый успех
      со ссылкой в никуда (ровно то, из-за чего заведена tsk-519);
- (е) файлы, лежащие на диске с прошлых времён, продолжают читаться;
- (ж) «файла нет» отличается от «хранилище не ответило»: 410 против 503.
"""
from __future__ import annotations

import io
import uuid
from typing import Any, Dict, List

import pytest
from sqlalchemy import text

from app.services import attachment_storage

pytestmark = pytest.mark.asyncio


class _FakeS3:
    """Двойник S3: объекты в памяти, поведение ошибок как у boto3."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.content_types: Dict[str, str] = {}
        self.put_calls: List[str] = []
        self.fail_put = False

    def put_object(self, *, Bucket: str, Key: str, Body: Any, ContentType: str) -> None:
        if self.fail_put:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        data = Body.read() if hasattr(Body, "read") else Body
        self.objects[Key] = data
        self.content_types[Key] = ContentType
        self.put_calls.append(Key)

    def get_object(self, *, Bucket: str, Key: str) -> Dict[str, Any]:
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {
            "Body": io.BytesIO(self.objects[Key]),
            "ContentType": self.content_types.get(Key, "application/octet-stream"),
            "ContentLength": len(self.objects[Key]),
        }

    def head_object(self, *, Bucket: str, Key: str) -> Dict[str, Any]:
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)
        self.content_types.pop(Key, None)

    def get_paginator(self, _name: str):
        outer = self

        class _Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):
                contents = [
                    {"Key": key} for key in sorted(outer.objects) if key.startswith(Prefix)
                ]
                yield {"Contents": contents}

        return _Paginator()


@pytest.fixture
def fake_s3(monkeypatch) -> _FakeS3:
    """Включает режим объектного хранилища и подменяет клиента двойником."""
    fake = _FakeS3()
    monkeypatch.setattr(attachment_storage.settings, "s3_endpoint_url", "https://s3.test")
    monkeypatch.setattr(attachment_storage.settings, "s3_bucket_name", "test-bucket")
    monkeypatch.setattr(attachment_storage.settings, "s3_access_key", "key")
    monkeypatch.setattr(attachment_storage.settings, "s3_secret_key", "secret")
    monkeypatch.setattr(attachment_storage, "_client", lambda: fake)
    return fake


def _headers() -> Dict[str, str]:
    from app.core.config import Settings

    return {"X-API-Key": next(iter(Settings().valid_api_keys))}


async def _make_attempt(client, db) -> int:
    user_id = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar()
    course_id = (await db.execute(text("SELECT id FROM courses LIMIT 1"))).scalar()
    resp = await client.post(
        "/api/v1/attempts",
        json={
            "user_id": int(user_id),
            "course_id": int(course_id) if course_id else None,
            "source_system": "test",
        },
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


# ── (а) файл уходит в бакет, а не на диск ───────────────────────────────────


async def test_upload_goes_to_bucket_not_disk(client, db, fake_s3):
    """Главное обещание задачи: диск приложения больше не хранит вложение."""
    attempt_id = await _make_attempt(client, db)
    upload = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("solution.py", b"print(42)", "text/x-python")},
        headers=_headers(),
    )
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()["attachment_id"]

    key = f"attempts/{attachment_id}"
    assert key in fake_s3.objects, "файл не попал в бакет"
    assert fake_s3.objects[key] == b"print(42)"

    on_disk = attachment_storage.local_dir(attachment_storage.ATTEMPTS) / attachment_id
    assert not on_disk.exists(), "файл всё ещё ложится на диск приложения"


# ── (б) выдача потоком, без ссылки на бакет ─────────────────────────────────


async def test_download_streams_through_app_without_redirect(client, db, fake_s3):
    """Прямая ссылка на бакет обошла бы проверку прав — её быть не должно."""
    attempt_id = await _make_attempt(client, db)
    upload = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("solution.py", b"print(42)", "text/x-python")},
        headers=_headers(),
    )
    attachment_id = upload.json()["attachment_id"]

    download = await client.get(
        f"/api/v1/attempts/{attempt_id}/attachments/{attachment_id}",
        headers=_headers(),
    )
    assert download.status_code == 200, download.text
    assert download.content == b"print(42)"
    assert "s3.test" not in str(download.headers)
    assert download.headers.get("content-type", "").startswith("text/x-python")


# ── (в) у каждого вида файлов своё пространство ключей ──────────────────────


async def test_spaces_do_not_share_prefix():
    """Чек не должен лежать в одной куче с учебным вложением."""
    keys = {
        space: attachment_storage.object_key(space, "one.png")
        for space in attachment_storage.SPACES
    }
    assert len(set(keys.values())) == len(attachment_storage.SPACES)
    assert keys[attachment_storage.RECEIPTS].startswith("receipts/")
    assert keys[attachment_storage.ATTEMPTS].startswith("attempts/")
    assert keys[attachment_storage.MESSAGES].startswith("messages/")


# ── (г) тип содержимого не «общий» ──────────────────────────────────────────


async def test_content_type_comes_from_name_not_generic(fake_s3):
    """Урок tsk-536: с типом `binary/octet-stream` картинка у человека не рисуется."""
    payload = io.BytesIO(b"\x89PNG\r\n\x1a\n")
    await attachment_storage.store_bytes(
        attachment_storage.RECEIPTS, "7_abc.png", payload
    )
    assert fake_s3.content_types["receipts/7_abc.png"] == "image/png"

    opened = await attachment_storage.open_stream(attachment_storage.RECEIPTS, "7_abc.png")
    assert opened is not None
    _stream, media_type = opened
    assert media_type == "image/png"


async def test_generic_stored_type_is_overridden_by_name(fake_s3):
    """Старый объект лежит с общим типом — отдаём по имени, а не как есть."""
    fake_s3.objects["receipts/8_old.png"] = b"\x89PNG"
    fake_s3.content_types["receipts/8_old.png"] = "binary/octet-stream"

    opened = await attachment_storage.open_stream(attachment_storage.RECEIPTS, "8_old.png")
    assert opened is not None
    assert opened[1] == "image/png"


# ── (д) отказ хранилища не выдаётся за успех ────────────────────────────────


async def test_storage_failure_returns_503_and_stores_nothing(client, db, fake_s3):
    """«Успешная» загрузка без файла — это и есть битая ссылка из tsk-519."""
    fake_s3.fail_put = True
    attempt_id = await _make_attempt(client, db)

    upload = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("solution.py", b"print(42)", "text/x-python")},
        headers=_headers(),
    )
    assert upload.status_code == 503, upload.text
    assert not fake_s3.objects


# ── (е) файлы с диска, оставшиеся с прошлых времён ──────────────────────────


async def test_file_left_on_disk_is_still_readable(client, db, fake_s3):
    """Перенос не мгновенный: то, что осталось на диске, обязано читаться."""
    from app.services.attempt_attachments import build_attachment_id

    attempt_id = await _make_attempt(client, db)
    legacy_id = build_attachment_id(attempt_id, None, "old.py")
    path = attachment_storage.local_dir(attachment_storage.ATTEMPTS) / legacy_id
    path.write_bytes(b"print('old')")
    try:
        download = await client.get(
            f"/api/v1/attempts/{attempt_id}/attachments/{legacy_id}", headers=_headers()
        )
        assert download.status_code == 200, download.text
        assert download.content == b"print('old')"
    finally:
        path.unlink(missing_ok=True)


# ── (ж) «файла нет» ≠ «хранилище не ответило» ───────────────────────────────


async def test_missing_file_is_410(client, db, fake_s3):
    from app.services.attempt_attachments import build_attachment_id

    attempt_id = await _make_attempt(client, db)
    lost_id = build_attachment_id(attempt_id, 7, "lost.py")
    download = await client.get(
        f"/api/v1/attempts/{attempt_id}/attachments/{lost_id}", headers=_headers()
    )
    assert download.status_code == 410, download.text


async def test_storage_outage_is_503_not_410(client, db, fake_s3, monkeypatch):
    """Ответить «утрачен навсегда» из-за сетевой заминки — соврать преподавателю."""
    from app.services.attempt_attachments import build_attachment_id

    attempt_id = await _make_attempt(client, db)
    name = build_attachment_id(attempt_id, 7, "x.py")

    def _boom(**_kwargs):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")

    monkeypatch.setattr(fake_s3, "get_object", _boom)
    download = await client.get(
        f"/api/v1/attempts/{attempt_id}/attachments/{name}", headers=_headers()
    )
    assert download.status_code == 503, download.text


# ── вытеснение прежнего файла работает и в бакете ───────────────────────────


async def test_reupload_same_task_replaces_object_in_bucket(client, db, fake_s3):
    """Инвариант tsk-575 «одно вложение на пару» не должен потеряться при переезде."""
    attempt_id = await _make_attempt(client, db)
    first = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("draft.py", b"draft", "text/x-python")},
        data={"task_id": "77"},
        headers=_headers(),
    )
    second = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("final.py", b"final", "text/x-python")},
        data={"task_id": "77"},
        headers=_headers(),
    )
    assert first.status_code == 201 and second.status_code == 201

    assert f"attempts/{first.json()['attachment_id']}" not in fake_s3.objects
    assert f"attempts/{second.json()['attachment_id']}" in fake_s3.objects


async def test_upload_for_other_task_keeps_neighbour_object(client, db, fake_s3):
    """И обратное: файл соседнего задания остаётся на месте (дефект tsk-575)."""
    attempt_id = await _make_attempt(client, db)
    first = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("task1.py", b"one", "text/x-python")},
        data={"task_id": "11"},
        headers=_headers(),
    )
    second = await client.post(
        f"/api/v1/attempts/{attempt_id}/attachments",
        files={"file": ("task2.py", b"two", "text/x-python")},
        data={"task_id": "22"},
        headers=_headers(),
    )
    assert f"attempts/{first.json()['attachment_id']}" in fake_s3.objects
    assert f"attempts/{second.json()['attachment_id']}" in fake_s3.objects


# ── имя файла с кириллицей не роняет выдачу ─────────────────────────────────


async def test_cyrillic_filename_header_is_encoded():
    """Имя чека — «чек за август.png»; в заголовке допустима только латиница."""
    header = attachment_storage.content_disposition("чек за август.png")
    assert header.startswith("attachment; filename*=utf-8''")
    header.encode("latin-1")  # падение здесь означало бы 500 у человека


async def test_ascii_filename_header_stays_simple():
    assert attachment_storage.content_disposition("report.pdf") == (
        'attachment; filename="report.pdf"'
    )


# ── имя не выводит за пределы пространства ──────────────────────────────────


async def test_name_cannot_escape_space(fake_s3):
    """Страховка от имени вида `../чужое`: на диске путь обязан остаться внутри."""
    root = attachment_storage.local_dir(attachment_storage.MESSAGES).resolve()
    assert attachment_storage._safe_local_path(attachment_storage.MESSAGES, "../evil") is None
    inside = attachment_storage._safe_local_path(attachment_storage.MESSAGES, "ok.txt")
    assert inside is not None and inside.parent == root


async def test_unknown_space_is_refused():
    with pytest.raises(ValueError):
        attachment_storage.object_key("materials", "x.png")
