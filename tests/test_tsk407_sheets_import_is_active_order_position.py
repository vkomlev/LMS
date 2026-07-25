"""tsk-407: импорт материалов из Google Sheets не должен затирать is_active/order_position.

Тот же класс дефекта, что tsk-378 закрыл для JSON bulk-upsert
(`POST /materials/bulk-upsert`), но в другом, не затронутом той правкой коде:
`POST /materials/import/google-sheets`. Источник данных здесь — не Pydantic-модель
с `model_fields_set`, а `dict`, который строит `MaterialsSheetsParserService.parse_material_row`
из строки таблицы. Правка (tsk-407): `parse_material_row` кладёт ключи
`is_active`/`order_position` в результат только если соответствующая колонка
присутствует в таблице и ячейка не пуста; `materials_extra.py` на UPDATE-ветке
добавляет их в payload условно по наличию ключа, на CREATE-ветке — как раньше,
безусловно.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.api.v1 import materials_extra
from app.core.config import Settings

_settings = Settings()

IMPORT_URL = "/api/v1/materials/import/google-sheets"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


def _rows(headers: list[str], data_rows: list[list[str]]) -> list[list[str]]:
    return [headers, *data_rows]


def _patch_sheet(monkeypatch: pytest.MonkeyPatch, rows: list[list[str]]) -> None:
    def _fake_read_sheet(*, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        return rows

    monkeypatch.setattr(materials_extra.gsheets_service, "read_sheet", _fake_read_sheet)


async def _new_course(db) -> tuple[int, str]:
    course_uid = f"lms:test:tsk407:{uuid.uuid4().hex[:12]}"
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-407', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": "test_tsk407_sheets_import", "uid": course_uid},
        )
    ).first()
    await db.flush()
    return int(row.id), course_uid


async def _material_row(db, course_id: int, external_uid: str) -> tuple[bool, int]:
    row = (
        await db.execute(
            text(
                "SELECT is_active, order_position FROM materials "
                "WHERE course_id = :cid AND external_uid = :uid"
            ),
            {"cid": course_id, "uid": external_uid},
        )
    ).first()
    assert row is not None, f"материал {external_uid} не найден"
    return bool(row.is_active), row.order_position


async def _import(client, monkeypatch, rows: list[list[str]]) -> Any:
    _patch_sheet(monkeypatch, rows)
    resp = await client.post(
        IMPORT_URL,
        params={"api_key": _api_key()},
        json={"spreadsheet_url": "sheet-id", "sheet_name": "Materials", "dry_run": False},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_reissue_without_is_active_column_does_not_reactivate(client, db, monkeypatch):
    course_id, course_uid = await _new_course(db)
    uid = f"tsk407-mat-{uuid.uuid4().hex[:8]}"

    headers = ["course_uid", "external_uid", "title", "type", "url", "is_active"]
    await _import(
        client,
        monkeypatch,
        _rows(headers, [[course_uid, uid, "tsk-407 материал", "link", "https://example.com/tsk407", "true"]]),
    )
    assert (await _material_row(db, course_id, uid))[0] is True

    # Методист выключает материал вручную (эмулируем прямой правкой строки).
    await db.execute(
        text("UPDATE materials SET is_active = false WHERE course_id = :cid AND external_uid = :uid"),
        {"cid": course_id, "uid": uid},
    )
    await db.flush()
    assert (await _material_row(db, course_id, uid))[0] is False

    # Переиздание тем же материалом, но в таблице БЕЗ колонки is_active.
    headers_no_active = ["course_uid", "external_uid", "title", "type", "url"]
    result = await _import(
        client,
        monkeypatch,
        _rows(headers_no_active, [[course_uid, uid, "tsk-407 материал", "link", "https://example.com/tsk407"]]),
    )
    assert result["updated"] == 1, result

    assert (await _material_row(db, course_id, uid))[0] is False, (
        "переиздание без колонки is_active реактивировало выключенный материал"
    )


@pytest.mark.asyncio
async def test_reissue_without_order_position_column_does_not_move_to_end(client, db, monkeypatch):
    course_id, course_uid = await _new_course(db)
    uid_a = f"tsk407-mat-{uuid.uuid4().hex[:8]}"
    uid_b = f"tsk407-mat-{uuid.uuid4().hex[:8]}"
    uid_c = f"tsk407-mat-{uuid.uuid4().hex[:8]}"

    headers = ["course_uid", "external_uid", "title", "type", "url", "order_position"]
    await _import(
        client,
        monkeypatch,
        _rows(
            headers,
            [
                [course_uid, uid_a, "A", "link", "https://example.com/a", "1"],
                [course_uid, uid_b, "B", "link", "https://example.com/b", "2"],
                [course_uid, uid_c, "C", "link", "https://example.com/c", "3"],
            ],
        ),
    )
    assert (await _material_row(db, course_id, uid_b))[1] == 2

    # Переиздание среднего материала таблицей БЕЗ колонки order_position.
    headers_no_pos = ["course_uid", "external_uid", "title", "type", "url"]
    result = await _import(
        client,
        monkeypatch,
        _rows(headers_no_pos, [[course_uid, uid_b, "B", "link", "https://example.com/b"]]),
    )
    assert result["updated"] == 1, result

    position = (await _material_row(db, course_id, uid_b))[1]
    assert position == 2, "переиздание без order_position утащило материал в конец курса"


@pytest.mark.asyncio
async def test_explicit_values_in_sheet_still_applied(client, db, monkeypatch):
    course_id, course_uid = await _new_course(db)
    uid = f"tsk407-mat-{uuid.uuid4().hex[:8]}"

    headers = ["course_uid", "external_uid", "title", "type", "url", "is_active", "order_position"]
    await _import(
        client,
        monkeypatch,
        _rows(headers, [[course_uid, uid, "tsk-407", "link", "https://example.com/tsk407", "true", "1"]]),
    )
    assert (await _material_row(db, course_id, uid)) == (True, 1)

    # Явные значения в таблице по-прежнему применяются на переиздании.
    result = await _import(
        client,
        monkeypatch,
        _rows(headers, [[course_uid, uid, "tsk-407", "link", "https://example.com/tsk407", "false", "1"]]),
    )
    assert result["updated"] == 1, result
    assert (await _material_row(db, course_id, uid))[0] is False


@pytest.mark.asyncio
async def test_create_without_columns_defaults_to_active_and_trigger_position(client, db, monkeypatch):
    course_id, course_uid = await _new_course(db)
    uid = f"tsk407-mat-{uuid.uuid4().hex[:8]}"

    headers = ["course_uid", "external_uid", "title", "type", "url"]
    result = await _import(
        client,
        monkeypatch,
        _rows(headers, [[course_uid, uid, "tsk-407 новый", "link", "https://example.com/new"]]),
    )
    assert result["imported"] == 1, result

    is_active, order_position = await _material_row(db, course_id, uid)
    assert is_active is True
    assert order_position is not None and order_position >= 1
