"""
tsk-110 ADR-0040 / tsk-160 ADR-0047: тесты GET /api/v1/media/{sha_ext}

Dev-режим (S3 не настроен, cas_media_root):
  1. ok_image      — корректный sha_ext, файл существует → HTTP 200, верный content-type
  2. ok_pdf        — расширение pdf → HTTP 200, application/pdf
  3. ok_xls        — расширение xls → HTTP 200, application/vnd.ms-excel
  4. missing       — корректный sha_ext, файл отсутствует → HTTP 404
  5. wrong_ext     — расширение не из allowlist → HTTP 400
  6. short_sha     — sha256 короче 64 hex → HTTP 400
  7. long_sha      — sha256 длиннее 64 hex → HTTP 400
  8. uppercase_sha — sha256 с заглавными буквами → HTTP 400 (regex строгий: [0-9a-f])
  9. no_ext        — только sha256 без расширения → HTTP 400
  10. traversal_dots — ../ в параметре → HTTP 400

S3-режим (s3_media_bucket_url задан, ADR-0047):
  11. s3_redirect         — корректный sha_ext → HTTP 307, Location = <bucket>/<shard>/<sha_ext>
  12. s3_redirect_no_check_existence — редирект отдаётся, даже если файла нет ни в S3, ни локально
      (LMS не проверяет существование в S3-режиме — принятый trade-off ADR-0047)
  13. s3_wrong_ext_still_400 — валидация sha_ext работает одинаково в обоих режимах
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient, ASGITransport

# ─── bootstrap ────────────────────────────────────────────────────────────────
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# ─── константы ────────────────────────────────────────────────────────────────
_GOOD_SHA = "a" * 64          # 64 hex-символа 'a' — валидный sha256
_GOOD_PNG  = f"{_GOOD_SHA}.png"
_GOOD_PDF  = f"{_GOOD_SHA}.pdf"
_GOOD_XLS  = f"{_GOOD_SHA}.xls"


# ─── фикстура: временная CAS-директория ──────────────────────────────────────

@pytest.fixture()
def cas_root(tmp_path: Path, monkeypatch) -> Path:
    """
    Создаёт временную CAS-директорию, подменяет settings.cas_media_root
    и возвращает корень CAS.
    """
    from app.api.v1 import media as media_module
    monkeypatch.setattr(media_module.settings, "cas_media_root", tmp_path)
    return tmp_path


def _write_cas_file(cas_root: Path, sha_ext: str, content: bytes = b"fake-data") -> Path:
    """Кладёт файл в CAS по правилу <shard>/<sha_ext>."""
    sha256_hex = sha_ext[:64]
    shard = sha256_hex[:2]
    shard_dir = cas_root / shard
    shard_dir.mkdir(parents=True, exist_ok=True)
    p = shard_dir / sha_ext
    p.write_bytes(content)
    return p


# ─── тесты ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ok_image(cas_root: Path):
    """HTTP 200 для png-файла, content-type = image/png."""
    _write_cas_file(cas_root, _GOOD_PNG, b"\x89PNG\r\n\x1a\n")
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{_GOOD_PNG}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_ok_pdf(cas_root: Path):
    """HTTP 200 для pdf, content-type = application/pdf."""
    _write_cas_file(cas_root, _GOOD_PDF, b"%PDF-1.4")
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{_GOOD_PDF}")
    assert r.status_code == 200, r.text
    assert "application/pdf" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_ok_xls(cas_root: Path):
    """HTTP 200 для xls, content-type = application/vnd.ms-excel."""
    _write_cas_file(cas_root, _GOOD_XLS, b"PK")
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{_GOOD_XLS}")
    assert r.status_code == 200, r.text
    assert "vnd.ms-excel" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_missing(cas_root: Path):
    """HTTP 404 — sha_ext валидный, но файла нет в CAS."""
    # Файл не создаём намеренно
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{_GOOD_PNG}")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_wrong_ext(cas_root: Path):
    """HTTP 400 — расширение .exe не в allowlist."""
    sha_ext = f"{_GOOD_SHA}.exe"
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{sha_ext}")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_short_sha(cas_root: Path):
    """HTTP 400 — sha256 короче 64 символов."""
    sha_ext = "abc123.png"   # только 6 hex
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{sha_ext}")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_long_sha(cas_root: Path):
    """HTTP 400 — sha256 длиннее 64 символов."""
    sha_ext = f"{'a' * 65}.png"
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{sha_ext}")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_uppercase_sha(cas_root: Path):
    """HTTP 400 — заглавные hex-символы не соответствуют regex [0-9a-f]."""
    sha_ext = f"{'A' * 64}.png"   # uppercase
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{sha_ext}")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_no_ext(cas_root: Path):
    """HTTP 400 — только sha256 без расширения."""
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{_GOOD_SHA}")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_traversal_dots(cas_root: Path):
    """
    HTTP 400/404 — попытка path traversal через ../..
    FastAPI URL-декодирует параметр; regex не пропустит символы вне [0-9a-f.].
    """
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # FastAPI может вернуть 404 (not found route) или 400 (наш validator)
        r = await c.get("/api/v1/media/../../../etc/passwd")
    assert r.status_code in (400, 404), r.text


# ─── S3-режим (ADR-0047, tsk-160) ─────────────────────────────────────────────

_S3_BUCKET_URL = "https://s3.twcstorage.ru/lms-media-cas"


@pytest.fixture()
def s3_mode(monkeypatch):
    """Включает S3-режим: подменяет settings.s3_media_bucket_url."""
    from app.api.v1 import media as media_module
    monkeypatch.setattr(media_module.settings, "s3_media_bucket_url", _S3_BUCKET_URL)


@pytest.mark.asyncio
async def test_s3_redirect(s3_mode):
    """HTTP 307 с корректным Location = <bucket>/<shard>/<sha_ext> в S3-режиме."""
    from app.api.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        r = await c.get(f"/api/v1/media/{_GOOD_PNG}")
    assert r.status_code == 307, r.text
    shard = _GOOD_SHA[:2]
    assert r.headers["location"] == f"{_S3_BUCKET_URL}/{shard}/{_GOOD_PNG}"


@pytest.mark.asyncio
async def test_s3_redirect_no_check_existence(s3_mode, tmp_path: Path, monkeypatch):
    """
    Редирект отдаётся, даже если файла нет ни в S3 (не проверяем), ни локально
    (cas_media_root — пустая директория). Принятый trade-off ADR-0047.
    """
    from app.api.v1 import media as media_module
    monkeypatch.setattr(media_module.settings, "cas_media_root", tmp_path)
    from app.api.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        r = await c.get(f"/api/v1/media/{_GOOD_PNG}")
    assert r.status_code == 307, r.text


@pytest.mark.asyncio
async def test_s3_wrong_ext_still_400(s3_mode):
    """Валидация sha_ext (регекс) работает одинаково в S3- и dev-режиме."""
    sha_ext = f"{_GOOD_SHA}.exe"
    from app.api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/v1/media/{sha_ext}")
    assert r.status_code == 400, r.text
