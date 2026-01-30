# app/api/v1/materials_extra.py
"""
Расширенные эндпойнты для материалов: список по курсу, reorder, move, bulk-update, copy, stats, импорт из Google Sheets.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Body, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
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


@router.get(
    "/courses/{course_id}/materials",
    response_model=MaterialsListResponse,
    summary="Список материалов курса",
)
async def list_course_materials(
    course_id: int,
    is_active: bool | None = Query(None, description="Фильтр по активности"),
    type: str | None = Query(None, alias="type", description="Фильтр по типу материала"),
    order_by: str = Query("order_position", description="Сортировка: order_position, title, created_at"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> MaterialsListResponse:
    """Возвращает материалы курса с фильтрацией и пагинацией."""
    items, total = await materials_service.list_by_course(
        db,
        course_id,
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
