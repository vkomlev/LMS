# app/api/v1/materials_extra.py
"""
Расширенные эндпойнты для материалов: поиск, список по курсу, reorder, move, bulk-update, copy, stats, загрузка файлов, импорт из Google Sheets.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends, Body, Query, File, UploadFile, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import Settings
from app.repos.courses_repo import CoursesRepository
from app.repos.materials_repo import MaterialsRepository
from app.schemas.materials import (
    MaterialRead,
    MaterialReorderRequest,
    MaterialReorderResponse,
    MaterialOrderRead,
    MaterialMoveRequest,
    MaterialBulkUpdateRequest,
    MaterialBulkUpdateResponse,
    MaterialCopyRequest,
    MaterialsListResponse,
    MaterialsGoogleSheetsImportRequest,
    MaterialsGoogleSheetsImportResponse,
    MaterialsGoogleSheetsImportError,
    MaterialsImportByCourseItem,
)
from app.services.materials_service import MaterialsService
from app.services.google_sheets_service import GoogleSheetsService
from app.services.materials_sheets_parser_service import MaterialsSheetsParserService
from app.utils.exceptions import DomainError

logger = logging.getLogger("api.materials_extra")

router = APIRouter(tags=["materials"])
materials_service = MaterialsService()
materials_repo = MaterialsRepository()
courses_repo = CoursesRepository()
gsheets_service = GoogleSheetsService()
parser_service = MaterialsSheetsParserService()
settings = Settings()


@router.get(
    "/materials/search",
    response_model=MaterialsListResponse,
    summary="Поиск материалов по title и external_uid",
)
async def search_materials(
    q: str = Query(..., min_length=1, description="Строка поиска (title, external_uid)"),
    course_id: int | None = Query(None, description="Ограничить поиск курсом; при отсутствии — по всем курсам"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> MaterialsListResponse:
    """Глобальный поиск материалов по заголовку и external_uid. course_id опционально."""
    items, total = await materials_service.search_materials(
        db, q, course_id=course_id, skip=skip, limit=limit
    )
    logger.info("search_materials q=%s course_id=%s total=%s", q, course_id, total)
    return MaterialsListResponse(
        items=[MaterialRead.model_validate(m) for m in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/courses/{course_id}/materials",
    response_model=MaterialsListResponse,
    summary="Список материалов курса",
)
async def list_course_materials(
    course_id: int,
    q: str | None = Query(None, description="Поиск по заголовку и external_uid (в рамках курса)"),
    is_active: bool | None = Query(None, description="Фильтр по активности"),
    type: str | None = Query(None, alias="type", description="Фильтр по типу материала"),
    order_by: str = Query("order_position", description="Сортировка: order_position, title, created_at"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> MaterialsListResponse:
    """Возвращает материалы курса с фильтрацией и пагинацией. Параметр q — поиск по title и external_uid (ILIKE)."""
    items, total = await materials_service.list_by_course(
        db,
        course_id,
        q=q,
        is_active=is_active,
        type_filter=type,
        order_by=order_by,
        skip=skip,
        limit=limit,
    )
    logger.info("list_course_materials course_id=%s total=%s skip=%s limit=%s", course_id, total, skip, limit)
    return MaterialsListResponse(
        items=[MaterialRead.model_validate(m) for m in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/courses/{course_id}/materials/reorder",
    response_model=MaterialReorderResponse,
    summary="Изменить порядок материалов курса",
)
async def reorder_course_materials(
    course_id: int,
    body: MaterialReorderRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MaterialReorderResponse:
    """Массовое изменение порядка материалов. Триггер БД пересчитает позиции."""
    material_orders = [{"material_id": x.material_id, "order_position": x.order_position} for x in body.material_orders]
    materials = await materials_service.reorder_materials(db, course_id, material_orders)
    logger.info("reorder_materials course_id=%s updated=%s", course_id, len(materials))
    return MaterialReorderResponse(
        updated=len(materials),
        materials=[MaterialOrderRead(id=m.id, order_position=m.order_position or 0) for m in materials],
    )


@router.post(
    "/materials/{material_id}/move",
    response_model=MaterialRead,
    summary="Переместить материал",
)
async def move_material(
    material_id: int,
    body: MaterialMoveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MaterialRead:
    """Переместить материал в другую позицию или в другой курс."""
    material = await materials_service.move_material(
        db,
        material_id,
        body.new_order_position,
        body.course_id,
    )
    logger.info("move_material material_id=%s new_order_position=%s course_id=%s", material_id, body.new_order_position, body.course_id)
    return MaterialRead.model_validate(material)


@router.post(
    "/courses/{course_id}/materials/bulk-update",
    response_model=MaterialBulkUpdateResponse,
    summary="Массовое обновление активности материалов",
)
async def bulk_update_materials(
    course_id: int,
    body: MaterialBulkUpdateRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MaterialBulkUpdateResponse:
    """Обновить is_active для указанных материалов курса."""
    updated = await materials_service.bulk_update_active(db, course_id, body.material_ids, body.is_active)
    logger.info("bulk_update_materials course_id=%s updated=%s is_active=%s", course_id, updated, body.is_active)
    return MaterialBulkUpdateResponse(updated=updated)


@router.post(
    "/materials/{material_id}/copy",
    response_model=MaterialRead,
    status_code=201,
    summary="Копировать материал в другой курс",
)
async def copy_material(
    material_id: int,
    body: MaterialCopyRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MaterialRead:
    """Создать копию материала в целевом курсе."""
    material = await materials_service.copy_material(db, material_id, body.target_course_id, body.order_position)
    logger.info("copy_material material_id=%s target_course_id=%s new_id=%s", material_id, body.target_course_id, material.id)
    return MaterialRead.model_validate(material)


@router.get(
    "/courses/{course_id}/materials/stats",
    summary="Статистика материалов курса",
)
async def get_course_materials_stats(
    course_id: int,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Возвращает total, by_type, active, inactive."""
    stats = await materials_service.get_stats_by_course(db, course_id)
    logger.info("get_course_materials_stats course_id=%s total=%s", course_id, stats.get("total"))
    return stats


