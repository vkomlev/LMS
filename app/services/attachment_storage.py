# app/services/attachment_storage.py
"""Хранилище вложений (tsk-593): объектное хранилище вместо диска приложения.

**Зачем.** Вложения ответов учеников, файлы переписки и чеки об оплате лежали
на диске приложения, на корневом разделе, без отдельного тома. Ровно так уже
потеряли ВСЕ файлы материалов: в tsk-519 машину переносили, базу перенесли, а
каталог `uploads/` нет — осталось 0 файлов и битые ссылки, о которых база не
знала. Материалы после этого перевели в объектное хранилище (tsk-520), а эти
три вида файлов остались на старой схеме, хотя чек об оплате — вообще
платёжный документ.

**Три пространства, а не одно.** У чека другой круг читателей и другой срок
хранения, чем у учебного вложения, поэтому ключи разделены префиксом:

* `attempts/<attachment_id>` — вложения ответов учеников;
* `messages/<attachment_id>`  — файлы переписки;
* `receipts/<stored_name>`    — чеки об оплате.

**Имя файла НЕ меняется.** В отличие от материалов (там имя — хэш содержимого),
здесь имя само по себе является идентификатором и уже записано в базе:
в `answer_json.response.meta.attachments[].attachment_id`, в
`messages.attachment_id`, в `student_payment.receipt_file`. Переименование
означало бы переписывание истории работ и платежей, поэтому ключ в бакете —
это префикс плюс существующее имя. Заодно из имени вложения ответа
по-прежнему читается пара «попытка + задание» (tsk-575).

**Наружу прямых ссылок нет.** Файл всегда идёт потоком через эндпоинт с
проверкой прав. Переадресация на бакет (как у публичного `/api/v1/media`)
здесь запрещена: она обошла бы проверку доступа, а в бакете публичное чтение
разрешено любому, кто знает ключ.

**Без S3 (разработка).** Если реквизиты не заданы — файл пишется и читается в
каталоге пространства, как раньше. Тот же каталог остаётся запасным путём
чтения для файлов, загруженных до переезда.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import IO, Any, Iterator, List, Optional, Set, Tuple
from urllib.parse import quote

from fastapi import UploadFile

from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger("services.attachment_storage")

settings = Settings()

#: Пространства ключей. Строковые константы, а не enum: имена уезжают в ключи
#: бакета, в записи проверки целостности и в логи — там нужна ровно эта строка.
ATTEMPTS = "attempts"
MESSAGES = "messages"
RECEIPTS = "receipts"

SPACES: Tuple[str, ...] = (ATTEMPTS, MESSAGES, RECEIPTS)

_DEFAULT_CONTENT_TYPE = "application/octet-stream"

#: Всё, что не латиница, цифра, точка, дефис или подчёркивание, в имени ключа
#: заменяется. Пробелы и кириллица в ключе бакета работают, но ломают ручную
#: сверку и ссылки в логах, а разделители пути открыли бы чужое пространство.
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _known(space: str) -> None:
    """Страховка от опечатки в имени пространства: чужой префикс — не наш файл."""
    if space not in SPACES:
        raise ValueError(f"Неизвестное пространство вложений: {space!r}")


def prefix_for(space: str) -> str:
    """Префикс ключей пространства в бакете."""
    _known(space)
    return {
        ATTEMPTS: settings.attempt_attachments_s3_prefix,
        MESSAGES: settings.message_attachments_s3_prefix,
        RECEIPTS: settings.payment_receipts_s3_prefix,
    }[space]


def local_dir(space: str) -> Path:
    """Каталог пространства на диске: режим разработки и файлы до переезда.

    Настройки читаются НА ВЫЗОВ, а не берутся из модульного `settings`: каталог
    задаётся переменной окружения, и тесты подменяют её `monkeypatch.setenv`.
    Реквизиты S3 при этом живут в модульном `settings` — их тесты подменяют
    прямо на объекте, как у файлов материалов.
    """
    _known(space)
    fresh = Settings()
    return {
        ATTEMPTS: fresh.attempt_attachments_upload_dir,
        MESSAGES: fresh.messages_upload_dir,
        RECEIPTS: fresh.payment_receipts_upload_dir,
    }[space]


def object_key(space: str, name: str) -> str:
    """Ключ объекта в бакете: `<префикс пространства>/<имя файла>`."""
    return f"{prefix_for(space)}/{name}"


def safe_name(filename: Optional[str]) -> str:
    """Имя файла, безопасное для ключа: без путей, пробелов и не-латиницы.

    Имя приходит от клиента и становится частью ключа в бакете, поэтому чистим
    его одинаково во всех пространствах. Показываемое человеку имя это не
    портит: оно хранится отдельно (у чека — в `receipt_name`, у вложения — в
    метаданных ответа).
    """
    base = os.path.basename(filename or "attachment")
    cleaned = _UNSAFE_NAME_RE.sub("_", base).strip("._")
    return cleaned or "attachment"


def s3_enabled() -> bool:
    """True, если заданы все реквизиты записи в S3 (иначе — режим диска)."""
    return bool(
        settings.s3_endpoint_url
        and settings.s3_bucket_name
        and settings.s3_access_key
        and settings.s3_secret_key
    )


def guess_content_type(name: str, declared: Optional[str] = None) -> str:
    """Тип содержимого файла: из имени, иначе из заявленного клиентом.

    Имя ГЛАВНЕЕ заявленного типа. Причина — урок tsk-536: файлы уезжали в
    хранилище с типом `binary/octet-stream`, и браузер отказывался рисовать
    картинку, хотя сам файл был целым. Расширение мы контролируем сами (у
    чеков — ставим по подтверждённому типу, у вложений — переносим из имени),
    а заголовок присылает клиент.
    """
    guessed = mimetypes.guess_type(name)[0]
    if guessed:
        return guessed
    declared = (declared or "").split(";")[0].strip()
    return declared or _DEFAULT_CONTENT_TYPE


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Заголовок `Content-Disposition` с именем файла, безопасным для не-ASCII.

    Имя чека — «чек за август.png», то есть кириллица. Голая подстановка в
    заголовок роняет ответ (в заголовках только latin-1), поэтому для таких
    имён идёт форма `filename*=utf-8''…` из RFC 5987.
    """
    kind = "inline" if inline else "attachment"
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        return f"{kind}; filename*=utf-8''{quote(filename, safe='')}"
    escaped = filename.replace('"', r"\"")
    return f'{kind}; filename="{escaped}"'


