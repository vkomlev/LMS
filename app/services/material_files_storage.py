# app/services/material_files_storage.py
"""Хранилище файлов материалов (tsk-520): CAS в S3 вместо диска приложения.

**Зачем.** Раньше `POST /materials/upload` клал файл на диск приложения под
именем `{uuid4hex}_{оригинал}`. Диск не переживает переезд и пересоздание
машины: в tsk-519 БД перенесли, каталог `uploads/` нет — материал полгода
ссылался на файл, которого на сервере не было, и БД об этом не знала.

**Как теперь.** Файл адресуется содержимым: имя — `<sha256hex>.<ext>`, тот же
формат, что у медиа заданий (ADR-0040/0047). Ключ в бакете —
`<префикс>/<sha[:2]>/<sha256hex>.<ext>`; префикс отделяет файлы материалов от
CAS-пространства заданий, которое наполняет ContentBackbone.

**Границы доступа.** Прямая ссылка на S3 наружу НЕ выдаётся: файл всегда
проходит через `GET /materials/files/{file_id}` с проверкой доступа к курсу
(tsk-516). Отсюда стрим через приложение вместо 307-редиректа, которым
отдаётся публичное медиа заданий.

**Dev без S3.** Если ключи не заданы — файл пишется в `materials_upload_dir`
под тем же именем `<sha>.<ext>`. Чтение с диска остаётся и как запасной путь
для файлов, загруженных до этой задачи (имена вида `{uuid4hex}_{оригинал}`).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
import tempfile
from pathlib import Path
from typing import IO, Iterator, Optional, Tuple

from fastapi import UploadFile

from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger("services.material_files_storage")

settings = Settings()

# Расширение из имени файла: буквы и цифры, до 8 символов. Всё прочее
# (двойные точки, пробелы, кириллица, отсутствие расширения) — в `bin`,
# потому что имя файла целиком приходит от клиента и попадает в ключ бакета.
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_DEFAULT_EXT = "bin"

# Имя файла в CAS-формате: <64 hex>.<ext>. Файлы, загруженные до tsk-520,
# этому не соответствуют и ищутся только на диске.
_SHA_EXT_RE = re.compile(r"^[0-9a-f]{64}\.[A-Za-z0-9]{1,8}$")


def _ext_for(filename: Optional[str], content_type: Optional[str]) -> str:
    """Возвращает расширение файла: из имени, иначе из Content-Type, иначе `bin`."""
    suffix = Path(filename or "").suffix.lstrip(".").lower()
    if _EXT_RE.match(suffix):
        return suffix

    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    guessed = guessed.lstrip(".").lower()
    if _EXT_RE.match(guessed):
        return guessed

    return _DEFAULT_EXT


def s3_enabled() -> bool:
    """True, если заданы все реквизиты записи в S3 (иначе — dev-режим на диске)."""
    return bool(
        settings.s3_endpoint_url
        and settings.s3_bucket_name
        and settings.s3_access_key
        and settings.s3_secret_key
    )


def object_key(sha_ext: str) -> str:
    """Ключ объекта в бакете: `<префикс>/<sha[:2]>/<sha256hex>.<ext>`."""
    return f"{settings.material_files_s3_prefix}/{sha_ext[:2]}/{sha_ext}"


def _client():
    """Создаёт boto3-клиент S3. Импорт локальный: зависимость нужна только с ключами."""
    import boto3  # локальный импорт: dev без S3 работает без установленного boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def _put_object_sync(fileobj: IO[bytes], key: str, content_type: str) -> None:
    """Синхронная запись объекта (boto3 блокирующий) — вызывать через `to_thread`."""
    fileobj.seek(0)
    _client().put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=fileobj,
        ContentType=content_type,
    )


def _write_to_disk(fileobj: IO[bytes], sha_ext: str) -> None:
    """Записывает файл в каталог загрузок атомарно (tmp + replace)."""
    settings.materials_upload_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.materials_upload_dir / sha_ext
    if dest.exists():
        return  # содержимое то же самое — sha совпал

    fileobj.seek(0)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(settings.materials_upload_dir))
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            while True:
                chunk = fileobj.read(settings.attachment_chunk_size)
                if not chunk:
                    break
                tmp.write(chunk)
        os.replace(tmp_path, dest)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


async def store_upload(file: UploadFile) -> Tuple[str, int]:
    """Сохраняет загруженный файл и возвращает `(<sha256hex>.<ext>, размер)`.

    Идемпотентно по содержимому: одинаковый файл даёт одно и то же имя и
    перезаписывает сам себя тем же байтом в байт содержимым.

    Raises:
        DomainError: 413, если файл больше `MAX_ATTACHMENT_SIZE_BYTES`;
            503, если хранилище не приняло файл (сеть, доступ, конфигурация).
    """
    digest = hashlib.sha256()
    total = 0
    ext = _ext_for(file.filename, file.content_type)
    content_type = (file.content_type or "").split(";")[0].strip() or (
        mimetypes.guess_type(f"x.{ext}")[0] or "application/octet-stream"
    )

    # Буфер: до 4 чанков в памяти, дальше — временный файл на диске. Тело нужно
    # целиком до записи, потому что имя объекта считается по его же содержимому.
    with tempfile.SpooledTemporaryFile(max_size=settings.attachment_chunk_size * 4) as buf:
        while True:
            chunk = await file.read(settings.attachment_chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_attachment_size_bytes:
                raise DomainError(
                    f"Файл слишком большой. Максимум {settings.max_attachment_size_bytes} байт",
                    status_code=413,
                )
            digest.update(chunk)
            buf.write(chunk)

        sha_ext = f"{digest.hexdigest()}.{ext}"

        if s3_enabled():
            from botocore.exceptions import BotoCoreError, ClientError

            key = object_key(sha_ext)
            try:
                await asyncio.to_thread(_put_object_sync, buf, key, content_type)
            except (BotoCoreError, ClientError) as exc:
                # Молчать нельзя: клиент вписывает возвращённый url в материал,
                # и «успешная» загрузка без файла даёт ровно ту битую ссылку,
                # ради которой задача и заведена (tsk-519).
                logger.error("tsk-520: хранилище не приняло файл key=%r err=%s", key, exc)
                raise DomainError(
                    "Хранилище файлов недоступно, файл не сохранён", status_code=503
                ) from exc
            logger.info(
                "tsk-520: файл материала записан в S3 key=%r size=%s тип=%r",
                key, total, content_type,
            )
        else:
            await asyncio.to_thread(_write_to_disk, buf, sha_ext)
            logger.info(
                "tsk-520: S3 не настроен — файл материала записан на диск name=%r size=%s",
                sha_ext, total,
            )

    return sha_ext, total


def _disk_path(file_id: str) -> Optional[Path]:
    """Путь файла в каталоге загрузок, если он там есть и не выходит за его корень."""
    root = settings.materials_upload_dir.resolve()
    candidate = (settings.materials_upload_dir / file_id).resolve()
    # Root-jail: страховка на случай экзотического имени, прошедшего проверку роутера.
    if not candidate.is_relative_to(root):
        logger.error("tsk-520: попытка выхода за каталог загрузок file_id=%r", file_id)
        return None
    return candidate if candidate.is_file() else None


def _iter_file(path: Path) -> Iterator[bytes]:
    """Читает файл с диска чанками."""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(settings.attachment_chunk_size)
            if not chunk:
                break
            yield chunk


def _iter_body(body) -> Iterator[bytes]:
    """Читает тело ответа S3 чанками и закрывает поток."""
    try:
        while True:
            chunk = body.read(settings.attachment_chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def _head_object_sync(key: str) -> bool:
    """True, если объект есть в бакете. Вызывать через `to_thread` (boto3 блокирующий)."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _client().head_object(Bucket=settings.s3_bucket_name, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound", "NoSuchBucket"):
            return False
        logger.error("tsk-521: ошибка проверки объекта key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc
    except BotoCoreError as exc:
        logger.error("tsk-521: ошибка соединения с S3 key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc


async def object_exists(key: str) -> bool:
    """Есть ли объект с таким ключом в бакете (без скачивания тела).

    Используется проверкой целостности ссылок (tsk-521): ей нужен факт
    наличия, а не содержимое.

    Raises:
        DomainError: 503, если хранилище недоступно — «нет ответа» нельзя
            выдавать за «файла нет», иначе проверка нарисует битыми все ссылки.
    """
    return await asyncio.to_thread(_head_object_sync, key)


async def material_file_exists(file_id: str) -> bool:
    """Есть ли файл материала — в бакете (CAS-имя) либо на диске (старые имена)."""
    if s3_enabled() and _SHA_EXT_RE.match(file_id):
        if await object_exists(object_key(file_id)):
            return True
    return _disk_path(file_id) is not None


def _get_object_sync(key: str) -> Optional[dict]:
    """Синхронное чтение объекта; None, если объекта нет. Вызывать через `to_thread`."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return _client().get_object(Bucket=settings.s3_bucket_name, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None
        # Сетевая, конфигурационная ошибка или отказ доступа — это не «файла нет»:
        # ответить 404 значило бы выдать отсутствие файла за факт.
        logger.error("tsk-520: ошибка чтения S3 key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc
    except BotoCoreError as exc:
        logger.error("tsk-520: ошибка соединения с S3 key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc


async def open_file(file_id: str) -> Optional[Tuple[Iterator[bytes], str]]:
    """Открывает файл материала: `(поток чанков, Content-Type)` или None, если файла нет.

    Порядок источников: S3 (если настроен и имя в CAS-формате), затем диск —
    там лежат файлы, загруженные до tsk-520, и все файлы dev-режима.

    Обращение к S3 уходит в отдельный поток: boto3 блокирующий, а вызов идёт из
    обработчика запроса — иначе на время сетевого запроса встал бы весь сервис.
    Чтение чанков в возвращаемом генераторе блокирующим не является: Starlette
    крутит синхронный итератор в пуле потоков.
    """
    guessed_type = mimetypes.guess_type(file_id)[0] or "application/octet-stream"

    if s3_enabled() and _SHA_EXT_RE.match(file_id):
        key = object_key(file_id)
        obj = await asyncio.to_thread(_get_object_sync, key)
        if obj is not None:
            return _iter_body(obj["Body"]), (obj.get("ContentType") or guessed_type)
        logger.info("tsk-520: в S3 нет объекта key=%r — пробуем диск", key)

    path = _disk_path(file_id)
    if path is None:
        return None
    return _iter_file(path), guessed_type