@router.post(
    "/materials/upload",
    summary="Загрузить файл для использования в контенте материала",
)
async def upload_material_file(
    file: UploadFile = File(..., description="Файл (PDF, документ, изображение и т.д.)"),
) -> Dict[str, str]:
    """
    Загружает файл на сервер. Возвращает url для подстановки в content материала
    (например, content.sources[].url для pdf/video или content.url для link).

    Важно: загрузка не обновляет поле content материала автоматически.
    Клиент должен выполнить PATCH материала, добавив возвращённый url в content.
    При PATCH передавайте полный объект content, чтобы сохранить существующие
    источники (наш файл и внешняя ссылка могут сосуществовать в content.sources).

    Лимит размера: MAX_ATTACHMENT_SIZE_BYTES (по умолчанию 10 MB).
    Файлы сохраняются в MATERIALS_UPLOAD_DIR (по умолчанию uploads/materials).
    """
    settings.materials_upload_dir.mkdir(parents=True, exist_ok=True)
    original = file.filename or "file"
    safe_name = f"{uuid4().hex}_{original}"
    file_path = settings.materials_upload_dir / safe_name

    total = 0
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(settings.attachment_chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_attachment_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Файл слишком большой. Максимум {settings.max_attachment_size_bytes} байт",
                    )
                f.write(chunk)
    except HTTPException:
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        raise

    url_path = f"/api/v1/materials/files/{safe_name}"
    logger.info("upload_material_file filename=%s size=%s url=%s", original, total, url_path)
    return {"url": url_path, "filename": original}


@router.get(
    "/materials/files/{file_id}",
    summary="Скачать загруженный файл материала",
)
async def download_material_file(file_id: str):
    """
    Отдаёт файл по идентификатору (имя файла, выданное при upload).
    Идентификатор не должен содержать / или ..
    """
    if "/" in file_id or ".." in file_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый file_id")
    file_path = settings.materials_upload_dir / file_id
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    media_type = mimetypes.guess_type(file_id)[0] or "application/octet-stream"
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_id)