#: Готовый клиент и реквизиты, на которых он собран. Пересобираем только при
#: смене реквизитов — в бою они не меняются, а в тестах и скриптах меняются.
_client_cache: Tuple[Optional[tuple], Any] = (None, None)
_client_lock = threading.Lock()


def _client():
    """Клиент S3 — один на процесс. Импорт локальный: без ключей зависимость не нужна.

    tsk-644: таймауты заданы явно. На умолчаниях botocore (60 c соединение,
    60 c чтение, режим повторов `legacy`) молчащее хранилище держит вызывающего
    минутами — а зовут отсюда в том числе приём ответа ученика, синхронно.
    Замер стенда 2026-08-22: до починки одно чтение держало 211 c.

    tsk-735: клиент ПЕРЕИСПОЛЬЗУЕТСЯ, а не собирается на каждый вызов. Сборка
    сама по себе дёшева (медиана 4 мс), но вместе с ней выбрасывалось и
    соединение: каждый вызов начинался новым рукопожатием TLS. Замер на боевой
    машине 29.08: проверка наличия файла новым клиентом — 80 мс, общим — 13 мс,
    вшестеро дешевле. Клиент botocore рассчитан на вызовы из нескольких потоков
    (а зовут его именно так — через `asyncio.to_thread`), поэтому общий
    экземпляр здесь безопасен; небезопасна общая `Session`, которую мы не
    держим.
    """
    global _client_cache
    import boto3  # локальный импорт: разработка без S3 живёт без установленного boto3
    from botocore.config import Config

    key = (
        settings.s3_endpoint_url,
        settings.s3_access_key,
        settings.s3_secret_key,
        settings.s3_region,
        settings.s3_connect_timeout_sec,
        settings.s3_read_timeout_sec,
        settings.s3_retries,
    )
    cached_key, cached = _client_cache
    if cached is not None and cached_key == key:
        return cached

    with _client_lock:
        cached_key, cached = _client_cache
        if cached is not None and cached_key == key:
            return cached
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(
                connect_timeout=settings.s3_connect_timeout_sec,
                read_timeout=settings.s3_read_timeout_sec,
                retries={"max_attempts": settings.s3_retries, "mode": "standard"},
            ),
        )
        _client_cache = (key, client)
        return client


