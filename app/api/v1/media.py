# app/api/v1/media.py
"""
CAS media endpoint (ADR-0040, tsk-110).

Публичный endpoint для отдачи медиафайлов и файлов-вложений внешних задач,
загруженных ContentBackbone в общее CAS-хранилище.

Маршрут: GET /api/v1/media/{sha_ext}
    sha_ext = {sha256hex}.{ext}  (64 hex-символа + точка + разрешённое расширение)

Безопасность:
- Regex-валидация sha_ext исключает path traversal на уровне параметра.
- Путь строится детерминированно (не из user input напрямую).
- root-jail через Path.is_relative_to() — дополнительная страховка.
- Нет аутентификации: stem-изображения уже публичны через guest-endpoint (Y-5).

Структура CAS на диске:
    <cas_media_root>/<sha256[:2]>/<sha256hex>.<ext>
    Пример: data/media_store/ab/abc123...0000.png
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.core.config import Settings

logger = logging.getLogger("api.media")
settings = Settings()
router = APIRouter(tags=["media"])

# Разрешённые расширения и их content-type (allowlist, не mimetypes.guess_type).
# Только типы, ожидаемые от внешних задач CB: изображения + учебные файлы.
_EXT_CONTENT_TYPE: dict[str, str] = {
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "gif":  "image/gif",
    "webp": "image/webp",
    "svg":  "image/svg+xml",
    "pdf":  "application/pdf",
    "txt":  "text/plain; charset=utf-8",
    "ods":  "application/vnd.oasis.opendocument.spreadsheet",
    "odt":  "application/vnd.oasis.opendocument.text",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls":  "application/vnd.ms-excel",
    "csv":  "text/csv; charset=utf-8",
}

# Regex для валидации sha_ext:
#   - ровно 64 hex-символа (sha256)
#   - точка
#   - одно из разрешённых расширений
_SHA_EXT_RE = re.compile(
    r"^[0-9a-f]{64}\.(" + "|".join(re.escape(e) for e in _EXT_CONTENT_TYPE) + r")$"
)


@router.get(
    "/media/{sha_ext}",
    summary="Получить CAS-медиафайл по sha256-имени",
    response_class=FileResponse,
)
async def get_cas_media(sha_ext: str) -> FileResponse:
    """
    Отдаёт файл из CAS-хранилища ContentBackbone.

    sha_ext: строка вида ``<sha256hex>.<ext>`` (64 hex-символа + расширение).
    Endpoint публичный — не требует аутентификации (изображения задач доступны
    через guest-mode, sha256-имена не поддаются перебору).

    HTTP 400 — неверный формат sha_ext.
    HTTP 404 — файл отсутствует в CAS.
    HTTP 200 — FileResponse с корректным content-type из allowlist.
    """
    # 1. Validate формат — защита от traversal до построения пути
    if not _SHA_EXT_RE.match(sha_ext):
        logger.warning("media: неверный sha_ext=%r (не соответствует regex)", sha_ext)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный формат sha_ext. Ожидается: <64 hex-символа>.<расширение>",
        )

    # 2. Построить путь (детерминированно, не из user-input напрямую)
    sha256_hex = sha_ext[:64]
    ext = sha_ext[65:]  # after the dot at position 64
    shard = sha256_hex[:2]
    file_path = settings.cas_media_root / shard / sha_ext

    # 3. Root-jail: убедиться что resolved path внутри cas_media_root
    try:
        resolved = file_path.resolve()
        cas_root_resolved = settings.cas_media_root.resolve()
        if not resolved.is_relative_to(cas_root_resolved):
            logger.error(
                "media: path traversal attempt sha_ext=%r resolved=%s root=%s",
                sha_ext, resolved, cas_root_resolved,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Недопустимый путь",
            )
    except ValueError:
        # is_relative_to может бросить ValueError на Windows при разных дисках
        logger.error("media: root-jail ValueError sha_ext=%r", sha_ext)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недопустимый путь",
        )

    # 4. Проверить существование файла
    if not resolved.exists() or not resolved.is_file():
        logger.info("media: файл не найден в CAS sha_ext=%r path=%s", sha_ext, resolved)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Медиафайл не найден",
        )

    # 5. Content-type из allowlist (не mimetypes — защита от MIME-sniffing)
    media_type = _EXT_CONTENT_TYPE[ext]

    logger.info("media: отдаём файл sha_ext=%r size=%d", sha_ext, resolved.stat().st_size)
    return FileResponse(
        path=str(resolved),
        media_type=media_type,
        filename=sha_ext,
    )