@router.post(
    "/materials/import/google-sheets",
    response_model=MaterialsGoogleSheetsImportResponse,
    summary="Импорт материалов из Google Sheets",
)
async def import_materials_from_google_sheets(
    payload: MaterialsGoogleSheetsImportRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MaterialsGoogleSheetsImportResponse:
    """
    Массовый импорт материалов из таблицы. Курс для каждой строки задаётся полем course_uid.
    Upsert по паре (course_id, external_uid).
    """
    try:
        spreadsheet_id = parser_service.extract_spreadsheet_id(payload.spreadsheet_url)
    except DomainError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    sheet_name = payload.sheet_name or "Materials"
    range_name = f"{sheet_name}!A:Z"
    try:
        rows = gsheets_service.read_sheet(spreadsheet_id=spreadsheet_id, range_name=range_name)
    except Exception as e:
        logger.exception("Ошибка чтения Google Sheet: %s", e)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Ошибка при чтении Google Sheet: {e!s}")

    if not rows:
        return MaterialsGoogleSheetsImportResponse(
            imported=0,
            updated=0,
            total_rows=0,
            by_course=[],
            errors=[MaterialsGoogleSheetsImportError(row=0, error="Таблица пуста или не найдена")],
        )

    headers = [str(h).strip() for h in rows[0]]
    column_mapping = parser_service.build_column_mapping_from_headers(headers, payload.column_mapping)

    global_errors: List[MaterialsGoogleSheetsImportError] = []
    parsed_by_row: List[tuple[int, Dict[str, Any]]] = []

    for row_index, row_values in enumerate(rows[1:], start=1):
        row_dict = {}
        for idx, val in enumerate(row_values):
            if idx < len(headers):
                row_dict[headers[idx]] = str(val) if val else ""
        if not any(row_dict.values()):
            continue
        try:
            data = parser_service.parse_material_row(row_dict, column_mapping)
            parsed_by_row.append((row_index, data))
        except DomainError as e:
            global_errors.append(
                MaterialsGoogleSheetsImportError(
                    row=row_index,
                    error=e.detail,
                    course_uid=row_dict.get(column_mapping.get("course_uid", "")),
                    external_uid=row_dict.get(column_mapping.get("external_uid", "")),
                )
            )
        except Exception as e:
            logger.exception("Парсинг строки %d: %s", row_index, e)
            global_errors.append(
                MaterialsGoogleSheetsImportError(
                    row=row_index,
                    error=f"Ошибка парсинга: {e!s}",
                    course_uid=row_dict.get(column_mapping.get("course_uid", "")),
                    external_uid=row_dict.get(column_mapping.get("external_uid", "")),
                )
            )

    unique_course_uids = {data["course_uid"] for _, data in parsed_by_row}
    course_uid_to_id: Dict[str, int] = {}
    for uid in unique_course_uids:
        course = await courses_repo.get_by_keys(db, {"course_uid": uid})
        if course:
            course_uid_to_id[uid] = course.id
        else:
            for row_index, data in parsed_by_row:
                if data["course_uid"] == uid:
                    global_errors.append(
                        MaterialsGoogleSheetsImportError(
                            row=row_index,
                            error=f"Курс с course_uid '{uid}' не найден",
                            course_uid=uid,
                            external_uid=data.get("external_uid"),
                        )
                    )

    parsed_by_row = [(ri, d) for ri, d in parsed_by_row if d["course_uid"] in course_uid_to_id]

    by_course_id: Dict[int, List[tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for row_index, data in parsed_by_row:
        cid = course_uid_to_id[data["course_uid"]]
        by_course_id[cid].append((row_index, data))

    imported_total = 0
    updated_total = 0
    by_course_result: List[MaterialsImportByCourseItem] = []

    for course_uid, course_id in course_uid_to_id.items():
        rows_for_course = by_course_id.get(course_id, [])
        if not rows_for_course:
            continue
        course_errors: List[MaterialsGoogleSheetsImportError] = []
        imported_c = 0
        updated_c = 0

        if not payload.dry_run:
            for row_index, data in rows_for_course:
                course_id_val = course_uid_to_id[data["course_uid"]]
                existing = await materials_repo.get_by_keys(db, {"course_id": course_id_val, "external_uid": data["external_uid"]})
                payload_data = {
                    "course_id": course_id_val,
                    "title": data["title"],
                    "type": data["type"],
                    "content": data["content"],
                    "description": data.get("description"),
                    "caption": data.get("caption"),
                    "order_position": data.get("order_position"),
                    "is_active": data.get("is_active", True),
                    "external_uid": data["external_uid"],
                }
                try:
                    if existing:
                        await materials_repo.update(db, existing, payload_data)
                        updated_c += 1
                        updated_total += 1
                    else:
                        await materials_repo.create(db, payload_data)
                        imported_c += 1
                        imported_total += 1
                except IntegrityError as e:
                    course_errors.append(
                        MaterialsGoogleSheetsImportError(
                            row=row_index,
                            error=str(e),
                            course_uid=course_uid,
                            external_uid=data["external_uid"],
                        )
                    )
                except Exception as e:
                    logger.exception("Upsert материала строка %d: %s", row_index, e)
                    course_errors.append(
                        MaterialsGoogleSheetsImportError(
                            row=row_index,
                            error=str(e),
                            course_uid=course_uid,
                            external_uid=data["external_uid"],
                        )
                    )
        else:
            imported_c = len(rows_for_course)

        by_course_result.append(
            MaterialsImportByCourseItem(
                course_uid=course_uid,
                course_id=course_id,
                imported=imported_c,
                updated=updated_c,
                errors=course_errors,
            )
        )

    if payload.dry_run:
        imported_total = sum(len(rows) for rows in by_course_id.values())
        updated_total = 0

    logger.info(
        "import_materials_from_google_sheets dry_run=%s total_rows=%s imported=%s updated=%s errors_count=%s",
        payload.dry_run,
        len(rows) - 1,
        imported_total,
        updated_total,
        len(global_errors),
    )
    return MaterialsGoogleSheetsImportResponse(
        imported=imported_total,
        updated=updated_total,
        total_rows=len(rows) - 1,
        by_course=by_course_result,
        errors=global_errors,
    )