def _safe_local_path(space: str, name: str) -> Optional[Path]:
    """Путь файла в каталоге пространства, если имя не выводит за его пределы."""
    # Каталог берём ОДИН раз: `local_dir` читает настройки на вызов, а эта
    # функция зовётся на каждую проверку наличия файла.
    directory = local_dir(space)
    root = directory.resolve()
    try:
        candidate = (directory / name).resolve()
    except OSError:
        return None
    if not candidate.is_relative_to(root):
        logger.error("tsk-593: попытка выхода за каталог %s: %r", space, name)
        return None
    return candidate


# ---------------------------------------------------------------- запись


def _put_object_sync(fileobj: IO[bytes], key: str, content_type: str) -> None:
    """Синхронная запись объекта (boto3 блокирующий) — звать через `to_thread`."""
    fileobj.seek(0)
    _client().put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=fileobj,
        ContentType=content_type,
    )


def _write_to_disk(space: str, name: str, fileobj: IO[bytes]) -> None:
    """Записывает файл в каталог пространства атомарно (временный файл + замена)."""
    directory = local_dir(space)
    directory.mkdir(parents=True, exist_ok=True)
    dest = _safe_local_path(space, name)
    if dest is None:
        raise DomainError("Недопустимое имя файла", status_code=400)

    fileobj.seek(0)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(directory))
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


