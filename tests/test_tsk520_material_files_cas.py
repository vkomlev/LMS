"""tsk-520: файлы материалов адресуются содержимым и лежат в S3, а не на диске.

До правки `POST /materials/upload` клал файл на диск приложения под именем
`{uuid4hex}_{оригинал}`. Диск не переживает переезд машины: в tsk-519 БД
перенесли, каталог `uploads/` нет — материал полгода ссылался на файл,
которого на сервере не было.

Сценарии:
- имя файла = `<sha256 содержимого>.<ext>`, url указывает на тот же путь API
- одинаковое содержимое даёт одно и то же имя (дедупликация)
- расширение берётся из имени, из Content-Type, иначе `bin`
- файл больше лимита отклоняется с 413 и не сохраняется
- загруженный файл скачивается обратно байт в байт
- при настроенном S3 запись идёт в бакет ключом `<префикс>/<shard>/<sha>.<ext>`
- при настроенном S3 скачивание стримит из бакета, а не с диска
- отсутствующий в S3 объект ищется на диске (файлы, загруженные до tsk-520)
- прямая ссылка на бакет наружу не выдаётся: ответ 200 с телом, не 307
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
from typing import Any, Dict
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services import material_files_storage

settings = Settings()


def _service_api_key() -> str:
    raw = os.environ.get("VALID_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        pytest.skip("VALID_API_KEYS пуст в .env")
    return keys[0]


async def _pick_root(db) -> int:
    row = (
        await db.execute(
            text(
                "SELECT id FROM courses "
                "WHERE id NOT IN (SELECT course_id FROM course_parents) LIMIT 1"
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нужен хотя бы один корневой курс")
    return int(row[0])


async def _create_material_with_file(db, *, course_id: int, file_id: str) -> int:
    content = {
        "sources": [{"url": f"/api/v1/materials/files/{file_id}", "type": "file"}],
        "default_source": 0,
    }
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES (:t, 'image', CAST(:c AS jsonb), :cid, true) RETURNING id"
        ),
        {
            "t": f"tsk520-mat-{random.randint(10**8, 10**10)}",
            "c": json.dumps(content),
            "cid": course_id,
        },
    )
    mid = res.scalar_one()
    await db.commit()
    return mid


async def _drop_material(db, material_id: int) -> None:
    await db.execute(text("DELETE FROM materials WHERE id = :m"), {"m": material_id})
    await db.commit()


def _drop_disk_file(file_id: str) -> None:
    path = settings.materials_upload_dir / file_id
    if path.exists():
        path.unlink()


class _FakeS3:
    """Двойник boto3-клиента: держит объекты в памяти и записывает вызовы."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.content_types: Dict[str, str] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: Any, ContentType: str) -> None:
        data = Body.read() if hasattr(Body, "read") else Body
        self.objects[Key] = data
        self.content_types[Key] = ContentType
        self.put_calls.append({"bucket": Bucket, "key": Key, "size": len(data)})

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.get_calls.append(Key)
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {
            "Body": io.BytesIO(self.objects[Key]),
            "ContentType": self.content_types.get(Key, "application/octet-stream"),
        }


@pytest.fixture
def fake_s3(monkeypatch) -> _FakeS3:
    """Включает S3-режим хранилища и подменяет клиента двойником."""
    fake = _FakeS3()
    monkeypatch.setattr(material_files_storage.settings, "s3_endpoint_url", "https://s3.test")
    monkeypatch.setattr(material_files_storage.settings, "s3_bucket_name", "test-bucket")
    monkeypatch.setattr(material_files_storage.settings, "s3_access_key", "key")
    monkeypatch.setattr(material_files_storage.settings, "s3_secret_key", "secret")
    monkeypatch.setattr(material_files_storage, "_client", lambda: fake)
    return fake


# ─── имя файла считается по содержимому ─────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_returns_content_addressed_name(db, client):
    """Имя файла — sha256 его содержимого: то же адресование, что у медиа заданий."""
    payload = f"tsk-520 {uuid4().hex}".encode()
    expected_sha = hashlib.sha256(payload).hexdigest()

    resp = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("конспект.pdf", payload, "application/pdf")},
        params={"api_key": _service_api_key()},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    file_id = url.rsplit("/", 1)[-1]
    try:
        assert url == f"/api/v1/materials/files/{expected_sha}.pdf"
        assert file_id == f"{expected_sha}.pdf"
    finally:
        _drop_disk_file(file_id)


