"""tsk-433: ручная правка материала переживает переиздание из источника.

Кабинет методиста получает правку материалов, но `bulk_upsert` перезаписывал
`title`/`content`/`description`/`caption` **безусловно** — условная запись
(tsk-377/378/407) была сделана только для `is_active`, `order_position` и
`requirement_level`. Без защиты методист поправил бы текст, а ближайшее
переиздание молча вернуло бы старый.

Защита — `materials.content_provenance` (jsonb):
    {"source": "manual_web", "edited_at": ..., "edited_by": ..., "fields": [...]}
Поля из `fields` импорт не трогает; всё остальное обновляется как раньше.

Тесты бьют по РЕАЛЬНОМУ HTTP-пути импорта (`POST /materials/bulk-upsert`), а не
по сервису напрямую: дефект живёт на стыке схемы и сервиса, и вызов сервиса
мимо эндпоинта его не воспроизводит (тот же принцип, что в tsk-378).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.config import Settings

_settings = Settings()

MATERIALS_URL = "/api/v1/materials/bulk-upsert"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-433', 'self_guided', false, :uid) RETURNING id"
            ),
            {
                "t": "test_tsk433_manual_edit",
                "uid": f"lms:test:tsk433:{uuid.uuid4().hex[:12]}",
            },
        )
    ).first()
    await db.flush()
    return int(row.id)


def _source_payload(
    external_uid: str, course_id: int, *, title: str, text_body: str
) -> dict[str, Any]:
    """Payload переиздания из источника — как его кладёт ContentBackbone."""
    return {
        "course_id": course_id,
        "external_uid": external_uid,
        "title": title,
        "type": "text",
        "content": {"text": text_body, "format": "html"},
    }


async def _post(client, items: list[dict[str, Any]]):
    return await client.post(
        MATERIALS_URL, params={"api_key": _api_key()}, json={"items": items}
    )


async def _row(db, course_id: int, external_uid: str):
    row = (
        await db.execute(
            text(
                "SELECT title, content, content_provenance FROM materials "
                "WHERE course_id = :cid AND external_uid = :uid"
            ),
            {"cid": course_id, "uid": external_uid},
        )
    ).first()
    assert row is not None, f"материал {external_uid} не найден"
    return row


async def _mark_manual(db, course_id: int, external_uid: str, fields: list[str]) -> None:
    """Пометить поля как поправленные вручную (то, что делает PATCH из кабинета)."""
    await db.execute(
        text(
            "UPDATE materials SET content_provenance = CAST(:prov AS jsonb) "
            "WHERE course_id = :cid AND external_uid = :uid"
        ),
        {
            "prov": json.dumps(
                {
                    "source": "manual_web",
                    "edited_at": "2026-07-29T18:00:00+00:00",
                    "edited_by": 2,
                    "fields": fields,
                }
            ),
            "cid": course_id,
            "uid": external_uid,
        },
    )
    await db.flush()


@pytest.mark.asyncio
async def test_manually_edited_title_survives_reimport(client, db):
    """Правленый вручную заголовок переиздание не возвращает к исходному."""
    course_id = await _new_course(db)
    uid = f"tsk433-{uuid.uuid4().hex[:8]}"

    r = await _post(client, [_source_payload(uid, course_id, title="Из источника", text_body="<p>раз</p>")])
    assert r.status_code == 200, r.text

    # методист правит заголовок через кабинет
    await db.execute(
        text("UPDATE materials SET title = :t WHERE course_id = :cid AND external_uid = :uid"),
        {"t": "Поправлено методистом", "cid": course_id, "uid": uid},
    )
    await _mark_manual(db, course_id, uid, ["title"])

    # источник переиздаёт с прежним заголовком
    r = await _post(client, [_source_payload(uid, course_id, title="Из источника", text_body="<p>раз</p>")])
    assert r.status_code == 200, r.text

    row = await _row(db, course_id, uid)
    assert row.title == "Поправлено методистом", "правка заголовка затёрта импортом"


@pytest.mark.asyncio
async def test_unprotected_field_still_updated(client, db):
    """Поля, которых методист не касался, импорт обновляет как раньше."""
    course_id = await _new_course(db)
    uid = f"tsk433-{uuid.uuid4().hex[:8]}"

    await _post(client, [_source_payload(uid, course_id, title="Из источника", text_body="<p>старое</p>")])
    await db.execute(
        text("UPDATE materials SET title = :t WHERE course_id = :cid AND external_uid = :uid"),
        {"t": "Поправлено методистом", "cid": course_id, "uid": uid},
    )
    await _mark_manual(db, course_id, uid, ["title"])  # защищён только заголовок

    r = await _post(
        client, [_source_payload(uid, course_id, title="Из источника", text_body="<p>новое</p>")]
    )
    assert r.status_code == 200, r.text

    row = await _row(db, course_id, uid)
    assert row.title == "Поправлено методистом", "защищённый заголовок затёрт"
    assert "новое" in json.dumps(row.content, ensure_ascii=False), (
        "незащищённое содержимое обязано обновиться из источника"
    )


@pytest.mark.asyncio
async def test_manually_edited_content_survives_reimport(client, db):
    """Правленое вручную содержимое переиздание не возвращает."""
    course_id = await _new_course(db)
    uid = f"tsk433-{uuid.uuid4().hex[:8]}"

    await _post(client, [_source_payload(uid, course_id, title="Тема", text_body="<p>из источника</p>")])
    await db.execute(
        text(
            "UPDATE materials SET content = CAST(:c AS jsonb) "
            "WHERE course_id = :cid AND external_uid = :uid"
        ),
        {
            "c": json.dumps({"text": "<p>переписано методистом</p>", "format": "html"}),
            "cid": course_id,
            "uid": uid,
        },
    )
    await _mark_manual(db, course_id, uid, ["content"])

    await _post(client, [_source_payload(uid, course_id, title="Тема", text_body="<p>из источника</p>")])

    row = await _row(db, course_id, uid)
    assert "переписано методистом" in json.dumps(row.content, ensure_ascii=False), (
        "правка содержимого затёрта импортом"
    )


@pytest.mark.asyncio
async def test_material_without_provenance_unchanged_behaviour(client, db):
    """Без пометки поведение ровно прежнее — регресс к tsk-378/407."""
    course_id = await _new_course(db)
    uid = f"tsk433-{uuid.uuid4().hex[:8]}"

    await _post(client, [_source_payload(uid, course_id, title="Было", text_body="<p>было</p>")])
    r = await _post(client, [_source_payload(uid, course_id, title="Стало", text_body="<p>стало</p>")])
    assert r.status_code == 200, r.text

    row = await _row(db, course_id, uid)
    assert row.title == "Стало", "без пометки импорт обязан обновлять заголовок"
    assert "стало" in json.dumps(row.content, ensure_ascii=False)
    assert row.content_provenance is None


@pytest.mark.asyncio
async def test_broken_provenance_does_not_block_import(client, db):
    """Битый провенанс не превращается в «защитить всё» — импорт работает.

    Молчаливая блокировка по мусорному значению была бы хуже потери правки:
    источник перестал бы обновлять материал, и никто бы не понял почему.
    """
    course_id = await _new_course(db)
    uid = f"tsk433-{uuid.uuid4().hex[:8]}"

    await _post(client, [_source_payload(uid, course_id, title="Было", text_body="<p>было</p>")])
    await db.execute(
        text(
            "UPDATE materials SET content_provenance = CAST(:prov AS jsonb) "
            "WHERE course_id = :cid AND external_uid = :uid"
        ),
        {"prov": json.dumps({"source": "manual_web"}), "cid": course_id, "uid": uid},
    )
    await db.flush()

    await _post(client, [_source_payload(uid, course_id, title="Стало", text_body="<p>стало</p>")])
    row = await _row(db, course_id, uid)
    assert row.title == "Стало", "битый провенанс не должен блокировать импорт"


@pytest.mark.asyncio
async def test_provenance_cannot_protect_arbitrary_column(client, db):
    """Провенанс защищает только поля из белого списка, а не любую колонку."""
    course_id = await _new_course(db)
    uid = f"tsk433-{uuid.uuid4().hex[:8]}"

    await _post(client, [_source_payload(uid, course_id, title="Было", text_body="<p>было</p>")])
    await _mark_manual(db, course_id, uid, ["type", "external_uid", "course_id"])

    await _post(client, [_source_payload(uid, course_id, title="Стало", text_body="<p>стало</p>")])
    row = await _row(db, course_id, uid)
    assert row.title == "Стало", (
        "поля вне белого списка защищать нельзя — иначе провенанс станет "
        "способом заморозить произвольную колонку"
    )