async def store_upload(space: str, name: str, file: UploadFile) -> Tuple[int, str]:
    """Сохраняет загруженный файл под именем `name`. Возвращает `(размер, тип)`.

    Raises:
        DomainError: 413, если файл больше `MAX_ATTACHMENT_SIZE_BYTES`;
            503, если хранилище не приняло файл (сеть, доступ, настройка).
            Молчать про 503 нельзя: клиент запишет имя вложения в ответ ученика,
            и «успешная» загрузка без файла даст ровно ту битую ссылку, ради
            которой задача и заведена.
    """
    _known(space)
    content_type = guess_content_type(name, file.content_type)
    total = 0

    # Буфер: до 4 кусков в памяти, дальше — временный файл. Тело нужно целиком
    # до записи, потому что размер проверяется до отправки в хранилище.
    with tempfile.SpooledTemporaryFile(max_size=settings.attachment_chunk_size * 4) as buf:
        while True:
            chunk = await file.read(settings.attachment_chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_attachment_size_bytes:
                raise DomainError(
                    f"Файл больше допустимого размера "
                    f"({settings.max_attachment_size_bytes} байт)",
                    status_code=413,
                )
            buf.write(chunk)

        await store_bytes(space, name, buf, content_type=content_type)

    return total, content_type


async def store_bytes(
    space: str, name: str, fileobj: IO[bytes], *, content_type: Optional[str] = None
) -> None:
    """Кладёт готовый поток в хранилище под именем `name` (без проверки размера).

    Отдельно от `store_upload`, потому что этим же путём идёт перенос файлов с
    диска: там тело уже на руках и лимит размера применять нельзя — файл сдан
    учеником давно и должен переехать как есть.
    """
    _known(space)
    ctype = content_type or guess_content_type(name)

    if not s3_enabled():
        await asyncio.to_thread(_write_to_disk, space, name, fileobj)
        logger.info(
            "tsk-593: S3 не настроен — файл записан на диск пространство=%s имя=%r",
            space, name,
        )
        return

    from botocore.exceptions import BotoCoreError, ClientError

    key = object_key(space, name)
    try:
        await asyncio.to_thread(_put_object_sync, fileobj, key, ctype)
    except (BotoCoreError, ClientError) as exc:
        logger.error("tsk-593: хранилище не приняло файл key=%r err=%s", key, exc)
        raise DomainError(
            "Хранилище файлов недоступно, файл не сохранён", status_code=503
        ) from exc
    logger.info("tsk-593: файл записан в S3 key=%r тип=%r", key, ctype)


# ---------------------------------------------------------------- чтение


def _iter_file(path: Path) -> Iterator[bytes]:
    """Читает файл с диска кусками."""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(settings.attachment_chunk_size)
            if not chunk:
                break
            yield chunk


def _iter_body(body) -> Iterator[bytes]:
    """Читает тело ответа S3 кусками и закрывает поток."""
    try:
        while True:
            chunk = body.read(settings.attachment_chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def _get_object_sync(key: str) -> Optional[dict]:
    """Синхронное чтение объекта; None, если объекта нет. Звать через `to_thread`."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return _client().get_object(Bucket=settings.s3_bucket_name, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound", "NoSuchBucket"):
            return None
        # Сеть, настройка или отказ доступа — это НЕ «файла нет»: ответить 404
        # значило бы выдать отсутствие файла за установленный факт.
        logger.error("tsk-593: ошибка чтения S3 key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc
    except BotoCoreError as exc:
        logger.error("tsk-593: ошибка соединения с S3 key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc


async def open_stream(space: str, name: str) -> Optional[Tuple[Iterator[bytes], str]]:
    """Открывает файл: `(поток кусков, тип содержимого)` или None, если файла нет.

    Порядок источников: хранилище, затем диск — там лежат файлы, загруженные до
    переезда, и все файлы режима разработки.

    Обращение к S3 уходит в отдельный поток: boto3 блокирующий, а вызов идёт из
    обработчика запроса. Чтение кусков в возвращаемом генераторе блокирующим не
    считается: Starlette крутит синхронный итератор в пуле потоков.
    """
    _known(space)
    fallback_type = guess_content_type(name)

    if s3_enabled():
        obj = await asyncio.to_thread(_get_object_sync, object_key(space, name))
        if obj is not None:
            stored = (obj.get("ContentType") or "").split(";")[0].strip()
            # Тип из имени главнее сохранённого: часть старых объектов лежит с
            # `binary/octet-stream`, и картинка по такому типу не рисуется
            # (урок tsk-536). Сохранённый тип берём, только если из имени вывод
            # не сделать.
            media_type = fallback_type
            if media_type == _DEFAULT_CONTENT_TYPE and stored:
                media_type = stored
            return _iter_body(obj["Body"]), media_type

    path = _safe_local_path(space, name)
    if path is None or not path.is_file():
        return None
    return _iter_file(path), fallback_type


async def read_from_bucket(space: str, name: str) -> Optional[Tuple[bytes, str]]:
    """Читает объект ТОЛЬКО из бакета: `(содержимое, тип)` или None.

    Отличается от `open_stream` тем, что НЕ откатывается на диск. Именно это и
    нужно проверке переноса: пока файл лежит на диске, обычное чтение вернёт
    его и там, где в бакете ничего нет, — и «перенос» отрапортует успех, ничего
    не перенеся. Так и вышло при первом прогоне переноса (46 файлов «OK» при
    нуле объектов в бакете), это тот же класс, что и «ответ 200 — значит
    работает».

    Raises:
        DomainError: 503, если хранилище недоступно.
    """
    _known(space)
    if not s3_enabled():
        return None
    obj = await asyncio.to_thread(_get_object_sync, object_key(space, name))
    if obj is None:
        return None
    body = obj["Body"]
    try:
        data = body.read()
    finally:
        body.close()
    return data, (obj.get("ContentType") or "").split(";")[0].strip()


def read_bytes_sync(space: str, name: str, *, max_bytes: int) -> Optional[bytes]:
    """Читает файл целиком, синхронно; None, если файла нет или он крупнее лимита.

    Синхронная форма нужна оценке кода (`code_review_service`): она вызывается
    из потока (`asyncio.to_thread`) на приёме ответа и напрямую в фоновом тике,
    и вся её логика — синхронная.

    Raises:
        DomainError: 503, если хранилище недоступно (см. `_get_object_sync`).
    """
    _known(space)
    if s3_enabled():
        obj = _get_object_sync(object_key(space, name))
        if obj is not None:
            length = obj.get("ContentLength")
            if length is not None and int(length) > max_bytes:
                return None
            body = obj["Body"]
            try:
                # Читаем на байт больше предела: так видно, что файл длиннее, и
                # обрезанный кусок не уедет на оценку как целая программа.
                data = body.read(max_bytes + 1)
            finally:
                body.close()
            return None if len(data) > max_bytes else data

    path = _safe_local_path(space, name)
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------- наличие


def _head_object_sync(key: str) -> bool:
    """True, если объект есть в бакете. Звать через `to_thread`."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _client().head_object(Bucket=settings.s3_bucket_name, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound", "NoSuchBucket"):
            return False
        logger.error("tsk-593: ошибка проверки объекта key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc
    except BotoCoreError as exc:
        logger.error("tsk-593: ошибка соединения с хранилищем key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc


async def exists(space: str, name: str) -> bool:
    """Есть ли файл — в бакете либо на диске (файлы до переезда, разработка).

    Raises:
        DomainError: 503, если хранилище не ответило. «Нет ответа» нельзя
            выдавать за «файла нет»: иначе проверка целостности пометит
            утраченными все вложения разом.
    """
    _known(space)
    if s3_enabled() and await asyncio.to_thread(_head_object_sync, object_key(space, name)):
        return True
    path = _safe_local_path(space, name)
    return path is not None and path.is_file()


async def existing_names(space: str, names: List[str]) -> Set[str]:
    """Какие из перечисленных файлов реально есть. Проверки идут параллельно.

    Нужна там, где вложений на экране много (очередь преподавателя, история
    работ): последовательная проверка превратила бы страницу в цепочку сетевых
    запросов.
    """
    _known(space)
    unique = sorted({n for n in names if n})
    if not unique:
        return set()

    sem = asyncio.Semaphore(max(1, settings.attachment_storage_concurrency))

    async def check(name: str) -> Tuple[str, bool]:
        async with sem:
            return name, await exists(space, name)

    found = await asyncio.gather(*(check(n) for n in unique))
    return {name for name, ok in found if ok}


def _list_keys_sync(prefix: str) -> List[str]:
    """Ключи бакета с этим префиксом. Звать через `to_thread`."""
    from botocore.exceptions import BotoCoreError, ClientError

    keys: List[str] = []
    try:
        paginator = _client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
    except (BotoCoreError, ClientError) as exc:
        logger.error("tsk-593: ошибка перечисления key_prefix=%r err=%s", prefix, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc
    return keys


async def list_names(space: str, name_prefix: str = "") -> List[str]:
    """Имена файлов пространства, начинающиеся с `name_prefix` (бакет + диск).

    Диск читается всегда, а не только без S3: файлы, загруженные до переезда,
    остаются там, и гейты «есть ли вложение у этого задания» обязаны их видеть.
    """
    _known(space)
    names: Set[str] = set()

    if s3_enabled():
        key_prefix = object_key(space, name_prefix)
        head = f"{prefix_for(space)}/"
        for key in await asyncio.to_thread(_list_keys_sync, key_prefix):
            if key.startswith(head):
                names.add(key[len(head):])

    directory = local_dir(space)
    if directory.exists():
        for path in directory.iterdir():
            if path.is_file() and path.name.startswith(name_prefix):
                names.add(path.name)

    return sorted(names)


# ---------------------------------------------------------------- удаление


def _delete_object_sync(key: str) -> None:
    """Синхронное удаление объекта. Звать через `to_thread`."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _client().delete_object(Bucket=settings.s3_bucket_name, Key=key)
    except (BotoCoreError, ClientError) as exc:
        logger.error("tsk-593: не удалось удалить объект key=%r err=%s", key, exc)
        raise DomainError("Хранилище файлов недоступно", status_code=503) from exc


async def delete(space: str, name: str) -> None:
    """Удаляет файл и в бакете, и на диске (файл мог остаться там до переезда)."""
    _known(space)
    if s3_enabled():
        await asyncio.to_thread(_delete_object_sync, object_key(space, name))

    path = _safe_local_path(space, name)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "tsk-593: не удалось удалить файл с диска пространство=%s имя=%r",
                space, name, exc_info=True,
            )