@pytest.mark.asyncio
async def test_same_content_gives_same_name(db, client):
    """Один и тот же файл, загруженный дважды, не плодит копии."""
    payload = f"tsk-520 dedup {uuid4().hex}".encode()
    api_key = _service_api_key()

    first = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("a.txt", payload, "text/plain")},
        params={"api_key": api_key},
    )
    second = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("b.txt", payload, "text/plain")},
        params={"api_key": api_key},
    )
    file_id = first.json()["url"].rsplit("/", 1)[-1]
    try:
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["url"] == second.json()["url"]
        # Имя файла не зависит от того, как его назвал клиент
        assert first.json()["filename"] == "a.txt"
        assert second.json()["filename"] == "b.txt"
    finally:
        _drop_disk_file(file_id)


@pytest.mark.parametrize(
    "filename, content_type, expected_ext",
    [
        ("схема.PNG", "image/png", "png"),          # расширение из имени, регистр не важен
        ("без-расширения", "application/pdf", "pdf"),  # из Content-Type
        ("файл.таблица", "", "bin"),                  # ни то, ни другое не годится
    ],
)
@pytest.mark.asyncio
async def test_extension_resolution(db, client, filename, content_type, expected_ext):
    """Расширение: имя → Content-Type → `bin`. Имя приходит от клиента, в ключ идёт очищенное."""
    payload = f"tsk-520 ext {uuid4().hex} {expected_ext}".encode()
    resp = await client.post(
        "/api/v1/materials/upload",
        files={"file": (filename, payload, content_type or "application/octet-stream")},
        params={"api_key": _service_api_key()},
    )
    file_id = resp.json()["url"].rsplit("/", 1)[-1]
    try:
        assert resp.status_code == 200, resp.text
        assert file_id.endswith(f".{expected_ext}"), file_id
    finally:
        _drop_disk_file(file_id)


@pytest.mark.asyncio
async def test_too_large_rejected(db, client, monkeypatch):
    """Файл больше лимита отклоняется 413 и не оседает в хранилище."""
    monkeypatch.setattr(material_files_storage.settings, "max_attachment_size_bytes", 1024)
    payload = b"x" * 4096
    sha = hashlib.sha256(payload).hexdigest()

    resp = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("big.bin", payload, "application/octet-stream")},
        params={"api_key": _service_api_key()},
    )
    assert resp.status_code == 413, resp.text
    assert not (settings.materials_upload_dir / f"{sha}.bin").exists()


# ─── круг «загрузил → скачал» ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_roundtrip_download_returns_same_bytes(db, client):
    """Загруженный файл скачивается обратно байт в байт (dev-режим, диск)."""
    payload = f"tsk-520 roundtrip {uuid4().hex}".encode()
    api_key = _service_api_key()
    course_id = await _pick_root(db)

    up = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("конспект.txt", payload, "text/plain")},
        params={"api_key": api_key},
    )
    file_id = up.json()["url"].rsplit("/", 1)[-1]
    mid = await _create_material_with_file(db, course_id=course_id, file_id=file_id)
    try:
        down = await client.get(
            f"/api/v1/materials/files/{file_id}", params={"api_key": api_key}
        )
        assert down.status_code == 200, down.text
        assert down.content == payload
    finally:
        _drop_disk_file(file_id)
        await _drop_material(db, mid)


# ─── S3-режим ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_goes_to_bucket_with_sharded_key(db, client, fake_s3):
    """С настроенными ключами файл уходит в бакет, а не на диск приложения."""
    payload = f"tsk-520 s3 {uuid4().hex}".encode()
    sha = hashlib.sha256(payload).hexdigest()

    resp = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("схема.png", payload, "image/png")},
        params={"api_key": _service_api_key()},
    )
    assert resp.status_code == 200, resp.text

    expected_key = f"{settings.material_files_s3_prefix}/{sha[:2]}/{sha}.png"
    assert [c["key"] for c in fake_s3.put_calls] == [expected_key]
    assert fake_s3.objects[expected_key] == payload
    assert fake_s3.content_types[expected_key] == "image/png"
    # На диске приложения ничего не осталось — ровно то, ради чего задача
    assert not (settings.materials_upload_dir / f"{sha}.png").exists()


