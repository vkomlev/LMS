"""tsk-377: переиздание через bulk-upsert не сбрасывает `requirement_level`.

Дефект: `TaskUpsertItem.requirement_level` / `MaterialsBulkUpsertItem.requirement_level`
имеют дефолт `required`, а сервис получал уже материализованный словарь — то есть
физически не отличал «поле не передали» от «передали required». Ни один конвейер
поля не шлёт (`TaskPayload` / `MaterialPayload` в
`D:\\Work\\ContentBackbone\\monolith\\lms_client\\contracts.py`, парсеры Google
Sheets), поэтому любое переиздание задания или материала молча возвращало уровень
методиста в `required` — так эродировала простановка tsk-112.

Проверка идёт ЧЕРЕЗ ЭНДПОИНТ, а не через сервис: дефект жил именно в
материализации дефолта схемой, вызов сервиса напрямую его не воспроизводит.
Payload переиздания — ровно те ключи, которые кладёт ContentBackbone
(`task_payload_to_dict` / `material_payload_to_dict`), а не синтетический полный.

Отдельно закреплено текущее поведение `is_active` (тот же дефолт-паттерн):
переиздание по-прежнему включает элемент. Это не чинится здесь намеренно —
тест сторожит, что правка уровня не изменила активность молча (tsk-377,
раздел «Риски» ревью-артефакта).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.config import Settings

_settings = Settings()

TASKS_URL = "/api/v1/tasks/bulk-upsert"
MATERIALS_URL = "/api/v1/materials/bulk-upsert"

EASY = 2  # не HARD: маршрутизация tsk-347 в блок сложных здесь не участвует


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _new_course(db) -> int:
    """Курс без подкурса сложных — чистая ветка bulk-upsert."""
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-377', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": "test_tsk377_requirement_level", "uid": f"lms:test:tsk377:{uuid.uuid4().hex[:12]}"},
        )
    ).first()
    await db.flush()
    return int(row.id)


def _cb_task_payload(external_uid: str, course_id: int) -> dict[str, Any]:
    """Ровно то, что кладёт ContentBackbone: уровня и активности в ключах нет."""
    return {
        "external_uid": external_uid,
        "course_id": course_id,
        "task_content": {"type": "SA_COM", "stem": "tsk-377", "accepted_answers": ["1"]},
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "difficulty_id": EASY,
        "max_score": 1,
    }


def _cb_material_payload(external_uid: str, course_id: int) -> dict[str, Any]:
    """Ровно то, что кладёт ContentBackbone (material_payload_to_dict)."""
    return {
        "course_id": course_id,
        "external_uid": external_uid,
        "title": "tsk-377 материал",
        "type": "link",
        "content": {"url": "https://example.com/tsk377"},
    }


async def _post_tasks(client, items: list[dict[str, Any]]):
    return await client.post(
        TASKS_URL, params={"api_key": _api_key()}, json={"items": items}
    )


async def _post_materials(client, items: list[dict[str, Any]]):
    return await client.post(
        MATERIALS_URL, params={"api_key": _api_key()}, json={"items": items}
    )


async def _task_row(db, external_uid: str) -> tuple[str, bool]:
    row = (
        await db.execute(
            text("SELECT requirement_level, is_active FROM tasks WHERE external_uid = :uid"),
            {"uid": external_uid},
        )
    ).first()
    assert row is not None, f"задание {external_uid} не найдено"
    return row.requirement_level, bool(row.is_active)


async def _material_row(db, course_id: int, external_uid: str) -> tuple[str, bool]:
    row = (
        await db.execute(
            text(
                "SELECT requirement_level, is_active FROM materials "
                "WHERE course_id = :cid AND external_uid = :uid"
            ),
            {"cid": course_id, "uid": external_uid},
        )
    ).first()
    assert row is not None, f"материал {external_uid} не найден"
    return row.requirement_level, bool(row.is_active)


# --------------------------------------------------------------------------
# Задания
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_reissue_keeps_recommended(client, db):
    """Главный регресс: `recommended` переживает переиздание payload'ом ContentBackbone."""
    course_id = await _new_course(db)
    uid = f"tsk377-task-{uuid.uuid4().hex[:8]}"

    resp = await _post_tasks(client, [{**_cb_task_payload(uid, course_id), "requirement_level": "recommended"}])
    assert resp.status_code == 200, resp.text
    assert (await _task_row(db, uid))[0] == "recommended"

    # Доливка/round-trip гигиены стемов: уровня в payload нет.
    resp = await _post_tasks(client, [_cb_task_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["action"] == "updated"

    level, _ = await _task_row(db, uid)
    assert level == "recommended", "переиздание вернуло задание в основной поток ученика"


@pytest.mark.asyncio
async def test_task_explicit_level_still_applied(client, db):
    """Явно переданный уровень по-прежнему перезаписывает существующий."""
    course_id = await _new_course(db)
    uid = f"tsk377-task-{uuid.uuid4().hex[:8]}"

    await _post_tasks(client, [{**_cb_task_payload(uid, course_id), "requirement_level": "recommended"}])
    resp = await _post_tasks(client, [{**_cb_task_payload(uid, course_id), "requirement_level": "skippable"}])
    assert resp.status_code == 200, resp.text

    assert (await _task_row(db, uid))[0] == "skippable"


@pytest.mark.asyncio
async def test_task_create_defaults_to_required(client, db):
    """CREATE без уровня — прежний дефолт `required` (регресс)."""
    course_id = await _new_course(db)
    uid = f"tsk377-task-{uuid.uuid4().hex[:8]}"

    resp = await _post_tasks(client, [_cb_task_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["action"] == "created"

    assert (await _task_row(db, uid))[0] == "required"


@pytest.mark.asyncio
async def test_task_reissue_still_activates(client, db):
    """Сторож: активность правка НЕ трогала — переиздание по-прежнему включает задание.

    Тот же дефолт-паттерн, что был у уровня; чинить его здесь не входит в
    охват tsk-377. Тест падает, если поведение изменилось молча.
    """
    course_id = await _new_course(db)
    uid = f"tsk377-task-{uuid.uuid4().hex[:8]}"

    await _post_tasks(client, [_cb_task_payload(uid, course_id)])
    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE external_uid = :uid"), {"uid": uid}
    )
    assert (await _task_row(db, uid))[1] is False

    await _post_tasks(client, [_cb_task_payload(uid, course_id)])
    assert (await _task_row(db, uid))[1] is True


@pytest.mark.asyncio
async def test_task_leaving_hard_block_returns_to_required(client, db):
    """Стык с tsk-347: выход из блока сложных снимает и уровень блока.

    `recommended` внутри подкурса сложных ставит классификация, а не методист.
    Если задание переклассифицировали в более лёгкое и оно уехало обратно в
    номерной курс, «не передано = не менять» применять нельзя: задание сидело бы
    в основном курсе, но вне зачёта и вне next-item.
    """
    src_id = await _new_course(db)
    twin_uid = f"lms:tsk347:hard:{src_id}"
    await db.execute(
        text(
            "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
            "VALUES ('test_tsk377_requirement_level', 'tsk-377', 'self_guided', false, :uid)"
        ),
        {"uid": twin_uid},
    )
    await db.flush()
    uid = f"tsk377-task-{uuid.uuid4().hex[:8]}"

    hard = {**_cb_task_payload(uid, src_id), "difficulty_id": 4}
    assert (await _post_tasks(client, [hard])).status_code == 200
    assert (await _task_row(db, uid))[0] == "recommended"

    # Переклассификация в лёгкое: задание возвращается в номерной курс.
    assert (await _post_tasks(client, [_cb_task_payload(uid, src_id)])).status_code == 200

    level, _ = await _task_row(db, uid)
    assert level == "required"


# --------------------------------------------------------------------------
# Материалы
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_reissue_keeps_recommended(client, db):
    """Материал с `recommended` переживает переиздание payload'ом ContentBackbone."""
    course_id = await _new_course(db)
    uid = f"tsk377-mat-{uuid.uuid4().hex[:8]}"

    resp = await _post_materials(
        client, [{**_cb_material_payload(uid, course_id), "requirement_level": "recommended"}]
    )
    assert resp.status_code == 200, resp.text
    assert (await _material_row(db, course_id, uid))[0] == "recommended"

    resp = await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text

    level, _ = await _material_row(db, course_id, uid)
    assert level == "recommended", "переиздание сбросило уровень материала"


@pytest.mark.asyncio
async def test_material_reissue_without_level_is_unchanged(client, db):
    """Отсутствие уровня в payload не считается изменением: статус `unchanged`.

    Иначе сервис сообщал бы `updated` там, где ничего не менял.
    """
    course_id = await _new_course(db)
    uid = f"tsk377-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(
        client, [{**_cb_material_payload(uid, course_id), "requirement_level": "recommended"}]
    )
    resp = await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "unchanged", resp.text


@pytest.mark.asyncio
async def test_material_explicit_level_still_applied(client, db):
    """Явно переданный уровень материала по-прежнему применяется."""
    course_id = await _new_course(db)
    uid = f"tsk377-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(
        client, [{**_cb_material_payload(uid, course_id), "requirement_level": "recommended"}]
    )
    resp = await _post_materials(
        client, [{**_cb_material_payload(uid, course_id), "requirement_level": "skippable"}]
    )
    assert resp.status_code == 200, resp.text

    assert (await _material_row(db, course_id, uid))[0] == "skippable"


@pytest.mark.asyncio
async def test_material_create_defaults_to_required(client, db):
    """CREATE материала без уровня — прежний дефолт `required` (регресс)."""
    course_id = await _new_course(db)
    uid = f"tsk377-mat-{uuid.uuid4().hex[:8]}"

    resp = await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "created", resp.text

    assert (await _material_row(db, course_id, uid))[0] == "required"


@pytest.mark.asyncio
async def test_material_reissue_still_activates(client, db):
    """Сторож активности материала — зеркало теста для заданий."""
    course_id = await _new_course(db)
    uid = f"tsk377-mat-{uuid.uuid4().hex[:8]}"

    await _post_materials(client, [_cb_material_payload(uid, course_id)])
    await db.execute(
        text("UPDATE materials SET is_active = false WHERE course_id = :cid AND external_uid = :uid"),
        {"cid": course_id, "uid": uid},
    )
    assert (await _material_row(db, course_id, uid))[1] is False

    await _post_materials(client, [_cb_material_payload(uid, course_id)])
    assert (await _material_row(db, course_id, uid))[1] is True