@pytest.mark.asyncio
async def test_download_streams_from_bucket_without_redirect(db, client, fake_s3):
    """Файл отдаётся телом ответа, а не редиректом на бакет: иначе ACL обходится."""
    payload = f"tsk-520 stream {uuid4().hex}".encode()
    api_key = _service_api_key()
    course_id = await _pick_root(db)

    up = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("схема.png", payload, "image/png")},
        params={"api_key": api_key},
    )
    file_id = up.json()["url"].rsplit("/", 1)[-1]
    mid = await _create_material_with_file(db, course_id=course_id, file_id=file_id)
    try:
        down = await client.get(
            f"/api/v1/materials/files/{file_id}", params={"api_key": api_key}
        )
        assert down.status_code == 200, down.text
        assert down.content == payload
        assert down.headers["content-type"].startswith("image/png")
        # Ни в статусе, ни в заголовках нет ссылки на хранилище
        assert "location" not in {k.lower() for k in down.headers}
        assert fake_s3.get_calls, "чтение должно идти из бакета"
    finally:
        await _drop_material(db, mid)


@pytest.mark.asyncio
async def test_legacy_disk_file_still_served_when_missing_in_bucket(db, client, fake_s3):
    """Файл, загруженный до tsk-520, лежит только на диске — он должен открываться."""
    api_key = _service_api_key()
    course_id = await _pick_root(db)
    legacy_id = f"{uuid4().hex}_tsk520-legacy.txt"
    settings.materials_upload_dir.mkdir(parents=True, exist_ok=True)
    (settings.materials_upload_dir / legacy_id).write_bytes(b"legacy payload")
    mid = await _create_material_with_file(db, course_id=course_id, file_id=legacy_id)
    try:
        down = await client.get(
            f"/api/v1/materials/files/{legacy_id}", params={"api_key": api_key}
        )
        assert down.status_code == 200, down.text
        assert down.content == b"legacy payload"
    finally:
        _drop_disk_file(legacy_id)
        await _drop_material(db, mid)


@pytest.mark.asyncio
async def test_missing_everywhere_gives_404(db, client, fake_s3):
    """Нет ни в бакете, ни на диске — 404 (после проверки доступа, не до неё)."""
    api_key = _service_api_key()
    course_id = await _pick_root(db)
    absent = f"{hashlib.sha256(uuid4().bytes).hexdigest()}.png"
    mid = await _create_material_with_file(db, course_id=course_id, file_id=absent)
    try:
        down = await client.get(
            f"/api/v1/materials/files/{absent}", params={"api_key": api_key}
        )
        assert down.status_code == 404, down.text
    finally:
        await _drop_material(db, mid)


# ─── отказ хранилища ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_fails_loudly_when_bucket_rejects(db, client, fake_s3, monkeypatch):
    """Хранилище не приняло файл — 503, а не «успех» с url на несуществующий файл.

    Молчаливый успех дал бы ровно ту битую ссылку в материале, ради которой
    задача и заведена (tsk-519): клиент вписывает url в content и не узнаёт,
    что файла нет.
    """
    from botocore.exceptions import ClientError

    def _boom(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

    monkeypatch.setattr(fake_s3, "put_object", _boom)
    resp = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("схема.png", b"tsk-520 boom", "image/png")},
        params={"api_key": _service_api_key()},
    )
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_download_reports_storage_failure_not_404(db, client, fake_s3, monkeypatch):
    """Сбой хранилища — 503: ответить 404 значило бы выдать поломку за «файла нет»."""
    from botocore.exceptions import ClientError

    api_key = _service_api_key()
    course_id = await _pick_root(db)
    file_id = f"{hashlib.sha256(uuid4().bytes).hexdigest()}.png"
    mid = await _create_material_with_file(db, course_id=course_id, file_id=file_id)

    def _boom(**kwargs):
        raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")

    monkeypatch.setattr(fake_s3, "get_object", _boom)
    try:
        down = await client.get(
            f"/api/v1/materials/files/{file_id}", params={"api_key": api_key}
        )
        assert down.status_code == 503, down.text
    finally:
        await _drop_material(db, mid)


@pytest.mark.asyncio
async def test_anonymous_still_denied_for_cas_name(db, client, fake_s3):
    """Проверка доступа из tsk-516 держится и для нового формата имени."""
    payload = f"tsk-520 acl {uuid4().hex}".encode()
    sha = hashlib.sha256(payload).hexdigest()
    course_id = await _pick_root(db)
    mid = await _create_material_with_file(db, course_id=course_id, file_id=f"{sha}.png")
    try:
        resp = await client.get(f"/api/v1/materials/files/{sha}.png")
        assert resp.status_code == 401, resp.text
    finally:
        await _drop_material(db, mid)
